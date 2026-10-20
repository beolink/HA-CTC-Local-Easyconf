"""The restore after a forced harvest never presses the home screen's button (F9.4, R8).

After two rounds given way the harvest goes ahead and puts the panel back on
the page it found it on, which may lie outside the operation data subtree.
Without a route the only way there is stepping back, and async_step_back_to
used to press the chrome's top right button on every page that was not the
target, the home screen included, where that button is not a back button but
the quick menu on an i550 Pro; the restore then asked for the same walk a
second time. Here the real client is driven over test_harvest_route's panel,
whose home screen opens a quick menu: stepping back stops on a home screen the
client has recognised, as _async_back_to_root and async_goto_home already
did, and the whole forced round presses nothing on the home screen but the
operation data tile.
"""

from __future__ import annotations

import asyncio

import pytest

from conftest import load
import ha_stub
from test_harvest_route import BACK, HOME, OUTSIDE, QUICK_MENU, ROOT, ROUTES, TILE, Panel

INTERVAL = 1800
PAGE = 21


def run(coro):
    return asyncio.run(coro)


# ----------------------------------------------------------- the client


def test_stepping_back_stops_on_a_recognised_home_screen_without_pressing(web_api):
    panel = Panel(web_api)
    client = panel.client
    client.root = ROOT
    assert run(client.async_goto_home()) == HOME, "hemskärmen känns igen på kaklet"

    # A target above the home screen cannot be reached by stepping back.
    panel.page = 23
    assert not run(client.async_step_back_to(OUTSIDE))
    assert panel.presses == [(23, BACK), (ROOT, BACK)]
    assert panel.page == HOME, "panelen lämnas på hemskärmen"
    assert BACK not in panel.pressed_on(HOME), "hemskärmens knapp trycks aldrig"
    assert panel.pressed_on(QUICK_MENU) == [], "snabbmenyn öppnas aldrig"

    # A target below is still reached the short way.
    panel.page = 23
    before = len(panel.presses)
    assert run(client.async_step_back_to(ROOT))
    assert panel.presses[before:] == [(23, BACK)]


# ------------------------------------------------------- the forced round


@pytest.fixture()
def harvest(web_api, const):
    """A coordinator over the real client, one page behind the root's first tab."""
    ha_stub.skip_unless_stubbed()
    coordinator_module = load("coordinator")
    panel = Panel(web_api)
    client = panel.client
    client.root = ROOT

    async def vars_(screen):
        return [123]

    async def no_alarm(screen, values):
        return web_api.AlarmReading(False, [])

    client.async_vars = vars_
    client.async_alarm_readout = no_alarm
    page = const.SlowPage(
        page=PAGE,
        title="Värmesystem",
        screens=[PAGE * 10],
        values=[
            const.SlowValue(
                key=f"p{PAGE}_utetemperatur",
                label="Utetemperatur",
                page=PAGE,
                screen=PAGE * 10,
                fmt="%.1f",
                var_indices=[0],
                unit="°C",
                scale=0.1,
            )
        ],
        route=list(ROUTES[PAGE]),
    )
    coordinator = coordinator_module.CtcWebCoordinator(None, client, [page], INTERVAL)

    async def round() -> dict | None:
        try:
            coordinator.data = await coordinator._async_update_data()
        except coordinator_module.UpdateFailed:
            return None
        return coordinator.data

    return panel, coordinator, round


def test_the_forced_round_puts_the_panel_on_home_without_pressing_its_button(harvest, const):
    panel, coordinator, round = harvest

    async def scenario():
        # An ordinary round from the home screen: over the tile, down the
        # route, read, and back to the home screen by stepping back.
        assert await round() == {f"p{PAGE}_utetemperatur": 12.3}
        assert panel.page == HOME
        assert coordinator.home_page == HOME
        assert coordinator._expected_page == HOME

        # Somebody parks the panel on a page outside the subtree, one step
        # above the home screen, and leaves it there.
        panel.page = OUTSIDE
        for skipped in range(1, const.HARVEST_SKIP_LIMIT + 1):
            presses = len(panel.presses)
            await round()
            assert len(panel.presses) == presses, "panelen rörs inte under någons händer"
            assert coordinator.skips_in_a_row == skipped

        # The round that goes ahead: in over the home screen's tile, read,
        # and back towards the parked page, which stepping back cannot reach
        # without pressing the home screen's own button. So it stops there,
        # once, and the panel is left on the home screen.
        before = len(panel.presses)
        assert await round() == {f"p{PAGE}_utetemperatur": 12.3}
        assert panel.presses[before:] == [
            (OUTSIDE, BACK),
            (HOME, TILE),
            (ROOT, ROUTES[PAGE][0]),
            (PAGE, BACK),
            (ROOT, BACK),
        ], "en väg in, en väg tillbaka, och inget tryck på hemskärmens knapp"
        assert panel.page == HOME
        assert coordinator._expected_page == HOME
        assert coordinator.skips_in_a_row == 0

    run(scenario())
    assert BACK not in panel.pressed_on(HOME), "hemskärmens knapp trycks aldrig"
    assert panel.pressed_on(HOME) == [TILE, TILE], "bara kaklet, en gång per skörd"
    assert panel.pressed_on(QUICK_MENU) == [], "snabbmenyn öppnas aldrig"
