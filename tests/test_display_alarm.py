"""The display's alarm as a binary sensor, what it is on for, and what it says (0.27.1).

The sensor is on while a round of the harvest saw the open episode on the
panel within the confirmation window, or while the heat pump's own alarm
status is on. It says the code, the text, when the episode began and whether a
round has seen it lately, under the Swedish names and their English twins. Its
timer runs out the window by itself, so an episode nobody reads any more does
not stay red. The diagnostics carry the time of the last sighting, and the
explanation says what the sensor reads and what "not confirmed" means.

The entity is judged here against stand-ins, as the other binaries are; the
same wiring under a real core is in test_homeassistant_rows_alarms.py.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from conftest import load
import ha_stub
from test_seen import FakeStore

NOW = datetime(2026, 10, 20, 9, 0, tzinfo=UTC)
E050 = "[E050] Stopp, låg överhettning exp.ventil"
#: The heat pump's status code for an alarm, as const.py names it.
HEATPUMP_ALARM = 7
HEATPUMP_RUNNING = 3


@pytest.fixture(scope="module")
def binary_sensor():
    ha_stub.install()
    return load("binary_sensor")


@pytest.fixture(scope="module")
def alarms():
    return load("alarms")


class FakeModbus:
    """The Modbus coordinator as the binary reads it: data, codes, and whether it answered."""

    def __init__(self, data=None, codes=None, success=True):
        self.data = dict(data or {})
        self.codes = dict(codes or {})
        self.last_update_success = success


def _runtime(log, *, status=HEATPUMP_RUNNING, success=True):
    codes = {"hp1_status": status}
    return SimpleNamespace(
        modbus=FakeModbus({"hp1_status": status}, codes, success),
        web=None,
        device={"identifiers": {("ctc_ecozenith", "pump")}},
        alarms=log,
    )


def _entity(binary_sensor, log, **kwargs):
    return binary_sensor.CtcDisplayAlarm(_runtime(log, **kwargs))


@pytest.fixture()
def now(monkeypatch, binary_sensor):
    """The clock the binary reads, held at NOW."""
    monkeypatch.setattr(binary_sensor, "utcnow", lambda: NOW)
    return NOW


def _log(alarms, *sightings, closed_at=None):
    """A log that has seen the alarm at each moment in ``sightings``."""
    log = alarms.AlarmLog(FakeStore(), confirm_within=timedelta(minutes=15))
    for when in sightings:
        log.note(E050, when, 7.0)
    if closed_at is not None:
        log.note(None, closed_at, 7.0, complete=True)
    return log


# ------------------------------------------------------------- the state


def test_it_is_on_while_a_round_saw_the_alarm_within_the_window(binary_sensor, alarms, now):
    entity = _entity(binary_sensor, _log(alarms, NOW - timedelta(minutes=5)))
    assert entity.is_on is True


def test_it_is_off_for_an_open_episode_no_round_has_seen_within_the_window(
    binary_sensor, alarms, now
):
    entity = _entity(binary_sensor, _log(alarms, NOW - timedelta(minutes=20)))
    assert entity.is_on is False, "en öppen episod som ingen sett på länge är inte pågående"
    assert entity.extra_state_attributes["bekräftad"] is False


def test_it_is_on_for_the_heat_pump_alarm_alone_and_then_says_no_code(
    binary_sensor, alarms, now
):
    entity = _entity(binary_sensor, _log(alarms), status=HEATPUMP_ALARM)
    assert entity.is_on is True
    attributes = entity.extra_state_attributes
    assert attributes["kod"] is None and attributes["code"] is None
    assert attributes["visat"] is None and attributes["bekräftad"] is False


def test_it_is_off_when_nothing_is_shown_and_the_heat_pump_is_running(binary_sensor, alarms, now):
    entity = _entity(binary_sensor, _log(alarms))
    assert entity.is_on is False


def test_a_closed_episode_leaves_no_code_behind(binary_sensor, alarms, now):
    entity = _entity(binary_sensor, _log(alarms, NOW - timedelta(minutes=5),
                                         closed_at=NOW - timedelta(minutes=2)))
    assert entity.is_on is False
    attributes = entity.extra_state_attributes
    assert attributes["kod"] is None and attributes["last_confirmed"] is None


# ------------------------------------------------------------ the attributes


def test_the_attributes_say_code_text_start_and_the_confirmation_under_both_names(
    binary_sensor, alarms, now
):
    start = NOW - timedelta(minutes=10)
    seen = NOW - timedelta(minutes=5)
    log = _log(alarms, start, seen)
    attributes = _entity(binary_sensor, log).extra_state_attributes
    assert attributes["kod"] == attributes["code"] == "E050"
    assert attributes["text"] == "Stopp, låg överhettning exp.ventil"
    assert attributes["start"] == start.isoformat(timespec="seconds")
    assert attributes["visat"] == attributes["shown"] == E050
    assert attributes["senast bekräftad"] == attributes["last_confirmed"] == seen.isoformat(
        timespec="seconds"
    )
    assert attributes["bekräftad"] is True and attributes["confirmed"] is True


def test_the_entity_is_a_problem_named_by_its_translation_key_with_its_id_beside_the_others(
    binary_sensor, alarms, now
):
    entity = _entity(binary_sensor, _log(alarms))
    assert entity._attr_translation_key == "display_alarm"
    assert entity._attr_device_class == binary_sensor.BinarySensorDeviceClass.PROBLEM
    assert entity._attr_unique_id.endswith("_display_alarm")


def test_available_while_modbus_answers_or_an_alarm_is_confirmed(binary_sensor, alarms, now):
    quiet = _log(alarms)
    assert _entity(binary_sensor, quiet, success=True).available is True
    assert _entity(binary_sensor, quiet, success=False).available is False
    seen = _log(alarms, NOW - timedelta(minutes=2))
    assert _entity(binary_sensor, seen, success=False).available is True


# ------------------------------------------------------------ the timer


def test_the_window_runs_out_by_itself_and_the_state_is_written_then(
    binary_sensor, alarms, monkeypatch
):
    scheduled: list[tuple] = []
    monkeypatch.setattr(
        binary_sensor, "async_call_later",
        lambda hass, delay, action: scheduled.append((hass, delay, action)) or (lambda: None),
    )
    log = _log(alarms, NOW - timedelta(minutes=5))
    monkeypatch.setattr(binary_sensor, "utcnow", lambda: NOW)
    entity = _entity(binary_sensor, log)
    writes: list[int] = []
    entity.async_write_ha_state = lambda: writes.append(1)
    entity.hass = object()

    entity._schedule_expiry()
    # The sighting was five minutes ago, the window is fifteen: ten minutes
    # and a second to go.
    assert [delay for _hass, delay, _action in scheduled] == [601.0]

    entity._expired(NOW)
    assert writes == [1]
    assert entity._cancel_expiry is None


def test_no_timer_is_set_when_nothing_is_confirmed(binary_sensor, alarms, monkeypatch, now):
    scheduled: list = []
    monkeypatch.setattr(binary_sensor, "async_call_later", lambda *args: scheduled.append(args))
    entity = _entity(binary_sensor, _log(alarms, NOW - timedelta(minutes=40)))
    entity.hass = object()
    entity._schedule_expiry()
    assert scheduled == []


def test_a_window_that_has_run_out_sets_no_timer(binary_sensor, alarms, monkeypatch, now):
    scheduled: list = []
    monkeypatch.setattr(binary_sensor, "async_call_later", lambda *args: scheduled.append(args))
    entity = _entity(binary_sensor, _log(alarms, NOW - timedelta(minutes=16)))
    entity.hass = object()
    entity._schedule_expiry()
    assert scheduled == []


# ------------------------------------------------------- the diagnostics


def test_the_diagnostics_carry_the_time_of_the_last_sighting_and_nothing_more(alarms, now):
    diagnostics_data = load("diagnostics_data")
    log = _log(alarms, NOW - timedelta(minutes=5))
    section = diagnostics_data.alarms_section(SimpleNamespace(alarms=log))
    assert section["last_confirmed"] == (NOW - timedelta(minutes=5)).isoformat(timespec="seconds")
    assert set(section) == {"active", "last_confirmed", "episodes"}
    quiet = diagnostics_data.alarms_section(SimpleNamespace(alarms=_log(alarms)))
    assert quiet["last_confirmed"] is None


# ----------------------------------------------------- the explanation


def test_the_explanation_says_what_it_reads_and_what_not_confirmed_means():
    explanations = load("explanations")
    text = explanations.DERIVED["display_alarm"]
    swedish = str(text)
    assert "egna larmtexter" in swedish, "läses ur displayen, inte ur ett register"
    assert "Obekräftat" in swedish
    english = text.en
    assert "not from a register" in english and "Not confirmed" in english
    for words in (swedish, english):
        assert not any(dash in words for dash in ("–", "—", " - ")), words


def test_the_explanation_is_what_the_card_shows_for_the_entity():
    explanations = load("explanations")
    assert explanations.explain("display_alarm", "en") == explanations.DERIVED["display_alarm"].en
    assert explanations.explain("display_alarm", "sv") == str(explanations.DERIVED["display_alarm"])
