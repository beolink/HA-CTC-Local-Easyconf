"""The report's keys are a closed list, and the consent text names them all.

The consent text under Configure once listed the coefficient of performance
over a day, a year and the lifetime and promised "never any single reading",
while the report had grown the first year, five flags about the counters and,
on a fault, the two lifetime totals in kilowatt hours. Nothing in the suite
noticed, because nothing compared the whole report with anything. These tests
do: every parameter of the builder is set, and the keys that come out have to
match the two closed lists in stats_extra exactly. A new key fails here first,
before it can leave the house unmentioned.
"""

from __future__ import annotations

import inspect
import json
import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parent.parent
COMPONENT = ROOT / "custom_components" / "ctc_ecozenith"

#: Every parameter build_extra takes, each given a value that produces a key.
#: A parameter that is added to the builder has to be added here too, which is
#: the point: the test cannot be passed by leaving the new thing out.
EVERYTHING = {
    "model": "EcoZenith i255",
    "has_display": True,
    "control_enabled": True,
    "page_count": 7,
    "read_failures": 1,
    "heatpump_model": "EA720M",
    "serial": "720825400001",
    "display_firmware": "20260610",
    "heatpump_firmware": "20260522",
    "control_firmware": 610,
    "history_page": True,
    "heat_counter": True,
    "consumption_counter": False,
    "consumption_modbus": True,
    "heat_total": True,
    "consumption_total": True,
    "cop_floor": False,
    "cop_stuck": True,
    "cop_implausible": False,
    "cop_integrated": True,
    "power_rows": True,
    "power_values": True,
    "menu_pages": 7,
    "menu_home": True,
    "menu_root": True,
    # A known model sends no family flag, so the closed list holds as it is;
    # the flag for a model reported as other is tested below.
    "settings_stem": "ezi2xx",
    "heat_total_kwh": 22499.0,
    "consumption_total_kwh": 9116.0,
    "cop_day": 2.9,
    "cop_year": 2.6,
    "cop_first_year": 2.5,
    "cop_lifetime": 2.47,
    "cop_estimate_day": 3.16,
    "cop_estimate_lifetime": 3.61,
    "cop_estimate_carnot": True,
}


def test_every_parameter_of_the_builder_is_set_here(stats_extra):
    parameters = set(inspect.signature(stats_extra.build_extra).parameters)
    assert parameters == set(EVERYTHING), (
        "build_extra har fått en ny parameter: lägg den i EVERYTHING, i FEATURE_KEYS "
        "eller METRIC_KEYS, i samtyckestexten, i README och på stats.rnet.se/integritet"
    )


def test_the_flags_and_the_numbers_are_closed_lists(stats_extra):
    payload = stats_extra.build_extra(**EVERYTHING)
    assert set(payload) == {"models", "features", "errors", "firmwares", "metrics"}
    assert set(payload["features"]) == stats_extra.FEATURE_KEYS
    assert set(payload["metrics"]) == stats_extra.METRIC_KEYS


def test_the_lists_hold_nothing_but_flags_and_numbers(stats_extra):
    payload = stats_extra.build_extra(**EVERYTHING)
    for key, value in payload["features"].items():
        assert isinstance(value, (bool, int)) and not isinstance(value, str), key
    for key, value in payload["metrics"].items():
        assert isinstance(value, (int, float)) and not isinstance(value, bool), key
    assert all(isinstance(v, str) for v in payload["firmwares"].values())
    # The models are slugs from a closed list, never the name as discovered.
    assert payload["models"] == ["i255", "ea720m"]


def test_the_lists_do_not_overlap_and_are_what_the_consent_text_counts(stats_extra):
    assert not (stats_extra.FEATURE_KEYS & stats_extra.METRIC_KEYS)
    # Four figures, two estimated ones, two totals, three parts of the serial
    # number's story.
    assert len(stats_extra.METRIC_KEYS) == 11
    # Four about the set-up, six about whether a figure is possible, three why
    # not, three about the two power rows on the heat pump's page, whether they
    # gave numbers, and a figure the integration worked out from them itself, and
    # three about the display's menu:
    # how many pages it has, and how far the last walk through it got (R12, L6),
    # and whether the estimate comes from the refrigerant circuit (0.28.1).
    assert len(stats_extra.FEATURE_KEYS) == 20


