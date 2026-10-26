"""The set-up flow as a stranger meets it, read without Home Assistant.

config_flow.py imports Home Assistant and voluptuous at the top, so the
ordinary suite reads it as source; test_homeassistant_config_flow.py drives the
same flow under a real core. What is pinned here holds in CI as well: the
first screen sweeps nothing (R70), and the texts that say what is switched on
from the start are there, in all three files, wherever a new installation
passes.
"""

from __future__ import annotations

import json
import re

import pytest

from conftest import COMPONENT, ROOT

FLOW = (COMPONENT / "config_flow.py").read_text(encoding="utf-8")
FILES = ("strings.json", "translations/en.json", "translations/sv.json")


def _texts(name: str) -> dict:
    return json.loads((COMPONENT / name).read_text(encoding="utf-8"))


def _method(name: str) -> str:
    """The source of one method of the flow, up to the next definition."""
    body = FLOW.split(f"async def {name}(")[1]
    return re.split(r"\n    (?:async )?def |\n    @", body)[0]


# ------------------------------------------------- nothing before a choice (R70)


def test_the_first_step_is_a_menu_that_sweeps_nothing():
    first = _method("async_step_user")
    assert "async_show_menu" in first
    assert "async_discover" not in first and "async_home_assistant_networks" not in first
    # The sweep lives in the step the menu's search leads to, and nowhere else.
    assert FLOW.count("await async_discover(") == 1
    assert "await async_discover(" in _method("async_step_scan")


@pytest.mark.parametrize("name", FILES)
def test_the_menu_offers_search_and_address(name):
    menu = _texts(name)["config"]["step"]["user"]["menu_options"]
    assert set(menu) == {"scan", "manual"}
    assert re.search(r'menu_options=\[STEP_SCAN, STEP_MANUAL\]', FLOW)
    assert 'STEP_SCAN = "scan"' in FLOW and 'STEP_MANUAL = "manual"' in FLOW


#: What is on from the start, in each language: control, the walk to System
#: information, the statistics and the release check, and where it is changed.
DEFAULTS = {
    "en": ("control", "System information", "statistics", "releases", "Configure", "{privacy_url}"),
    "sv": ("styrning", "Systeminformation", "statistiken", "versioner", "Konfigurera",
           "{privacy_url}"),
}


@pytest.mark.parametrize("name", FILES)
def test_every_way_in_says_what_is_on_from_the_start(name):
    """The menu for a flow somebody started, the confirmation for a discovered one,
    and the page step that most installations pass, all carry the same sentence."""
    language = "sv" if name.endswith("sv.json") else "en"
    steps = _texts(name)["config"]["step"]
    sentences = set()
    for step in ("user", "slow", "confirm"):
        text = steps[step]["description"]
        last = text.split("\n\n")[-1]
        for word in DEFAULTS[language]:
            assert word in last, f"{word!r} saknas i {step} i {name}"
        sentences.add(last)
    assert len(sentences) == 1, f"meningen ska vara densamma i alla tre stegen i {name}"


def test_the_privacy_page_travels_as_a_placeholder_to_every_step_that_names_it():
    # hassfest refuses a URL in strings.json, so the address must be handed in.
    assert "**STATS_PLACEHOLDERS" in _method("async_step_user")
    assert "**STATS_PLACEHOLDERS" in _method("async_step_confirm")
    slow = FLOW.split("async def async_step_slow(")[1].split("async def async_step_dhcp(")[0]
    assert "**STATS_PLACEHOLDERS" in slow


#: What the sentence on the two open ports says, in each language (R71): both
#: ports, no login, only Home Assistant, never the internet, and the link.
EXPOSURE = {
    "en": ("port 80", "port 502", "no login", "only Home Assistant", "internet", "{exposure_url}"),
    "sv": ("port 80", "port 502", "saknar inloggning", "bara Home Assistant", "internet",
           "{exposure_url}"),
}


