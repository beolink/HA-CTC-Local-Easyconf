"""The coefficient of performance estimated from the display's two powers, under a real core.

The heat pump's operation page is harvested with the delivered heat and the
inverter's power on it, and the three estimate sensors are read off the sums the
integration keeps from those two rows, one harvest at a time (cop_estimate.py).
Here the sensors, their attributes and reasons, the store across a restart, the
store's removal with the entry, and the rule that a change of the new values
reloads nothing are held under Home Assistant itself.

The readings are stamped with the moment of the harvest, which is the real clock
unless it is held: the test holds the coordinator's clock and moves it half an
hour on for each harvest, so the sums integrate the time a heat pump would have.

Shares the stand-ins and fixtures of test_homeassistant.py and the answering
panel of test_homeassistant_harvest.py, and runs the same way, from a virtual
environment that has Home Assistant and pytest-homeassistant-custom-component
installed:

    python -m pytest tests/test_homeassistant_cop_estimate.py
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

pytest.importorskip("pytest_homeassistant_custom_component")

from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import CONF_HOST
from homeassistant.loader import async_get_integration
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry

from test_homeassistant import (  # noqa: F401  (the fixtures travel by import)
    HOST,
    IDENTITY,
    MODEL,
    _entity_id,
    _needs_auto_asyncio_mode,
    stubs_fixture,
)
from test_homeassistant_harvest import INTERVAL, LivePanel, _fire

import custom_components.ctc_ecozenith as integration
from custom_components.ctc_ecozenith import coordinator
from custom_components.ctc_ecozenith.catalogue import pages_to_storage
from custom_components.ctc_ecozenith.const import (
    CONF_IDENTITY,
    CONF_MENU,
    CONF_MENU_VERSION,
    CONF_MODBUS_PORT,
    CONF_SLAVE,
    CONF_SLOW_INTERVAL,
    CONF_SLOW_PAGES,
    CONF_WEB_PORT,
    DOMAIN,
    SlowPage,
    SlowValue,
)
from custom_components.ctc_ecozenith.harvest import MIN_FIRST_DELAY

PUMP = 22
PUMP_SCREEN = PUMP * 10
HEAT = "p22_avgiven_varme"
INVERTER = "p22_inverter_motoreffekt"
EVAPORATION = "p22_forangning_degc_bar_1"
CONDENSATION = "p22_kondensering_degc_bar_1"
#: The page answers in tenths: the delivered heat and the inverter's power in kilowatts,
#: then the evaporating and the condensing temperature in °C.
PANEL: dict[str, int] = {"heat": 24, "inverter": 20, "evaporation": 20, "condensation": 380}
#: The moment the readings are stamped with, moved on by _next_harvest.
CLOCK: dict[str, datetime] = {}

PAGES = [
    SlowPage(
        page=PUMP,
        title="Driftinfo Värmepump",
        screens=[PUMP_SCREEN],
        values=[
            SlowValue(
                key=HEAT, label="Avgiven värme", page=PUMP, screen=PUMP_SCREEN,
                fmt="%.1f", var_indices=[0], unit="kW", scale=0.1,
            ),
            SlowValue(
                key=INVERTER, label="Inverter Motoreffekt", page=PUMP, screen=PUMP_SCREEN,
                fmt="%.1f", var_indices=[1], unit="kW", scale=0.1,
            ),
        ],
        route=[(220, 255)],
    ),
]

#: The same page with the refrigerant circuit's two temperature rows on it as well,
#: as the panel prints them, a temperature and a pressure each (0.28.0).
CARNOT_PAGES = [
    SlowPage(
        page=PUMP,
        title="Driftinfo Värmepump",
        screens=[PUMP_SCREEN],
        values=[
            *PAGES[0].values,
            SlowValue(
                key=EVAPORATION, label="Förångning °C/bar 1", page=PUMP, screen=PUMP_SCREEN,
                fmt="%.1f", var_indices=[2], unit=None, scale=0.1,
            ),
            SlowValue(
                key=CONDENSATION, label="Kondensering °C/bar 1", page=PUMP, screen=PUMP_SCREEN,
                fmt="%.1f", var_indices=[3], unit=None, scale=0.1,
            ),
        ],
        route=[(220, 255)],
    ),
]


class PumpPanel(LivePanel):
    """The answering panel, with the heat pump's page reading what PANEL says."""

    async def async_vars(self, screen) -> list:
        if screen == PUMP_SCREEN:
            return [PANEL["heat"], PANEL["inverter"], PANEL["evaporation"], PANEL["condensation"]]
        return await super().async_vars(screen)


