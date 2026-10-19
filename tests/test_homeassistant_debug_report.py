"""The debug report under a real Home Assistant core: the switch, Configure, the wire.

test_debug_report.py works the rules and the sender on stand-ins. Here the
integration runs: the switch is a real entity on the real reload listener,
Configure is the real options flow, the store is Home Assistant's own and the
report goes through Home Assistant's shared session to a mocked server, built
by the real diagnostics. Switching the report on or off with the switch, or in
Configure as the only change, keeps the very Modbus client the entry was set
up with: no reload. A report goes when it is switched on, a moment after a
set-up while it is on, also while the entry waits for its controller, and
every half hour; it names neither the address, the MAC address nor the serial
number, its log is washed, the statistics' install id is reused or made in
their store, and an answer of 429, a refusal or a failure on the wire raises
nothing.

Shares the stand-ins and fixtures of test_homeassistant.py and runs the same
way, from a virtual environment that has Home Assistant and
pytest-homeassistant-custom-component installed:

    python -m pytest -o asyncio_mode=auto tests/test_homeassistant_debug_report.py
"""

from __future__ import annotations

from datetime import timedelta
import logging
import re
from unittest.mock import patch
import uuid

import pytest

pytest.importorskip("pytest_homeassistant_custom_component")

import aiohttp
from homeassistant.config_entries import ConfigEntryState
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers.json import json_dumps
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import async_fire_time_changed

from test_homeassistant import (  # noqa: F401  (the fixtures travel by import)
    HOST,
    IDENTITY,
    DeadModbus,
    FakeModbus,
    _entity_id,
    _needs_auto_asyncio_mode,
    _set_up,
    stubs_fixture,
)
from test_homeassistant_options import _defaults, _set_up_vsh

from custom_components.ctc_ecozenith import debug_report
from custom_components.ctc_ecozenith.const import (
    CONF_DEBUG_REPORT,
    CONF_FAST_INTERVAL,
    DOMAIN,
)

ENDPOINT = "https://stats.rnet.se/api/v1/diagnostics"
STATS_KEY = f"{DOMAIN}.stats"
SEQUENCE = re.compile(r"(?<!\d)0001(?!\d)")
KNOWN_ID = "6f1c2a7e-0d3b-4c8e-9a51-2b7d4e9f0c13"


@pytest.fixture(autouse=True)
def _a_short_start(monkeypatch):
    """The first report after a set-up a moment after it rather than two minutes, and the
    one for switching it on a moment after the write rather than five seconds."""
    monkeypatch.setattr(debug_report, "START_DELAY", timedelta(seconds=0.01))
    monkeypatch.setattr(debug_report, "SWITCHED_ON_DELAY", timedelta(seconds=0.02))


async def _settle(hass) -> None:
    await hass.async_block_till_done(wait_background_tasks=True)


async def _half_an_hour(hass) -> None:
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(minutes=31))
    await _settle(hass)


def _reports(aioclient_mock) -> list[dict]:
    return [
        payload for method, url, payload, _headers in aioclient_mock.mock_calls
        if method.lower() == "post" and str(url) == ENDPOINT
    ]


def _names_nothing(report: dict) -> str:
    """The report as Home Assistant's session writes it, naming no machine."""
    text = json_dumps(report)
    assert HOST not in text
    assert IDENTITY["mac"] not in text
    assert IDENTITY["serial"] not in text and IDENTITY["serial"].replace(" ", "") not in text
    assert not SEQUENCE.search(text), "löpnumret står kvar"
    assert "NaN" not in text
    return text


async def _switch(hass, service: str) -> None:
    await hass.services.async_call(
        "switch", service, {"entity_id": _entity_id(hass, "switch", "debug_report")},
        blocking=True,
    )
    await _settle(hass)


# ------------------------------------------------------------- the switch