@pytest.mark.parametrize("name", FILES)
def test_the_first_step_and_the_confirmation_say_the_ports_have_no_login(name):
    """The confirmation is all a unit adopted from Home Assistant's discovery
    card ever shows, and the menu is where a search or a typed address starts:
    both carry the same paragraph, just before the one on what is switched on."""
    language = "sv" if name.endswith("sv.json") else "en"
    steps = _texts(name)["config"]["step"]
    paragraphs = set()
    for step in ("user", "confirm"):
        paragraph = steps[step]["description"].split("\n\n")[-2]
        for word in EXPOSURE[language]:
            assert word in paragraph, f"{word!r} saknas i {step} i {name}"
        paragraphs.add(paragraph)
    assert len(paragraphs) == 1, f"stycket ska vara detsamma i båda stegen i {name}"


def test_the_readme_section_travels_as_a_placeholder_to_both_steps():
    assert "**EXPOSURE_PLACEHOLDERS" in _method("async_step_user")
    assert "**EXPOSURE_PLACEHOLDERS" in _method("async_step_confirm")
    assert re.search(
        r'"exposure_url": "https://github\.com/beolink/HA-CTC-Local-Easyconf#network-and-exposure"',
        FLOW,
    )
    assert "\n## Network and exposure\n" in (ROOT / "README.md").read_text(encoding="utf-8")


@pytest.mark.parametrize("name", FILES)
def test_the_address_form_no_longer_says_nothing_was_found(name):
    """The form is now a choice of its own; an empty search says so as an error."""
    config = _texts(name)["config"]
    manual = config["step"]["manual"]["description"]
    assert "No CTC was found" not in manual and "Ingen CTC hittades" not in manual
    assert config["error"]["nothing_found"]
    assert '"nothing_found"' in _method("async_step_scan")


def test_the_swedish_flow_texts_use_no_dash_as_punctuation():
    def strings(node):
        if isinstance(node, dict):
            for value in node.values():
                yield from strings(value)
        elif isinstance(node, str):
            yield node

    for text in strings(_texts("translations/sv.json")["config"]):
        assert "–" not in text and "—" not in text and " - " not in text, text


# ------------------------------------------------ the flow for a stranger (R16)


@pytest.mark.parametrize("name", FILES)
def test_a_discovered_unit_is_named_on_its_card(name):
    assert _texts(name)["config"]["flow_title"] == "{name}"
    assert 'self.context["title_placeholders"] = {"name": display.label}' in FLOW


def test_the_list_names_the_model_first_and_carries_no_dash(discovery):
    found = discovery.DiscoveredDisplay(host="192.168.1.55", settings_name="settings_ezi2xx.bin")
    assert found.label == "EcoZenith i255 (192.168.1.55)"
    assert "—" not in found.label and "–" not in found.label


def test_no_label_in_the_flow_is_written_in_one_language():
    # The two that were: the list's way to the address form, and the count
    # behind every page in the tick boxes.
    assert "Ange" not in FLOW and "värden" not in FLOW
    assert "translation_key=CONF_PICKED" in FLOW
    # The two tick box lists and the live page's select name the pages alike;
    # the select's "none" is named by the strings through its translation key.
    assert FLOW.count("label=page_label(page)") == 3, (
        "kryssrutelistorna och valet av direktläst sida använder samma etikett"
    )
    assert "translation_key=CONF_LIVE_PAGE" in FLOW
    assert "label=LIVE_PAGE_NONE" in FLOW
    assert 'return f"{page.title} ({len(page.values)})"' in FLOW


@pytest.mark.parametrize("name", FILES)
def test_the_way_to_the_address_form_is_labelled_in_every_language(name):
    assert _texts(name)["selector"]["picked"]["options"]["manual"]


