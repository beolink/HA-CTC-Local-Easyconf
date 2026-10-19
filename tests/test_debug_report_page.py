"""The debug report's switch on the CTC page: a tick box, last on the controls tab.

Rapportera alla värden till utvecklaren, Report all values to the developer:
the owner asked for a box on the integration's own page, beside the option
under Configure, and the two are one setting (switch.py). It stands under a
heading of its own, Felsökning (Troubleshooting), last on the controls tab,
since it does nothing to the house, and it is there whether control is on or
off; on All values it is a row under the same heading and nowhere under
Other. The section card offers it as troubleshooting. The controls card draws
a switch as a tick box named by the row, sends switch.turn_on or turn_off at
once and follows the state; a refusal puts back what the state says. Checked
from the two houses' pumps (tests/fixtures/pumps.json) with the switch added,
and the card in node on the stub DOM.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
import shutil
import subprocess

import pytest

from conftest import load

NEW_HA = (2026, 9)
TESTS = Path(__file__).resolve().parent
CARD = TESTS.parent / "custom_components" / "ctc_ecozenith" / "www" / "ctc-ecozenith-card.js"
PAGE = TESTS / "card_page.js"
SWITCH = "switch.ctc_ecozenith_i255_rapportera_alla_varden_till_utvecklaren"
NAMES = {
    "sv": "Rapportera alla värden till utvecklaren",
    "en": "Report all values to the developer",
}


def _with_switch(pump, lang="sv"):
    """The pump as dashboard.py would hand it over with the debug report's switch."""
    pump = copy.deepcopy(pump)
    pump["entities"]["debug_report"] = SWITCH
    pump["names"]["debug_report"] = NAMES[lang]
    pump["language"] = lang
    return pump


def _headings(sections):
    return [section["cards"][0]["heading"] for section in sections]


@pytest.mark.parametrize("house", ["vsh", "pt"])
@pytest.mark.parametrize("lang", ["sv", "en"])
def test_the_switch_is_a_tick_box_last_on_the_controls_tab(dashboard_views, pumps, house, lang):
    pump = _with_switch(pumps[house], lang)
    text = dashboard_views.TEXT[lang]
    sections = dashboard_views.controls_sections(pump, text, NEW_HA)
    last = sections[-1]
    assert last["cards"][0]["heading"] == {"sv": "Felsökning", "en": "Troubleshooting"}[lang]
    assert last["cards"][0]["icon"] == "mdi:bug-outline"
    (card,) = last["cards"][1:]
    assert card["type"] == dashboard_views.CONTROLS_CARD
    (item,) = card["items"]
    assert item["entity"] == SWITCH
    assert item["name"] == NAMES[lang]
    assert "stats.rnet.se" in item["explanation"] and "14" in item["explanation"]
    assert item["source"] == {
        "sv": "Integrationens inställning, samma som under Konfigurera",
        "en": "The integration's setting, the same as under Configure",
    }[lang]
    if house == "pt":
        # Control is off there: the note says so, and the box is there all the same.
        assert not pumps[house]["control_enabled"]
        assert _headings(sections) == [text["control"], text["troubleshooting"]]
    else:
        assert _headings(sections)[:-1] == [text["heating"], text["hot_water"], text["power"]]


def test_without_the_switch_the_controls_tab_is_as_it_was(dashboard_views, pumps):
    for house in ("vsh", "pt"):
        sections = dashboard_views.controls_sections(
            pumps[house], dashboard_views.TEXT["sv"], NEW_HA
        )
        assert "Felsökning" not in _headings(sections)


def test_all_values_has_it_under_its_heading_and_other_has_it_not(dashboard_views, pumps):
    pump = _with_switch(pumps["vsh"])
    text = dashboard_views.TEXT["sv"]
    (section,) = dashboard_views.values_sections(pump, text, NEW_HA)
    rows = section["cards"][1]["rows"]
    at = rows.index({"heading": "Felsökning"})
    assert rows[at + 1]["entity"] == SWITCH
    assert rows[at - 1]["entity"] != SWITCH and "heading" not in rows[at - 1]
    assert "debug_report" in dashboard_views._KNOWN_KEYS
    performance = dashboard_views.performance_sections(pump, text, NEW_HA)
    assert SWITCH not in dashboard_views.entity_ids({"views": [{"sections": performance}]})