async def test_off_by_default_the_switch_says_so_and_nothing_is_sent(hass, stubs, aioclient_mock):
    aioclient_mock.post(ENDPOINT, status=204)
    entry = await _set_up(hass)
    await _settle(hass)
    state = hass.states.get(_entity_id(hass, "switch", "debug_report"))
    assert state.state == "off"
    assert state.attributes["friendly_name"].endswith("Report all values to the developer")
    await _half_an_hour(hass)
    assert _reports(aioclient_mock) == []
    assert CONF_DEBUG_REPORT not in entry.options
    await hass.config_entries.async_unload(entry.entry_id)


async def test_the_switch_reports_at_once_and_reloads_nothing(
    hass, stubs, aioclient_mock, hass_storage
):
    aioclient_mock.post(ENDPOINT, status=204)
    entry = await _set_up(hass)
    runtime = entry.runtime_data
    (client,) = FakeModbus.instances
    before = dict(entry.options)

    await _switch(hass, "turn_on")
    assert entry.options == {**before, CONF_DEBUG_REPORT: True}, "bara alternativet skrivs"
    assert entry.state is ConfigEntryState.LOADED
    assert entry.runtime_data is runtime, "ingen omladdning"
    assert FakeModbus.instances == [client] and client.connected and client.closes == 0
    assert hass.states.get(_entity_id(hass, "switch", "debug_report")).state == "on"

    (report,) = _reports(aioclient_mock)
    assert report["schema"] == 1 and report["integration"] == DOMAIN
    assert report["diagnostics"]["entry"]["loaded"] is True
    assert report["diagnostics"]["entry"]["data"]["host"] == "**REDACTED**"
    _names_nothing(report)
    # Made in the statistics' own store, where they will find it.
    made = hass_storage[STATS_KEY]["data"]["install_id"]
    assert report["install_id"] == made and uuid.UUID(made).version == 4

    # A warning of the integration's own goes with the next report, washed.
    logging.getLogger(f"custom_components.{DOMAIN}.web_api").warning(
        "Cannot connect to host %s:80. %s: no answer", HOST, IDENTITY["mac"]
    )
    await _half_an_hour(hass)
    first, second = _reports(aioclient_mock)
    assert first["log"] == []
    (line,) = second["log"]
    assert line["level"] == "WARNING"
    assert line["logger"] == f"custom_components.{DOMAIN}.web_api"
    assert line["message"] == "Cannot connect to host **REDACTED**:80. **REDACTED**: no answer"
    _names_nothing(second)

    # Off again: no reload, and nothing more goes.
    await _switch(hass, "turn_off")
    assert entry.options[CONF_DEBUG_REPORT] is False
    assert entry.runtime_data is runtime and FakeModbus.instances == [client]
    await _half_an_hour(hass)
    assert len(_reports(aioclient_mock)) == 2
    await hass.config_entries.async_unload(entry.entry_id)


# -------------------------------------------------------------- Configure