@pytest.mark.parametrize(
    ("name", "menus"),
    [
        ("strings.json", ("Installer, Define, Remote control", "Modbus TCP")),
        ("translations/en.json", ("Installer, Define, Remote control", "Modbus TCP")),
        ("translations/sv.json", ("Avancerat, Definiera, Fjärrstyrning", "Modbus TCP")),
    ],
)
def test_modbus_that_does_not_answer_has_a_step_of_its_own(name, menus):
    steps = _texts(name)["config"]["step"]
    failed = steps["modbus_failed"]
    assert set(failed["data"]) == set(steps["manual"]["data"]), "samma fält som adressformuläret"
    for words in menus:
        assert words in failed["description"]
    assert "{host}" in failed["description"]
    assert 'STEP_MODBUS_FAILED = "modbus_failed"' in FLOW
    # Where a Modbus failure goes is decided once the cause has a name (L12).
    failed_step = _method("_async_modbus_failed")
    assert "STEP_MODBUS_FAILED" in failed_step and '"manual"' not in failed_step
    assert "return await self._async_modbus_failed()" in _method("async_step_connect")


@pytest.mark.parametrize(
    ("name", "words"),
    [
        ("strings.json", ("already being added", "Discovered")),
        ("translations/en.json", ("already being added", "Discovered")),
        ("translations/sv.json", ("redan på att läggas till", "Upptäckta")),
    ],
)
def test_a_unit_already_being_added_has_a_text_of_its_own(name, words):
    """async_set_unique_id aborts with already_in_progress while another flow has the unit.

    The commonest way there: the display's DHCP card waits under Discovered,
    and somebody adds the integration by hand and picks or types the same
    address. Without the key the form showed the bare key.
    """
    text = _texts(name)["config"]["abort"]["already_in_progress"]
    for word in words:
        assert word in text


def _all_strings(node):
    if isinstance(node, dict):
        for value in node.values():
            yield from _all_strings(value)
    elif isinstance(node, str):
        yield node


@pytest.mark.parametrize(
    ("name", "menu", "other"),
    [
        ("strings.json", "Installer, ", "Advanced, "),
        ("translations/en.json", "Installer, ", "Advanced, "),
        ("translations/sv.json", "Avancerat, ", "Installatör"),
    ],
)
def test_the_installer_menu_has_one_name_per_language_in_the_flow(name, menu, other):
    """A Swedish panel says Avancerat and an English one Installer, and the steps
    that send somebody to the panel all say the same (F5.4). "Installatör" was
    never on a Swedish panel; it came with the step for a silent Modbus."""
    config = _texts(name)["config"]
    texts = list(_all_strings(config))
    assert not [text for text in texts if other in text], f"{other!r} står kvar i {name}"
    for text in (
        config["step"]["manual"]["description"],
        config["step"]["modbus_failed"]["description"],
        config["error"]["not_a_ctc"],
    ):
        assert menu in text


def test_the_readme_setup_names_the_installer_menu_the_same_way():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    first = " ".join(readme.split("## Setup")[1].split("\n2. ")[0].split())
    assert "Installer, Define, Communication, **Web** = Yes" in first
    assert "Advanced, Define" not in first


def test_addresses_already_set_up_are_left_out_of_the_list():
    scan = _method("async_step_scan")
    assert "self._configured_hosts()" in scan
    assert "display.host not in configured" in scan


@pytest.mark.parametrize(
    ("name", "words"),
    [
        ("strings.json", "already set up"),
        ("translations/en.json", "already set up"),
        ("translations/sv.json", "redan är tillagda"),
    ],
)
def test_a_search_that_found_only_units_set_up_says_so_and_not_that_it_found_none(name, words):
    """Leaving out what is set up could empty the list, and the address form
    then said the search had found no CTC display at all (F5.8)."""
    errors = _texts(name)["config"]["error"]
    assert words in errors["only_configured"]
    assert errors["only_configured"] != errors["nothing_found"]
    scan = _method("async_step_scan")
    assert 'error = "only_configured" if found else "nothing_found"' in scan
    assert 'errors={"base": error}' in scan


def _keys(node, prefix=""):
    if isinstance(node, dict):
        for key, value in node.items():
            yield from _keys(value, f"{prefix}/{key}")
    else:
        yield prefix


def test_the_three_files_carry_the_same_keys():
    english = _texts("strings.json")
    assert english == _texts("translations/en.json")
    assert sorted(_keys(english)) == sorted(_keys(_texts("translations/sv.json")))
