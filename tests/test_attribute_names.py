"""The Swedish attribute names have English twins, both sent for a version (R22).

Automations and the energy manager read skäl, underlag, the four energy
figures beside the coefficient of performance (dygn i underlaget, avgiven
värme kWh, tillförd energi kWh, tillförd energi ur), kod, källa, sida, skärm,
senast läst, senast skriven, gäller till, styrning aktiv and episoder.
Each now has an English twin with the same value, except control_active,
which is a boolean, and episodes, whose episodes carry English keys inside;
the Swedish names go in the next major version, which the README says beside
a table from old to new. Here the rule on its own, every entity that carries
one of the names, the card reading both for an override in force, and the
README's table held to the names in code.
"""

from __future__ import annotations

from datetime import UTC, datetime
import json
import re
import shutil
import subprocess
from types import SimpleNamespace

import pytest

from conftest import ROOT, load
import ha_stub

#: The twins as R22 names them.
EXPECTED = {
    "skäl": "reason",
    "underlag": "basis",
    "dygn i underlaget": "days_in_basis",
    "avgiven värme kWh": "heat_out_kwh",
    "tillförd energi kWh": "energy_in_kwh",
    "tillförd energi ur": "energy_in_from",
    "kod": "code",
    "källa": "source",
    "sida": "page",
    "skärm": "screen",
    "senast läst": "read_at",
    "senast skriven": "last_written",
    "gäller till": "valid_until",
    "styrning aktiv": "control_active",
    "episoder": "episodes",
    # R49: the pump's own value beside a control, and the overrides the
    # sensor Aktiva överstyrningar lists, twinned from the start.
    "pumpens värde": "pump_value",
    "styrningar": "overrides",
    # R28: the two energies beside the share of auxiliary heat.
    "elpatron kWh": "immersion_kwh",
    "kompressor kWh": "compressor_kwh",
    # 0.27.1: the display's alarm, the text the panel prints and when a round last saw it.
    "visat": "shown",
    "senast bekräftad": "last_confirmed",
    "bekräftad": "confirmed",
}

READ =datetime(2026, 10, 9, 11, 40, tzinfo=UTC)


@pytest.fixture(scope="module")
def names():
    return load("attribute_names")


@pytest.fixture(scope="module")
def stubbed():
    ha_stub.skip_unless_stubbed()


# ------------------------------------------------------------------ the rule


def test_the_twins_are_the_ones_r22_names(names):
    assert names.ENGLISH == EXPECTED
    for english in names.ENGLISH.values():
        assert re.fullmatch(r"[a-z][a-z_]*", english), english


def test_a_twin_carries_the_same_value_beside_the_swedish_name(names):
    shown = names.with_english({"skäl": "räknarna har inte lästs", "pågår": "ja"})
    assert shown == {
        "skäl": "räknarna har inte lästs",
        "reason": "räknarna har inte lästs",
        "pågår": "ja",
    }, "bara namnen i tabellen får en tvilling"


def test_a_twin_the_caller_has_set_is_kept(names):
    shown = names.with_english({"styrning aktiv": "ja", "control_active": True})
    assert shown["control_active"] is True
    assert shown["styrning aktiv"] == "ja"


def test_nothing_stays_nothing(names):
    assert names.with_english(None) is None
    assert names.with_english({}) == {}


def test_an_episode_in_english(names):
    episode = {"kod": "E017", "text": "Givare solpaneler ut", "start": "s", "slut": None,
               "utetemperatur": 7.2}
    assert names.episode_in_english(episode) == {
        "code": "E017", "text": "Givare solpaneler ut", "start": "s", "end": None,
        "outdoor_temperature": 7.2,
    }


# ------------------------------------------------------------- the entities


def test_an_unlabelled_code_is_given_under_both_names(stubbed, const):
    sensor = load("sensor")
    description = next(d for d in const.MODBUS_SENSORS if d.key == "system_status")
    coordinator = SimpleNamespace(data={"system_status": "Okänd"},
                                  unknown_codes={"system_status": 12})
    entity = sensor.CtcModbusSensor.__new__(sensor.CtcModbusSensor)
    entity.coordinator = coordinator
    entity._description = description
    assert entity.extra_state_attributes == {"kod": 12, "code": 12}
    coordinator.unknown_codes = {}
    assert entity.extra_state_attributes is None