@pytest.mark.parametrize("set_up", [_set_up, _set_up_vsh])
async def test_the_option_alone_in_configure_reloads_nothing(
    hass, stubs, aioclient_mock, set_up
):
    """Also for an entry never saved there, whose form fills in what it lacked."""
    aioclient_mock.post(ENDPOINT, status=204)
    entry = await set_up(hass)
    runtime = entry.runtime_data
    before = dict(entry.options)

    flow = await hass.config_entries.options.async_init(entry.entry_id)
    offered = _defaults(flow["data_schema"])
    assert offered[CONF_DEBUG_REPORT] is False
    result = await hass.config_entries.options.async_configure(
        flow["flow_id"], {**offered, CONF_DEBUG_REPORT: True}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await _settle(hass)
    assert entry.options == {**before, CONF_DEBUG_REPORT: True}
    assert entry.runtime_data is runtime and len(FakeModbus.instances) == 1, "ingen omladdning"
    assert len(_reports(aioclient_mock)) == 1
    assert hass.states.get(_entity_id(hass, "switch", "debug_report")).state == "on"

    # Saving it again unchanged writes nothing at all.
    flow = await hass.config_entries.options.async_init(entry.entry_id)
    offered = _defaults(flow["data_schema"])
    assert offered[CONF_DEBUG_REPORT] is True
    await hass.config_entries.options.async_configure(flow["flow_id"], offered)
    await _settle(hass)
    assert entry.runtime_data is runtime and len(FakeModbus.instances) == 1
    await hass.config_entries.async_unload(entry.entry_id)


async def test_another_option_beside_it_still_reloads(hass, stubs, aioclient_mock):
    aioclient_mock.post(ENDPOINT, status=204)
    entry = await _set_up(hass)
    flow = await hass.config_entries.options.async_init(entry.entry_id)
    offered = _defaults(flow["data_schema"])
    await hass.config_entries.options.async_configure(
        flow["flow_id"], {**offered, CONF_DEBUG_REPORT: True, CONF_FAST_INTERVAL: 45}
    )
    await _settle(hass)
    assert len(FakeModbus.instances) == 2, "intervallet kräver omladdningen"
    assert entry.runtime_data.modbus.update_interval == timedelta(seconds=45)
    # The set-up the reload brought reports a moment after it.
    assert len(_reports(aioclient_mock)) == 1
    await hass.config_entries.async_unload(entry.entry_id)


# ---------------------------------------------------------- set-up and wire


async def test_on_at_start_reports_and_every_half_hour_with_the_statistics_id(
    hass, stubs, aioclient_mock, hass_storage
):
    hass_storage[STATS_KEY] = {
        "version": 1, "minor_version": 1, "key": STATS_KEY, "data": {"install_id": KNOWN_ID},
    }
    aioclient_mock.post(ENDPOINT, status=204)
    entry = await _set_up(hass, **{CONF_DEBUG_REPORT: True})
    await _settle(hass)
    assert len(_reports(aioclient_mock)) == 1
    await _half_an_hour(hass)
    await _half_an_hour(hass)
    reports = _reports(aioclient_mock)
    assert len(reports) == 3
    assert {report["install_id"] for report in reports} == {KNOWN_ID}
    assert hass_storage[STATS_KEY]["data"] == {"install_id": KNOWN_ID}, "statistikens id står kvar"
    # Unloaded, it stops.
    await hass.config_entries.async_unload(entry.entry_id)
    await _half_an_hour(hass)
    assert len(_reports(aioclient_mock)) == 3


@pytest.mark.parametrize(
    "answer",
    [{"status": 429, "headers": {"Retry-After": "540"}}, {"status": 400}, {"status": 503},
     {"exc": aiohttp.ClientError("unreachable")}, {"exc": TimeoutError()}],
)
async def test_too_soon_a_refusal_or_a_failure_raises_nothing(hass, stubs, aioclient_mock, answer):
    aioclient_mock.post(ENDPOINT, **answer)
    entry = await _set_up(hass, **{CONF_DEBUG_REPORT: True})
    await _settle(hass)
    logging.getLogger(f"custom_components.{DOMAIN}.coordinator").warning("kept for later")
    await _half_an_hour(hass)
    assert entry.state is ConfigEntryState.LOADED
    reports = _reports(aioclient_mock)
    assert len(reports) == 2
    assert [line["message"] for line in reports[1]["log"]] == ["kept for later"]
    await hass.config_entries.async_unload(entry.entry_id)


async def test_an_entry_waiting_for_its_controller_reports_and_removal_stops_it(
    hass, stubs, aioclient_mock
):
    aioclient_mock.post(ENDPOINT, status=204)
    with patch(f"custom_components.{DOMAIN}.CtcModbusClient", DeadModbus):
        entry = await _set_up(hass, **{CONF_DEBUG_REPORT: True})
        await hass.async_block_till_done()
        assert entry.state is ConfigEntryState.SETUP_RETRY
        await _settle(hass)
        (report,) = _reports(aioclient_mock)
        assert report["diagnostics"]["entry"]["loaded"] is False
        _names_nothing(report)
        await hass.config_entries.async_remove(entry.entry_id)
        await _half_an_hour(hass)
    assert len(_reports(aioclient_mock)) == 1
    assert entry.entry_id not in hass.data.get(debug_report.DATA_KEY, {})