def test_a_model_reported_as_other_adds_its_family_and_nothing_else(stats_extra):
    # The one built key: family_<stem>, beside the closed list, for a model the
    # integration does not know (R19). Everything the backend takes: a boolean
    # or a whole number under a key of its pattern, at most 24 of them.
    payload = stats_extra.build_extra(
        **{**EVERYTHING, "model": "EcoZenith (ezi4xx)", "settings_stem": "ezi4xx"}
    )
    assert payload["models"] == ["other", "ea720m"]
    assert set(payload["features"]) == stats_extra.FEATURE_KEYS | {"family_ezi4xx"}
    assert payload["features"]["family_ezi4xx"] is True
    for key in payload["features"]:
        assert re.match(r"^[a-z][a-z0-9_]{0,31}$", key), key
    assert len(payload["features"]) <= 24


# ------------------------------------------------------ the consent text


def _consent(name: str) -> str:
    texts = json.loads((COMPONENT / name).read_text(encoding="utf-8"))
    return texts["options"]["step"]["init"]["data_description"]["send_statistics"]


def test_the_english_consent_text_names_what_the_report_carries():
    for name in ("strings.json", "translations/en.json"):
        text = _consent(name)
        for phrase in (
            "first year",
            "kilowatt hours",
            "only when it is wrong",
            "Python version",
            "devices",
            "hash",
            "warnings and errors",
            # stats.py's own fields, which build_extra never sees: the random
            # id that keeps one installation's reports apart (and that the
            # forget-me request is made in the name of).
            "random id",
            # Every flag in FEATURE_KEYS by name: history_page means the row
            # with the operating hours was recognised, not that the page is
            # harvested.
            "operating hours",
            # power_rows and cop_integrated: the two power rows, and a figure
            # the integration worked out from them itself.
            "two power rows",
            "handed over numbers",
            "own sums of the two power readings",
            # menu_pages, menu_home and menu_root.
            "pages the display's menu has",
            "home screen",
            # family_<stem>, for a model the integration does not know.
            "family code",
            "{endpoint}",
            "{privacy_url}",
        ):
            assert phrase in text, f"{phrase!r} saknas i {name}"
        # The promise the report no longer kept.
        assert "any single reading" not in text, name


def test_the_swedish_consent_text_names_what_the_report_carries():
    text = _consent("translations/sv.json")
    for phrase in (
        "första år",
        "kilowattimmar",
        "bara när det är fel",
        "Python-version",
        "enheter",
        "hash",
        "varningar och fel",
        "slumpat id",
        "de två effektraderna",
        "lämnade tal",
        "egen summering av de två effektvärdena",
        "drifttid",
        "sidor displayens meny har",
        "hemskärmen",
        "familjekoden",
        "{endpoint}",
        "{privacy_url}",
    ):
        assert phrase in text, f"{phrase!r} saknas i sv.json"
    assert "enskild avläsning" not in text
    # Swedish text in this repo is written without dashes as punctuation.
    assert "–" not in text and "—" not in text and " - " not in text


def test_the_two_english_texts_are_the_same():
    assert _consent("strings.json") == _consent("translations/en.json")


def test_the_readme_tells_the_same_story():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    section = readme[readme.index("## Anonymous statistics"):]
    section = section[: section.index("\n## ", 1)]
    for phrase in (
        "first year",
        "Python version",
        "devices",
        "hash",
        "warnings and errors",
        "random id",
        "operating hours",
        "pages the display's menu has",
        "home screen",
        "family code",
        "two power rows",
        "handed over numbers",
        "FEATURE_KEYS",
        "METRIC_KEYS",
        "only when it is wrong",
    ):
        assert phrase in section, f"{phrase!r} saknas i README:s statistikavsnitt"


def test_the_readme_names_the_one_key_outside_the_closed_lists(stats_extra):
    """The paragraph on the closed lists said a key not on them fails a test,
    while family_<stem> is sent beside them; it now names that key, when it is
    sent, and the very pattern the code holds it to (F5.3)."""
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    section = readme[readme.index("## Anonymous statistics"):]
    section = " ".join(section[: section.index("\n## ", 1)].split())
    closed = section[section.index("two closed lists"):]
    closed = closed[: closed.index("unnoticed")]
    assert "one built key" in closed
    assert "`family_`" in closed and '"other"' in closed
    assert f"`{stats_extra.FAMILY_STEM.pattern}`" in closed
    assert stats_extra.FAMILY_PREFIX == "family_"


def test_the_draft_schema_that_said_nothing_was_implemented_is_gone():
    # docs/reporting.md was a public draft of a far larger report, with "nothing
    # here is implemented yet" at the top, next to a consent text describing
    # the real one. Two descriptions of what leaves the house is one too many.
    assert not (ROOT / "docs" / "reporting.md").exists()