def test_a_display_row_says_where_and_when_under_both_names(stubbed, const):
    sensor = load("sensor")
    value = const.SlowValue(key="p22_utetemperatur", label="Utetemperatur", page=22, screen=220,
                            fmt="%.1f")
    web = SimpleNamespace(
        data={"p22_utetemperatur": 7.2},
        last_update_success=True,
        last_read=lambda key: READ,
        is_fresh=lambda key: True,
    )
    runtime = SimpleNamespace(web=web, device={"identifiers": {("ctc_ecozenith", "pump")}})
    attributes = sensor.CtcDisplaySensor(runtime, "Värmepump", value).extra_state_attributes
    for swedish, english in (("källa", "source"), ("sida", "page"), ("skärm", "screen"),
                             ("senast läst", "read_at")):
        assert attributes[english] == attributes[swedish], swedish
    assert attributes["read_at"] == READ.isoformat(timespec="seconds")
    assert attributes["page"] == "22" and attributes["screen"] == "220"


def test_the_latest_alarm_gives_its_code_under_both_names(stubbed):
    sensor = load("sensor")
    latest = {"code": "E017", "text": "Givare solpaneler ut",
              "shown": "[E017] Givare solpaneler ut",
              "start": "2026-10-09T06:00:00+00:00", "end": None, "outdoor": 7.2}
    runtime = SimpleNamespace(
        web=SimpleNamespace(data={}, last_update_success=True),
        alarms=SimpleNamespace(latest=latest),
        device={"identifiers": {("ctc_ecozenith", "pump")}},
    )
    attributes = sensor.CtcLastAlarmSensor(runtime).extra_state_attributes
    assert attributes["kod"] == attributes["code"] == "E017"
    assert attributes["pågår"] == "ja", "övriga namn står kvar som de var"


def test_the_coefficient_of_performance_gives_basis_and_reason(stubbed, cop):
    from test_cop_sensor import _runtime, _sensors

    sensor = load("sensor")
    sensors = _sensors(sensor, _runtime(cop))
    day = sensors["day"].extra_state_attributes
    assert day["basis"] == day["underlag"] == "senaste dygnet"
    assert day["reason"] == day["skäl"]
    lifetime = sensors["lifetime"].extra_state_attributes
    assert lifetime["basis"] == "hela livslängden"
    assert "reason" not in lifetime and "skäl" not in lifetime


def test_the_energy_figures_beside_the_coefficient_of_performance_have_twins(stubbed, cop):
    """R22 gave the five released names, underlag to tillförd energi ur, one
    version side by side, so an energy manager moves once (F4.3)."""
    from test_cop_sensor import _runtime, _sensors

    sensor = load("sensor")
    for span, attributes in _sensors(sensor, _runtime(cop)).items():
        shown = attributes.extra_state_attributes
        for swedish, english in (
            ("dygn i underlaget", "days_in_basis"),
            ("avgiven värme kWh", "heat_out_kwh"),
            ("tillförd energi kWh", "energy_in_kwh"),
            ("tillförd energi ur", "energy_in_from"),
        ):
            assert swedish in shown, (span, swedish)
            assert shown[english] == shown[swedish], (span, swedish)


class _Control:
    """The control manager as the entities read it, with one override in force or none."""

    def __init__(self, active: dict[int, int]) -> None:
        self.active = active

    def get(self, address):
        return self.active.get(address)

    def written_attributes(self, address):
        if address not in self.active:
            return {}
        return {"senast skriven": "2026-10-09T11:40:00+00:00",
                "gäller till": "2026-10-09T11:45:00+00:00"}

    def async_add_listener(self, listener):
        return lambda: None


def _control_runtime(const, active):
    return SimpleNamespace(
        control=_Control(active),
        modbus=SimpleNamespace(descriptions=const.MODBUS_SENSORS + const.MODBUS_SETTINGS, data={}),
        device={"identifiers": {("ctc_ecozenith", "pump")}},
    )


def _control(const, key):
    return next(r for r in const.CONTROL_NUMBERS + const.CONTROL_SELECTS if r.key == key)


def test_a_number_says_whether_control_is_in_force_as_a_boolean(const):
    number = load("number")
    register = _control(const, "ctl_room_setpoint_1")
    idle = number.CtcControlNumber(_control_runtime(const, {}), register).extra_state_attributes
    assert idle["styrning aktiv"] == "nej" and idle["control_active"] is False
    assert "last_written" not in idle and "valid_until" not in idle
    busy = number.CtcControlNumber(
        _control_runtime(const, {register.address: 215}), register
    ).extra_state_attributes
    assert busy["styrning aktiv"] == "ja" and busy["control_active"] is True
    assert busy["last_written"] == busy["senast skriven"]
    assert busy["valid_until"] == busy["gäller till"]