@pytest.mark.parametrize("lang", ["sv", "en"])
def test_the_section_card_offers_it_as_troubleshooting(dashboard_views, pumps, lang):
    section_card = load("section_card")
    pump = _with_switch(pumps["vsh"], lang)
    assert section_card.SECTIONS["troubleshooting"] == ("controls", "troubleshooting")
    cards = section_card.section_cards(pump, "troubleshooting", NEW_HA)
    page = dashboard_views.controls_sections(pump, dashboard_views.TEXT[lang], NEW_HA)[-1]
    assert cards == page["cards"][1:]
    assert section_card.label("troubleshooting", "sv") == "Styrning: Felsökning"
    assert section_card.label("troubleshooting", "en") == "Controls: Troubleshooting"
    # A pump without the switch gives an empty card, as any section it lacks.
    assert section_card.section_cards(pumps["pt"], "troubleshooting", NEW_HA) == []


# ------------------------------------------------------------- the card


def _play(states: dict, script: str = "") -> dict:
    """Mount the controls card around the switch, run ``script``, read the box and the calls."""
    config = {
        "items": [{"entity": SWITCH, "name": NAMES["sv"], "explanation": "Skickar allt."}],
        "states": {"on": "Till", "off": "Av"},
    }
    program = f"""
      const page = require({json.dumps(str(PAGE))});
      const calls = [];
      const open = [];
      const hass = (states) => ({{
        states,
        callService: (domain, service, data) => {{
          calls.push([domain, service, data]);
          return new Promise((resolve, reject) => open.push({{ resolve, reject }}));
        }},
      }});
      const card = page.mountCard(
        {json.dumps(str(CARD))}, "ctc-ecozenith-controls", {json.dumps(config)});
      card.hass = hass({json.dumps(states)});
      const box = () => page.all(card, (e) => e.tagName === "input")[0];
      const answer = async (verdict) => {{
        const call = open.shift();
        if (verdict === "accept") call.resolve(); else call.reject(new Error("refused"));
        await new Promise((resolve) => setImmediate(resolve));
      }};
      const t = {{
        state: (states) => {{ card.hass = hass(states); }},
        tick: () => {{ box().checked = !box().checked; box().fire("change"); }},
        accept: () => answer("accept"),
        refuse: () => answer("refuse"),
      }};
      const read = () => ({{
        type: box().type, checked: box().checked, disabled: box().disabled,
        label: box().getAttribute("aria-label"),
        pending: box().parentNode.dataset.pending,
      }});
      (async () => {{
        const before = read();
        {script}
        console.log(JSON.stringify({{before, after: read(), calls}}));
      }})().catch((err) => {{ console.error(err); process.exit(1); }});
    """
    done = subprocess.run(["node", "-e", program], capture_output=True, text=True, timeout=30)
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout)


def _state(state: str) -> dict:
    return {SWITCH: {"state": state, "attributes": {"friendly_name": NAMES["sv"]}}}


node = pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")


@node
def test_the_card_draws_a_tick_box_named_by_the_row():
    found = _play(_state("off"))
    assert found["before"] == {
        "type": "checkbox", "checked": False, "disabled": False,
        "label": NAMES["sv"], "pending": "0",
    }
    assert _play(_state("on"))["before"]["checked"] is True
    assert _play(_state("unavailable"))["before"]["disabled"] is True


@node
def test_a_tick_turns_the_switch_on_at_once_and_the_state_ends_the_wait():
    found = _play(_state("off"), """
      t.tick();
      t.state(STATES_OFF);   // a state from before the write keeps the tick
      const waiting = box().checked;
      await t.accept();
      t.state(STATES_ON);
      if (!waiting) throw new Error("the tick was taken back while the write was on its way");
    """.replace("STATES_OFF", json.dumps(_state("off"))).replace(
        "STATES_ON", json.dumps(_state("on"))))
    assert found["calls"] == [["switch", "turn_on", {"entity_id": SWITCH}]]
    assert found["after"]["checked"] is True and found["after"]["pending"] == "0"


@node
def test_untick_turns_it_off_and_a_refusal_puts_back_what_the_state_says():
    found = _play(_state("on"), """
      t.tick();
      await t.refuse();
    """)
    assert found["calls"] == [["switch", "turn_off", {"entity_id": SWITCH}]]
    assert found["after"]["checked"] is True, "vägrat: rutan visar tillståndet igen"