@pytest.fixture(autouse=True)
def pump_panel(stubs, monkeypatch):
    LivePanel.instances.clear()
    PANEL.update(heat=24, inverter=20, evaporation=20, condensation=380)
    CLOCK["now"] = dt_util.utcnow().replace(microsecond=0)
    monkeypatch.setattr(integration, "CtcWebClient", PumpPanel)
    monkeypatch.setattr(coordinator, "utcnow", lambda: CLOCK["now"])
    yield


async def _entry(hass, pages=PAGES) -> MockConfigEntry:
    stored = pages_to_storage(pages)
    version = str((await async_get_integration(hass, DOMAIN)).version)
    return MockConfigEntry(
        domain=DOMAIN,
        unique_id=f"{DOMAIN}_{HOST}",
        title=f"{MODEL} ({HOST})",
        data={
            CONF_HOST: HOST,
            CONF_MODBUS_PORT: 502,
            CONF_WEB_PORT: 80,
            CONF_SLAVE: 1,
            "model": MODEL,
        },
        options={
            CONF_MENU_VERSION: version,
            CONF_IDENTITY: IDENTITY,
            CONF_MENU: stored,
            CONF_SLOW_PAGES: stored,
            CONF_SLOW_INTERVAL: INTERVAL,
        },
    )


async def _set_up(hass, entry: MockConfigEntry) -> None:
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.LOADED


async def _first_harvest(hass) -> None:
    await _fire(hass, MIN_FIRST_DELAY + 1)


async def _next_harvest(hass) -> None:
    """Half an hour on, and the harvest due at that moment: the readings are stamped with it."""
    CLOCK["now"] += timedelta(seconds=INTERVAL)
    await _fire(hass, INTERVAL + 10)


def _estimate(hass, span: str):
    return hass.states.get(_entity_id(hass, "sensor", f"cop_estimate_{span}"))


# ----------------------------------------------------------------- the sensors


async def test_the_three_sensors_come_up_and_the_first_harvest_is_a_reading_only(
    hass, hass_storage, stubs
):
    entry = await _entry(hass)
    await _set_up(hass, entry)
    await _first_harvest(hass)

    # One reading is a quotient at once: 2.4 kW of heat against 2.0 kW of electricity.
    now = _estimate(hass, "now")
    assert float(now.state) == pytest.approx(1.2)
    assert now.attributes["method"] == "integrerad effekt, approximation"
    assert now.attributes["heat_hours"] == 0.0
    assert now.attributes["electric_hours"] == 0.0
    assert now.attributes["read_at"] is not None
    assert "reason" not in now.attributes

    # One reading integrates no time, so the sums wait for the second one.
    for span in ("day", "lifetime"):
        state = _estimate(hass, span)
        assert state.state == "unknown", span
        assert state.attributes["reason"] == (
            "waiting for a second reading, the sums start at the next harvest"
        )
        assert state.attributes["skäl"] == (
            "väntar på en andra läsning, summeringen börjar vid nästa skörd"
        )


async def test_the_second_harvest_gives_the_day_and_the_lifetime_their_hours(
    hass, hass_storage, stubs
):
    entry = await _entry(hass)
    await _set_up(hass, entry)
    await _first_harvest(hass)
    await _next_harvest(hass)

    for span in ("day", "lifetime"):
        state = _estimate(hass, span)
        assert float(state.state) == pytest.approx(1.2), span
        assert state.attributes["method"] == "integrerad effekt, approximation"
        assert state.attributes["heat_hours"] == pytest.approx(0.5), span
        assert state.attributes["electric_hours"] == pytest.approx(0.5), span
        assert "reason" not in state.attributes and "skäl" not in state.attributes


async def test_a_pump_that_never_reports_heat_says_so_and_shows_no_figure(
    hass, hass_storage, stubs
):
    PANEL["heat"] = 0
    entry = await _entry(hass)
    await _set_up(hass, entry)
    await _first_harvest(hass)
    await _next_harvest(hass)

    for span in ("day", "lifetime", "now"):
        state = _estimate(hass, span)
        assert state.state == "unknown", span
        assert state.attributes["skäl"] == "pumpen anger ingen avgiven värme", span
        assert state.attributes["reason"] == "the heat pump reports no heat output", span