def test_a_select_gives_the_two_moments_under_both_names(const):
    select = load("select")
    register = _control(const, "ctl_dhw_mode")
    busy = select.CtcControlSelect(
        _control_runtime(const, {register.address: 2}), register
    ).extra_state_attributes
    assert busy["last_written"] == busy["senast skriven"]
    assert busy["valid_until"] == busy["gäller till"]
    idle = select.CtcControlSelect(_control_runtime(const, {}), register).extra_state_attributes
    assert "last_written" not in idle


def test_the_alarm_binary_carries_the_episodes_in_english_too(stubbed):
    binary_sensor = load("binary_sensor")
    alarms = load("alarms")
    log = alarms.AlarmLog.__new__(alarms.AlarmLog)
    log.episodes = [{"code": "E017", "text": "Givare solpaneler ut",
                     "shown": "[E017] Givare solpaneler ut",
                     "start": "2026-10-09T06:00:00+00:00", "end": None, "outdoor": 7.2}]
    item = next(i for i in binary_sensor.DERIVED if i.episodes)
    runtime = SimpleNamespace(
        modbus=SimpleNamespace(data={}, codes={}, last_update_success=True),
        alarms=log,
        device={"identifiers": {("ctc_ecozenith", "pump")}},
    )
    attributes = binary_sensor.CtcDerivedBinary(runtime, item).extra_state_attributes
    (swedish,) = attributes["episoder"]
    (english,) = attributes["episodes"]
    assert swedish == {"kod": "E017", "text": "Givare solpaneler ut",
                       "start": "2026-10-09T06:00:00+00:00",
                       "slut": None, "utetemperatur": 7.2}
    assert english == {"code": "E017", "text": "Givare solpaneler ut",
                       "start": "2026-10-09T06:00:00+00:00",
                       "end": None, "outdoor_temperature": 7.2}


# -------------------------------------------------------------- the readme


def test_the_readme_has_the_table_and_says_when_the_swedish_names_go(names):
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    section = readme.split("## Attribute names")[1].split("\n## ")[0]
    assert "removed in the next major version" in section
    for swedish, english in names.ENGLISH.items():
        assert re.search(rf"\| `{re.escape(swedish)}`[^|]*\| `{english}`", section), swedish
    for swedish, english in names.EPISODE_KEYS.items():
        assert f"`{swedish}`" in section and f"`{english}`" in section, swedish
    (row,) = [line for line in section.splitlines() if line.startswith("| `styrningar`")]
    for swedish, english in names.OVERRIDE_KEYS.items():
        assert f"`{swedish}`" in row and f"`{english}`" in row, swedish


def test_an_override_says_when_it_was_set_under_both_names(names):
    """F6.2: satt and set_at, which the keepalive's rewrites do not move, in place of
    the last write."""
    assert names.OVERRIDE_KEYS["satt"] == "set_at"
    assert "senast skriven" not in names.OVERRIDE_KEYS
    item = {"namn": "Styr rumsbörvärde", "register": "1010", "värde": 21.5,
            "satt": "2026-10-13T09:12:00+00:00"}
    assert names.override_in_english(item) == {
        "name": "Styr rumsbörvärde", "register": "1010", "value": 21.5,
        "set_at": "2026-10-13T09:12:00+00:00",
    }


# ---------------------------------------------------------------- the card

CARD = ROOT / "custom_components" / "ctc_ecozenith" / "www" / "ctc-ecozenith-card.js"


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_the_card_reads_an_override_in_force_under_either_name():
    """A 0 the pump means as no limit is a word only while nothing is written."""
    states = {"no_limit": "ingen gräns"}

    def number(**attributes):
        return {"state": "0.0", "attributes": {"unit_of_measurement": "rps", **attributes}}

    cases = [
        number(control_active=False, **{"styrning aktiv": "nej"}),
        number(control_active=True, **{"styrning aktiv": "ja"}),
        number(control_active=True),
        number(control_active=False),
        number(**{"styrning aktiv": "ja"}),
        number(**{"styrning aktiv": "nej"}),
        number(),
    ]
    program = (
        f"const c = require({json.dumps(str(CARD))});\n"
        f"const cases = {json.dumps(cases)};\n"
        "console.log(JSON.stringify(cases.map((s) => "
        f"c.ownWord('number.a', s, {json.dumps(states)}, 'no_limit') ?? null)));\n"
    )
    done = subprocess.run(["node", "-e", program], capture_output=True, text=True, timeout=30)
    assert done.returncode == 0, done.stderr
    assert json.loads(done.stdout) == [
        "ingen gräns", None, None, "ingen gräns", None, "ingen gräns", "ingen gräns",
    ]
