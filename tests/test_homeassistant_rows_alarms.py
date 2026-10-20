"""Display rows and alarms under a real Home Assistant core (L3, R30).

Two things the ordinary suite cannot see end to end. A display row that reads
CTC's "not fitted" marker at set-up gets no entity until it first leaves a
number, and then gets it through async_add_entities with no reload; and a
registry entry for a row that is no longer on a page the menu still has is
removed, while one for a page the menu does not know is left alone. And the
alarm the panel prints in its header icon becomes the sensor Senaste larm and
an episode on the binary Larm, with the outdoor temperature Modbus had.

Shares the stand-ins and fixtures of test_homeassistant.py and runs the same
way, from a virtual environment that has Home Assistant and
pytest-homeassistant-custom-component installed:

    python -m pytest -o asyncio_mode=auto tests/test_homeassistant_rows_alarms.py
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

pytest.importorskip("pytest_homeassistant_custom_component")

from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import CONF_HOST
from homeassistant.helpers import entity_registry as er
from homeassistant.loader import async_get_integration
from pytest_homeassistant_custom_component.common import MockConfigEntry

from test_homeassistant import (  # noqa: F401  (the fixtures travel by import)
    HOST,
    IDENTITY,
    MODEL,
    FakePanel,
    _advance,
    _entity_id,
    _needs_auto_asyncio_mode,
    _set_up,
    stubs_fixture,
)

from custom_components.ctc_ecozenith.catalogue import pages_to_storage
from custom_components.ctc_ecozenith.const import (
    CONF_IDENTITY,
    CONF_MENU,
    CONF_MENU_VERSION,
    CONF_MODBUS_PORT,
    CONF_SLAVE,
    CONF_SLOW_PAGES,
    CONF_WEB_PORT,
    DOMAIN,
    SlowPage,
    SlowValue,
)
from custom_components.ctc_ecozenith.harvest import MIN_FIRST_DELAY
from custom_components.ctc_ecozenith.web_api import AlarmReading

PAGE = 22
SCREEN = 220
E017 = "[E017] Givare solpaneler ut"


def _row(key: str, label: str, index: int) -> SlowValue:
    return SlowValue(
        key=f"p{PAGE}_{key}", label=label, page=PAGE, screen=SCREEN,
        fmt="%.1f", var_indices=[index], unit="°C", scale=0.1,
    )


#: One heat pump page as the sweep records it: two rows that read, two that
#: read CTC's marker on an air to water unit, as on VSH's i255.
HEATPUMP = SlowPage(
    page=PAGE, title="Värmepump", screens=[1, 0, SCREEN],
    values=[
        _row("utetemperatur", "Utetemperatur", 0),
        _row("vp_in_ut_1", "VP in/ut 1", 1),
        _row("brine_in_ut_1", "Brine in/ut 1", 2),
        _row("brine_in_ut_2", "Brine in/ut 2", 3),
    ],
)


class ServingPanel(FakePanel):
    """A display standing on the heat pump page, answering its values; never moved."""

    vars: list[int] = []
    header: list[str] = []
    chrome: list[int] = [0]

    async def async_current_page(self) -> int:
        return PAGE

    async def async_screen_map(self, refresh: bool = False) -> dict[int, list[int]]:
        return {PAGE: [1, 0, SCREEN]}

    async def async_vars(self, screen) -> list[int]:
        return list(ServingPanel.vars) if screen == SCREEN else list(ServingPanel.chrome)

    async def async_goto_page(self, target: int, route=None) -> bool:
        return target == PAGE

    async def async_step_back_to(self, target: int, hops: int = 6) -> bool:
        return target == PAGE

    async def async_goto_home(self, hops: int = 6) -> int | None:
        return PAGE

    async def async_alarm_readout(self, screen: int, values):
        # The heat pump page carries an alarm: its header icon is picked by a variable.
        return AlarmReading(True, list(ServingPanel.header))


async def _set_up_with_the_page(hass):
    stored = pages_to_storage([HEATPUMP])
    with patch(f"custom_components.{DOMAIN}.CtcWebClient", ServingPanel):
        entry = await _set_up(hass, **{CONF_SLOW_PAGES: stored, CONF_MENU: stored})
    assert entry.state is ConfigEntryState.LOADED
    return entry


async def _harvest_again(hass, entry) -> None:
    with patch(f"custom_components.{DOMAIN}.CtcWebClient", ServingPanel):
        await entry.runtime_data.web.async_refresh()
        await hass.async_block_till_done()


def _row_entity(hass, key: str) -> str | None:
    return er.async_get(hass).async_get_entity_id(
        "sensor", DOMAIN, f"{DOMAIN}_{HOST}_p{PAGE}_{key}"
    )


async def _entry_with_the_page(hass) -> MockConfigEntry:
    """The entry before set-up, for a test that wants to seed storage or the registry first."""
    stored = pages_to_storage([HEATPUMP])
    version = str((await async_get_integration(hass, DOMAIN)).version)
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=f"{DOMAIN}_{HOST}",
        title=f"{MODEL} ({HOST})",
        data={CONF_HOST: HOST, CONF_MODBUS_PORT: 502, CONF_WEB_PORT: 80, CONF_SLAVE: 1,
              "model": MODEL},
        options={CONF_MENU_VERSION: version, CONF_IDENTITY: IDENTITY,
                 CONF_SLOW_PAGES: stored, CONF_MENU: stored},
    )
    entry.add_to_hass(hass)
    return entry


async def _set_up_entry(hass, entry) -> None:
    with patch(f"custom_components.{DOMAIN}.CtcWebClient", ServingPanel):
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.LOADED


# ------------------------------------------------------- rows that wait


async def test_a_row_that_reads_the_marker_gets_its_entity_when_it_first_leaves_it(hass, stubs):
    """Two brine rows on an air to water unit, as on VSH's i255.

    At set-up they read 9999 and have never read as a number, so no entity is
    made for them, while the two rows that read get theirs as before. The
    first harvest in which the brine rows read as numbers adds them through
    the platform's own async_add_entities: no reload, same unique ids. Once
    made they stay, unavailable while the marker is back, like any reading.
    """
    ServingPanel.vars = [72, 215, 9999, 9999]
    ServingPanel.header = []
    entry = await _set_up_with_the_page(hass)
    # the first harvest is planned, not run in set-up (R6)
    await _advance(hass, MIN_FIRST_DELAY + 1)

    assert hass.states.get(_row_entity(hass, "utetemperatur")).state == "7.2"
    assert hass.states.get(_row_entity(hass, "vp_in_ut_1")).state == "21.5"
    assert _row_entity(hass, "brine_in_ut_1") is None
    assert _row_entity(hass, "brine_in_ut_2") is None

    ServingPanel.vars = [72, 215, 35, 0]
    await _harvest_again(hass, entry)
    assert hass.states.get(_row_entity(hass, "brine_in_ut_1")).state == "3.5"
    assert hass.states.get(_row_entity(hass, "brine_in_ut_2")).state == "0.0", (
        "en äkta nolla är ett tal"
    )
    assert entry.runtime_data.seen.numeric >= {f"p{PAGE}_brine_in_ut_1", f"p{PAGE}_brine_in_ut_2"}

    # The marker is back: the entities stay. The harvest carries a row's last
    # reading through a round it did not get one (its age is R5's business),
    # and the rows are numbers in the record, so a later set-up makes them too.
    ServingPanel.vars = [72, 215, 9999, 9999]
    await _harvest_again(hass, entry)
    assert hass.states.get(_row_entity(hass, "brine_in_ut_1")).state == "3.5"
    assert _row_entity(hass, "brine_in_ut_2") is not None


async def test_a_row_that_has_been_a_number_is_made_at_once_and_the_old_store_is_migrated(
    hass, stubs, hass_storage
):
    """An installation from before: the seen store at version 1 names the row.

    Home Assistant finds the file one version behind and asks the store to
    migrate it; the display rows it knew become the numeric record, so a row
    that reads the marker right now, an outdoor unit switched off, still gets
    its entity at set-up, and the file is written back at minor 2, the major
    still 1 so that the release before can read it after a return.
    """
    entry = await _entry_with_the_page(hass)
    hass_storage[f"{DOMAIN}_{entry.entry_id}_seen"] = {
        "version": 1,
        "minor_version": 1,
        "key": f"{DOMAIN}_{entry.entry_id}_seen",
        "data": {"keys": [f"p{PAGE}_brine_in_ut_1", "hp1_rps"]},
    }
    ServingPanel.vars = [72, 215, 9999, 9999]
    ServingPanel.header = []
    await _set_up_entry(hass, entry)

    assert hass.states.get(_row_entity(hass, "brine_in_ut_1")).state == "unavailable"
    assert _row_entity(hass, "brine_in_ut_2") is None
    seen = entry.runtime_data.seen
    assert f"p{PAGE}_brine_in_ut_1" in seen.numeric and "hp1_rps" in seen.keys
    stored = hass_storage[f"{DOMAIN}_{entry.entry_id}_seen"]
    assert (stored["version"], stored["minor_version"]) == (1, 2)
    assert f"p{PAGE}_brine_in_ut_1" in stored["data"]["numeric"]
    assert set(stored["data"]["keys"]) >= {f"p{PAGE}_brine_in_ut_1", "hp1_rps"}


async def test_only_a_row_gone_from_a_page_the_menu_still_has_leaves_the_registry(hass, stubs):
    """Three entries from an earlier version, three fates.

    A row the parser no longer builds on page 22, which the menu has: removed.
    A row on page 99, which the menu does not know, so was not reached: kept.
    A row still on page 22 that reads the marker: kept, however long it does.
    """
    entry = await _entry_with_the_page(hass)
    registry = er.async_get(hass)
    gone = registry.async_get_or_create(
        "sensor", DOMAIN, f"{DOMAIN}_{HOST}_p{PAGE}_overhettning_s_h_4", config_entry=entry
    )
    unknown_page = registry.async_get_or_create(
        "sensor", DOMAIN, f"{DOMAIN}_{HOST}_p99_varmvatten", config_entry=entry
    )
    marker_row = registry.async_get_or_create(
        "sensor", DOMAIN, f"{DOMAIN}_{HOST}_p{PAGE}_brine_in_ut_2", config_entry=entry
    )
    ServingPanel.vars = [72, 215, 9999, 9999]
    ServingPanel.header = []
    await _set_up_entry(hass, entry)

    assert registry.async_get(gone.entity_id) is None
    assert registry.async_get(unknown_page.entity_id) is not None
    assert registry.async_get(marker_row.entity_id) is not None
    # And the marker row's entry is waiting for its entity, not provided yet.
    assert hass.states.get(marker_row.entity_id) is None or (
        hass.states.get(marker_row.entity_id).state == "unavailable"
    )


# --------------------------------------------------------------- the alarm


async def test_the_alarm_the_panel_prints_becomes_a_sensor_and_an_episode(hass, stubs):
    """From the header icon to Senaste larm and the binary's episodes, and back.

    The alarm is read off the harvest, so it costs the panel nothing, and it
    is concluded once per round with the outdoor temperature Modbus had then
    (7.2 °C in the stand-in). A sensor alarm leaves the heat pump running, so
    the binary itself, which reads the Modbus status, stays off while the log
    holds the open episode; when a round shows no alarm the episode is closed
    and the sensor keeps the alarm with "pågår: nej".
    """
    ServingPanel.vars = [72, 215, 9999, 9999]
    ServingPanel.header = [E017]
    entry = await _set_up_with_the_page(hass)
    # the first harvest is planned, not run in set-up (R6)
    await _advance(hass, MIN_FIRST_DELAY + 1)

    last = hass.states.get(_entity_id(hass, "sensor", "last_alarm"))
    assert last.state == E017
    assert last.attributes["kod"] == "E017"
    assert last.attributes["text"] == "Givare solpaneler ut"
    assert last.attributes["pågår"] == "ja" and last.attributes["slut"] is None
    assert last.attributes["utetemperatur vid start"] == 7.2
    assert last.attributes["start"]

    alarm = hass.states.get(_entity_id(hass, "binary_sensor", "alarm"))
    assert alarm.state == "off", "status 0 i Modbus: pumpen går, larmet är en givare"
    (episode,) = alarm.attributes["episoder"]
    assert episode["kod"] == "E017" and episode["slut"] is None and episode["utetemperatur"] == 7.2

    # The panel shows it, so the display's alarm is on, confirmed, with the text as printed.
    display = hass.states.get(_entity_id(hass, "binary_sensor", "display_alarm"))
    assert display.state == "on"
    assert display.attributes["kod"] == "E017" and display.attributes["visat"] == E017
    assert display.attributes["bekräftad"] is True and display.attributes["confirmed"] is True
    assert display.attributes["last_confirmed"]

    # The next round reads every page that can show an alarm and sees none: the episode
    # is closed, the sensor keeps it, and the display's alarm is off.
    ServingPanel.header = []
    await _harvest_again(hass, entry)
    last = hass.states.get(_entity_id(hass, "sensor", "last_alarm"))
    assert last.state == E017
    assert last.attributes["pågår"] == "nej" and last.attributes["slut"]
    alarm = hass.states.get(_entity_id(hass, "binary_sensor", "alarm"))
    assert alarm.attributes["episoder"][0]["slut"]
    display = hass.states.get(_entity_id(hass, "binary_sensor", "display_alarm"))
    assert display.state == "off" and display.attributes["kod"] is None

    # And the same alarm again is a new episode, newest first.
    ServingPanel.header = [E017]
    await _harvest_again(hass, entry)
    alarm = hass.states.get(_entity_id(hass, "binary_sensor", "alarm"))
    assert [e["slut"] is None for e in alarm.attributes["episoder"]] == [True, False]
    assert hass.states.get(_entity_id(hass, "binary_sensor", "display_alarm")).state == "on"


async def test_without_a_display_page_there_is_no_alarm_sensor(hass, stubs):
    entry = await _set_up(hass)
    assert entry.state is ConfigEntryState.LOADED
    assert (
        er.async_get(hass).async_get_entity_id("sensor", DOMAIN, f"{DOMAIN}_{HOST}_last_alarm")
        is None
    )
    alarm = hass.states.get(_entity_id(hass, "binary_sensor", "alarm"))
    assert "episoder" not in alarm.attributes
    # The display's alarm is made only where the log is, and so is the sensor's
    # registry entry.
    assert (
        er.async_get(hass).async_get_entity_id(
            "binary_sensor", DOMAIN, f"{DOMAIN}_{HOST}_display_alarm"
        )
        is None
    )