async def test_heat_without_electricity_is_implausible_and_no_figure(hass, hass_storage, stubs):
    PANEL["inverter"] = 0
    entry = await _entry(hass)
    await _set_up(hass, entry)
    await _first_harvest(hass)
    await _next_harvest(hass)

    for span in ("day", "lifetime", "now"):
        state = _estimate(hass, span)
        assert state.state == "unknown", span
        assert state.attributes["skäl"] == "orimligt värde", span
        assert state.attributes["reason"] == "implausible value", span


async def test_the_estimate_store_is_written_and_comes_back_after_a_restart(
    hass, hass_storage, stubs
):
    entry = await _entry(hass)
    await _set_up(hass, entry)
    await _first_harvest(hass)
    await _next_harvest(hass)
    estimate = entry.runtime_data.heat_estimate
    before = estimate.lifetime()
    await estimate.async_save()
    key = f"{DOMAIN}_{entry.entry_id}_cop_estimate"
    assert hass_storage[key]["data"]["hours"] == pytest.approx(0.5)

    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    again = entry.runtime_data.heat_estimate
    assert again is not estimate
    assert again.lifetime() == before
    assert float(_estimate(hass, "lifetime").state) == pytest.approx(1.2)


async def test_a_change_of_the_new_values_reloads_nothing(hass, hass_storage, stubs, monkeypatch):
    entry = await _entry(hass)
    await _set_up(hass, entry)
    reloads: list[str] = []
    reload_for_real = hass.config_entries.async_reload

    async def counting_reload(entry_id: str) -> bool:
        reloads.append(entry_id)
        return await reload_for_real(entry_id)

    monkeypatch.setattr(hass.config_entries, "async_reload", counting_reload)
    options = dict(entry.options)
    await _first_harvest(hass)
    await _next_harvest(hass)
    await _next_harvest(hass)

    # The sums have moved on twice, and nothing was asked of the entry.
    assert entry.runtime_data.heat_estimate.lifetime().hours == pytest.approx(1.0)
    assert reloads == []
    assert dict(entry.options) == options
    assert integration._takes_a_reload(entry, entry.runtime_data, entry.options) is False


async def test_removing_the_entry_takes_the_estimate_store_with_it(hass, hass_storage, stubs):
    entry = await _entry(hass)
    await _set_up(hass, entry)
    await _first_harvest(hass)
    await _next_harvest(hass)
    key = f"{DOMAIN}_{entry.entry_id}_cop_estimate"
    await entry.runtime_data.heat_estimate.async_save()
    assert key in hass_storage
    await hass.config_entries.async_remove(entry.entry_id)
    await hass.async_block_till_done()
    assert key not in hass_storage


# --------------------------------------------- the heat worked out from the refrigerant circuit


async def test_a_pump_that_reports_no_heat_gets_the_circuits_figure(hass, hass_storage, stubs):
    PANEL["heat"] = 0
    entry = await _entry(hass, CARNOT_PAGES)
    await _set_up(hass, entry)
    await _first_harvest(hass)
    await _next_harvest(hass)

    # 2.0 and 38.0 °C: half of Carnot, 0.5 x 311.15 / 36.0 = 4.32.
    for span in ("day", "lifetime", "now"):
        state = _estimate(hass, span)
        assert float(state.state) == pytest.approx(4.32), span
        assert state.attributes["method"] == "Carnot ur köldmediekretsen, approximation", span
        assert state.attributes["uncertainty"] == "±20 %", span
        assert "reason" not in state.attributes, span
    assert _estimate(hass, "day").attributes["heat_hours"] == pytest.approx(0.5)
    assert entry.runtime_data.refrigerant_rows is not None
    assert entry.runtime_data.carnot_estimate.lifetime().hours == pytest.approx(0.5)


async def test_a_pump_that_reports_heat_keeps_the_measured_figure_beside_the_circuit(
    hass, hass_storage, stubs
):
    entry = await _entry(hass, CARNOT_PAGES)
    await _set_up(hass, entry)
    await _first_harvest(hass)
    await _next_harvest(hass)

    for span in ("day", "lifetime", "now"):
        state = _estimate(hass, span)
        assert float(state.state) == pytest.approx(1.2), span
        assert state.attributes["method"] == "integrerad effekt, approximation", span
        assert "uncertainty" not in state.attributes, span
    # The circuit's sums are kept all the same, for the diagnostics.
    assert entry.runtime_data.carnot_estimate.lifetime().hours == pytest.approx(0.5)
