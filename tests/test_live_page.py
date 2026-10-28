"""The live page: the page the panel rests on, read by the minute without a press.

On 2026-10-26 the owner took "Driftinfo Värmepump" out of the harvested pages
to have the heat pump's two powers more often than every half hour, which only
took the page away: a display page is read by walking the panel there, and a
page nobody walks to is never read, so its rows went unavailable for good. The
display keeps only the page the panel shows fresh, so the powers follow the
machine by the minute only while the panel shows their page. The panel now
rests on one chosen page after every walk, and that page is read off it with
two GET requests and never a tap (coordinator.async_read_live_page).

Here the read and its cost, the skip while somebody uses the panel or a walk
holds it, the parking at the end of the walk with the walk's own taps, the
alarm note that never closes an episode, the set-up's offer, and an existing
installation that keeps its panel where it was. The coordinator runs against
the stub Home Assistant and the real client talks to the fake display of
tests/fake_panel.py, so a tap would be written down if one were made.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
import json
import re
from types import SimpleNamespace

import pytest

from conftest import COMPONENT, load
from fake_panel import FakePanel, caption, centre, heading, reading, tabs, tile
import ha_stub
from test_alarms import E017, FakeDisplay
from test_seen import FakeStore

HOME, ROOT, HEAT_PUMP, HISTORY = 1, 20, 22, 30
NOW = datetime(2026, 10, 26, 17, 21, tzinfo=UTC)
#: The i255's tabs under the operation data root, 240 wide each.
TAB = tabs([HEAT_PUMP, HISTORY], width=240)
TEXTS = {
    532: ("Driftinfo", "Operation data"),
    1001: ("Driftinfo översikt", "Operation data overview"),
    308: ("Driftinfo Värmepump", "Operation data heat pump"),
    312: ("Driftinfo, Historik", "Stored operation data"),
    1807: ("Avgiven värme (kW)", "Energy output (kW)"),
    1810: ("Tillförd effekt (kW)", "Power input (kW)"),
    1808: ("Avgiven värme totalt (kWh)", "Energy output total (kWh)"),
}


def run(coro):
    return asyncio.run(coro)


@pytest.fixture(scope="module")
def coordinator_module():
    ha_stub.skip_unless_stubbed()
    return load("coordinator")


def _row(const, page, screen, key, label, var, unit, fmt="%.1f", scale=0.1):
    return const.SlowValue(
        key=key, label=label, page=page, screen=screen, fmt=fmt, var_indices=[var],
        unit=unit, scale=scale,
    )


def heat_pump_page(const):
    return const.SlowPage(
        page=HEAT_PUMP, title="Driftinfo Värmepump", screens=[220], route=[centre(TAB[0])],
        values=[
            _row(const, HEAT_PUMP, 220, "p22_avgiven_varme", "Avgiven värme", 0, "kW"),
            _row(const, HEAT_PUMP, 220, "p22_tillford_effekt", "Tillförd effekt", 1, "kW"),
        ],
    )


def history_page(const):
    return const.SlowPage(
        page=HISTORY, title="Driftinfo, Historik", screens=[300], route=[centre(TAB[1])],
        values=[
            _row(const, HISTORY, 300, "p30_avgiven_varme_totalt", "Avgiven värme totalt", 0,
                 "kWh", fmt="%d", scale=1.0),
        ],
    )


def i255_panel() -> FakePanel:
    """An i255 with its home screen, the operation data root and two pages under it."""
    return FakePanel(
        pages={HOME: [10], ROOT: [200], HEAT_PUMP: [220], HISTORY: [300]},
        screens={
            10: tile(532, 100, ROOT),
            200: [heading(1001), *TAB],
            220: [
                heading(308),
                caption(1807, 60), reading("%.1f", 0, y=60),
                caption(1810, 90), reading("%.1f", 1, y=90),
            ],
            300: [heading(312), caption(1808, 60), reading("%d", 0, y=60)],
        },
        texts=TEXTS,
        parent={ROOT: HOME, HEAT_PUMP: ROOT, HISTORY: ROOT},
        home=HOME,
        start=HOME,
        values={220: [45, 12], 300: [22499]},
    )


@pytest.fixture()
def clock(monkeypatch, coordinator_module):
    """The coordinator's clock, held at NOW and moved by hand."""
    moments = [NOW]
    monkeypatch.setattr(coordinator_module, "utcnow", lambda: moments[0])

    def advance(seconds: float) -> datetime:
        moments[0] = moments[0] + timedelta(seconds=seconds)
        return moments[0]

    return advance


@pytest.fixture()
def house(coordinator_module, web_api, const, clock):
    """VSH's panel, the real client on it, and a coordinator with the heat pump page live."""

    def build(live_page=HEAT_PUMP):
        panel = i255_panel()
        client = panel.client(web_api)
        client.root = ROOT
        pages = [heat_pump_page(const), history_page(const)]
        web = coordinator_module.CtcWebCoordinator(None, client, pages, 1800, live_page=live_page)

        async def walk() -> dict:
            web.data = await web._async_update_data()
            return web.data

        return panel, client, web, walk

    return build


# ------------------------------------------------- the walk ends on the live page


def test_the_walk_reads_the_live_page_last_and_ends_on_it(house):
    panel, client, web, walk = house()

    async def scenario():
        data = await walk()
        assert data == {
            "p22_avgiven_varme": 4.5, "p22_tillford_effekt": 1.2,
            "p30_avgiven_varme_totalt": 22499.0,
        }
        assert web.pages_read == [HISTORY, HEAT_PUMP], "direktsidan läses sist"
        assert panel.page == HEAT_PUMP, "panelen vilar på direktsidan"
        assert web._expected_page == HEAT_PUMP
        # The taps are the walk's own: the tile, the history tab, back to the
        # root and the heat pump's tab. The last of them is the one that
        # reached the live page, so the parking itself pressed nothing.
        assert [press.landed for press in panel.presses] == [ROOT, HISTORY, ROOT, HEAT_PUMP]
        assert client.taps == 4

    run(scenario())


def test_without_a_live_page_the_walk_goes_back_home_as_before(house):
    # An installation from before the option: no live page in its options,
    # so the panel is put back on the home screen and nothing is read live.
    panel, client, web, walk = house(live_page=None)

    async def scenario():
        await walk()
        assert web.live_page is None
        assert panel.page == HOME, "tillbaka till hemskärmen som förut"
        assert web.pages_read == [HEAT_PUMP, HISTORY], "menyns ordning, som förut"
        requests = client.requests
        assert await web.async_read_live_page() is False
        assert client.requests == requests, "inget läses utan direktsida"

    run(scenario())


def test_a_live_page_that_is_not_harvested_is_no_live_page(house):
    _panel, _client, web, _walk = house(live_page=99)
    assert web.live_page is None


def test_after_giving_way_the_panel_goes_back_where_it_stood_and_is_parked_next_round(
    coordinator_module, web_api, const
):
    """The skip rule stands: somebody's page is respected, then the panel rests again."""
    from test_harvest_skips import HISTORY as ONE, HOME as ITS_HOME, OTHER, FakePanel as Moved

    page = const.SlowPage(page=ONE, title="Historik", screens=[ONE * 10], route=[(80, 255)],
                          values=[_row(const, ONE, ONE * 10, "p30_tillford", "Tillförd", 0,
                                       "kWh")])
    panel = Moved(web_api, start=ITS_HOME)
    web = coordinator_module.CtcWebCoordinator(None, panel, [page], 1800, live_page=ONE)

    async def scenario():
        web.data = await web._async_update_data()
        assert panel.moves == [ONE], "dit, och kvar där: ingen återställning hem"
        assert web._expected_page == ONE
        panel.page = OTHER
        for _ in range(const.HARVEST_SKIP_LIMIT):
            moves = len(panel.moves)
            web.data = await web._async_update_data()
            assert len(panel.moves) == moves, "panelen rörs inte medan någon använder den"
        # The round that goes ahead puts the panel back where the person left
        # it, as without a live page; the next round finds it there and parks it.
        web.data = await web._async_update_data()
        assert panel.moves[-2:] == [ONE, OTHER]
        web.data = await web._async_update_data()
        assert panel.moves[-1] == ONE and panel.page == ONE

    run(scenario())


# ------------------------------------------------- the read without a press


def test_the_live_page_is_read_with_two_requests_and_no_tap(house, clock):
    panel, client, web, walk = house()
    heard: list[int] = []

    async def scenario():
        await walk()
        web.async_add_listener(lambda: heard.append(1))
        pace, next_attempt, harvested = web.update_interval, web.next_attempt, web.last_harvest
        panel.values[220] = [50, 13]
        read_at = clock(60)
        before = len(panel.session.calls)
        taps = client.taps
        assert await web.async_read_live_page() is True
        assert panel.session.calls[before:] == [
            ("GET", "/vars/menu", None), ("GET", "/vars/220", None)
        ], "panelens läge och sidans skärm, inget annat"
        assert client.taps == taps and panel.presses[-1].landed == HEAT_PUMP
        assert web.data["p22_avgiven_varme"] == 5.0 and web.data["p22_tillford_effekt"] == 1.3
        assert web.read_at["p22_avgiven_varme"] == read_at
        assert web.read_at["p30_avgiven_varme_totalt"] == NOW, "historiksidan åldras som förut"
        assert heard == [1], "lyssnarna får veta, som efter ett varv"
        # The walk's own account and schedule stand.
        assert (web.update_interval, web.next_attempt, web.last_harvest) == (
            pace, next_attempt, harvested
        )
        assert web.pages_read == [HISTORY, HEAT_PUMP]
        assert (web.live_read_at, web.live_reads, web.live_skips, web.live_reason) == (
            read_at, 1, 0, None
        )
        assert web.live_panel_page == HEAT_PUMP and web.live_menu_vars == [HEAT_PUMP, 0, 0]

    run(scenario())


def test_nothing_is_read_while_the_panel_shows_another_page(house, clock):
    panel, client, web, walk = house()
    heard: list[int] = []

    async def scenario():
        await walk()
        web.async_add_listener(lambda: heard.append(1))
        told: list[str | None] = []
        stop = web.async_add_live_status_listener(lambda: told.append(web.live_reason))
        panel.page = HISTORY  # somebody opened the history page
        panel.values[220] = [50, 13]
        clock(60)
        before = len(panel.session.calls)
        taps = client.taps
        assert await web.async_read_live_page() is False
        assert panel.session.calls[before:] == [("GET", "/vars/menu", None)]
        assert client.taps == taps, "inget tryck: den som står vid panelen störs inte"
        assert web.data["p22_avgiven_varme"] == 4.5, "värdet bärs vidare"
        assert heard == []
        assert web.live_skips == 1 and web.live_panel_page == HISTORY
        assert web.live_reason == f"panelen visar sida {HISTORY}, inte sida {HEAT_PUMP}"
        assert web.live_read_at is None
        # The sensor's own channel hears of the try, with its reason.
        assert told == [f"panelen visar sida {HISTORY}, inte sida {HEAT_PUMP}"]
        # Back on the page, the next read goes through and the count starts over.
        panel.page = HEAT_PUMP
        assert await web.async_read_live_page() is True
        assert web.live_skips == 0 and web.live_reason is None and heard == [1]
        assert len(told) == 1, "en läsning som går igenom hörs genom de vanliga lyssnarna"
        stop()
        panel.page = HISTORY
        assert await web.async_read_live_page() is False
        assert len(told) == 1, "den som slutat lyssna hör inget mer"

    run(scenario())


def test_nothing_is_read_while_a_walk_holds_the_panel(house):
    panel, client, web, walk = house()

    async def scenario():
        await walk()
        before = len(panel.session.calls)
        async with client.panel:
            assert await web.async_read_live_page() is False
        assert panel.session.calls[before:] == [], "inte ett enda anrop under en bläddring"
        assert web.live_reason == "en bläddring håller panelen"
        assert web.live_skips == 1

    run(scenario())


def test_a_display_that_does_not_answer_costs_the_read_alone(house):
    panel, _client, web, walk = house()

    async def scenario():
        await walk()
        panel.silent = frozenset({"/vars/menu"})
        assert await web.async_read_live_page() is False
        assert web.live_reason.startswith("panelens läge kunde inte läsas")
        assert web.last_update_success, "varvets omdöme om displayen rörs inte"
        panel.silent = frozenset({"/vars/220"})
        assert await web.async_read_live_page() is False
        assert web.live_reason == "ingen skärm på sidan svarade"

    run(scenario())


# ------------------------------------------------- the alarm watch


def test_a_live_read_may_open_an_episode_but_never_closes_one(coordinator_module, const):
    """One page read between two walks is not the round that closes an episode."""
    alarms = load("alarms")
    page = const.SlowPage(
        page=HEAT_PUMP, title="Värmepump", screens=[1, 0, 220],
        values=[_row(const, HEAT_PUMP, 220, "p22_utetemperatur", "Utetemperatur", 0, "°C")],
    )
    display = FakeDisplay({"menu": [HEAT_PUMP, 0, 0], 1: [0], 0: [0], 220: [72]}, [])
    web = coordinator_module.CtcWebCoordinator(object(), display, [page], 1800,
                                               live_page=HEAT_PUMP)
    log = alarms.AlarmLog(FakeStore())

    def conclude(when):
        seen = web.alarms.fresh()
        if seen.read:
            log.note(seen.shown, when, 7.0, complete=seen.complete)
        return seen

    async def scenario():
        # A walk that sees nothing; the one page is the only one that can
        # print an alarm, so a walk's round is complete.
        web.data = await web._async_update_data()
        assert conclude(NOW) == (True, None, True)
        # The alarm appears between two walks: the live read opens the episode.
        display.candidates = [E017]
        assert await web.async_read_live_page()
        assert conclude(NOW + timedelta(minutes=1)) == (True, E017, False)
        assert log.active and log.latest["code"] == "E017"
        # It goes away: the live read sees none, but one page between two
        # walks cannot close the episode, even as the only alarm page.
        display.candidates = []
        assert await web.async_read_live_page()
        assert conclude(NOW + timedelta(minutes=2)) == (True, None, False)
        assert log.active, "episoden står tills ett varv har läst varje larmsida"
        # The next walk reads every page that can print one and closes it.
        web.data = await web._async_update_data()
        assert conclude(NOW + timedelta(minutes=30)) == (True, None, True)
        assert not log.active

    run(scenario())


def test_the_watch_marks_a_partial_note_and_only_a_whole_round_is_complete():
    from test_alarms import FakeClient, _page

    alarms = load("alarms")
    client = FakeClient({220: [E017]})
    watch = alarms.AlarmWatch([HEAT_PUMP])
    page = _page(HEAT_PUMP, [220], 220)
    run(watch.async_note(client, page, {220: [0]}, NOW))
    assert watch.fresh() == (True, E017, True)
    client.by_screen[220] = []
    run(watch.async_note(client, page, {220: [0]}, NOW + timedelta(minutes=1), partial=True))
    assert watch.fresh() == (True, None, False), "en delvis läsning sluter inget"
    run(watch.async_note(client, page, {220: [0]}, NOW + timedelta(minutes=30)))
    assert watch.fresh() == (True, None, True)


# ------------------------------------------------- the options and the set-up


def test_the_heat_pumps_page_is_the_default_live_page(const, cop):
    i255 = [heat_pump_page(const), history_page(const)]
    assert cop.live_page_default(i255) == HEAT_PUMP
    i550 = [
        const.SlowPage(page=42, title="Kompressor VP1", screens=[420], values=[
            _row(const, 42, 420, "p42_avgiven_varme", "Avgiven värme (kW)", 0, "kW"),
            _row(const, 42, 420, "p42_tillford_effekt", "Tillförd effekt (kW)", 1, "kW"),
        ]),
        const.SlowPage(page=30, title="Historisk driftinfo", screens=[300], values=[
            _row(const, 30, 300, "p30_tillford_energi_totalt", "Tillförd energi totalt", 0,
                 "kWh", fmt="%d", scale=1.0),
        ]),
    ]
    assert cop.live_page_default(i550) == 42
    # Without both powers there is nothing worth resting the panel on.
    assert cop.live_page_default([history_page(const)]) is None
    half = const.SlowPage(page=22, title="Värmepump", screens=[220], values=[
        _row(const, 22, 220, "p22_avgiven_varme", "Avgiven värme", 0, "kW"),
    ])
    assert cop.live_page_default([half]) is None


def test_what_the_forms_hand_over_is_read_against_the_pages(catalogue, const):
    pages = [heat_pump_page(const), history_page(const)]
    assert catalogue.live_page_among(HEAT_PUMP, pages) == HEAT_PUMP
    assert catalogue.live_page_among(99, pages) is None, "en sida som inte hämtas"
    assert catalogue.live_page_among(None, pages) is None
    assert catalogue.live_page_among(True, pages) is None
    assert catalogue.live_page_among("22", pages) is None
    assert catalogue.live_page_chosen("22", pages) == HEAT_PUMP
    assert catalogue.live_page_chosen(const.LIVE_PAGE_NONE, pages) is None
    assert catalogue.live_page_chosen(None, pages) is None
    assert catalogue.live_page_chosen("", pages) is None
    assert catalogue.live_page_chosen("99", pages) is None
    assert catalogue.live_page_chosen("x", pages) is None
    assert catalogue.live_interval_chosen(90, 60) == 90
    assert catalogue.live_interval_chosen(90.0, 60) == 90
    assert catalogue.live_interval_chosen(5, 60) == const.MIN_LIVE_INTERVAL, "golvet håller"
    assert catalogue.live_interval_chosen(None, 60) == 60
    assert catalogue.live_interval_chosen("x", 60) == 60
    assert const.DEFAULT_LIVE_INTERVAL == 60 and const.MIN_LIVE_INTERVAL == 30


FLOW = (COMPONENT / "config_flow.py").read_text(encoding="utf-8")
SETUP = (COMPONENT / "__init__.py").read_text(encoding="utf-8")


def _method(source: str, name: str) -> str:
    body = source.split(f"async def {name}(")[1]
    return re.split(r"\n    (?:async )?def |\n    @|\nasync def |\ndef ", body)[0]


def test_the_set_up_form_offers_the_heat_pumps_page_as_the_live_page():
    # config_flow.py imports Home Assistant, so the offer is held in the
    # source, as the other rules of the flow are; the function it offers is
    # judged above.
    slow = _method(FLOW, "async_step_slow")
    assert "live=(live_page_default(self._pages), DEFAULT_LIVE_INTERVAL)" in slow
    saved = slow.split("if user_input is not None:")[1].split("session = ")[0]
    assert "live = live_page_chosen(user_input.get(CONF_LIVE_PAGE), self._pages)" in saved
    assert "page.page in chosen or page.page == live" in saved, "direktsidan hämtas alltid"
    assert "CONF_LIVE_PAGE: live," in saved
    assert "CONF_LIVE_INTERVAL: live_interval_chosen(" in saved


def test_configure_offers_the_stored_live_page_and_suggests_none_to_an_existing_installation():
    init = _method(FLOW, "async_step_init")
    assert "live_page_default" not in init, "en befintlig anläggning parkeras inte av sig själv"
    assert "live_page_among(options.get(CONF_LIVE_PAGE), menu)" in init
    submitted = init.split("if user_input is not None:")[1].split("\n        options = ")[0]
    assert "CONF_LIVE_PAGE: live," in submitted
    assert "p.page in chosen or p.page == live" in submitted
    # The defaults table knows both, so an installation whose options lack
    # them is not reloaded over a save that changed nothing (debug_report).
    table = FLOW.split("FIRST_FORM_DEFAULTS: dict[str, Any] = {")[1].split("}")[0]
    assert "CONF_LIVE_PAGE: None," in table
    assert "CONF_LIVE_INTERVAL: DEFAULT_LIVE_INTERVAL," in table
    # The rescan form keeps the live page only while the menu offered has it.
    rescan = _method(FLOW, "async_step_rescan")
    assert "live = live_page_among(settings.get(CONF_LIVE_PAGE), self._pages)" in rescan
    assert "CONF_LIVE_PAGE: live," in rescan


def test_set_up_hands_the_coordinator_the_stored_live_page_and_runs_the_timer():
    assert "live_page=live_page_among(options.get(CONF_LIVE_PAGE), pages)," in SETUP
    timer = SETUP.split("if web.live_page is not None:")[1].split("\n    # What this")[0]
    assert "MIN_LIVE_INTERVAL, int(options.get(CONF_LIVE_INTERVAL, DEFAULT_LIVE_INTERVAL))" in timer
    assert "await web.async_read_live_page()" in timer
    assert 'if getattr(entry, "pref_disable_polling", False):' in timer
    assert (
        "entry.async_on_unload(\n"
        "                async_track_time_interval(hass, _read_live_page, "
        "timedelta(seconds=live_interval))"
    ) in timer, "klockan går med posten och stannar när den lastas ur"


def test_the_pump_dict_carries_the_live_page_for_the_source_line():
    dashboard = (COMPONENT / "dashboard.py").read_text(encoding="utf-8")
    assert '"live_page": getattr(runtime.web, "live_page", None),' in dashboard
    assert '"page": page.page,' in dashboard


# ------------------------------------------------- what the owner can see


def test_the_harvest_sensor_says_how_the_live_reads_go():
    ha_stub.install()
    sensor = load("sensor")
    web = SimpleNamespace(
        skips_in_a_row=0, patience=SimpleNamespace(failures=0), next_attempt=None,
        last_skip_reason=None, last_failure=None, pages_read=[HISTORY, HEAT_PUMP],
        pages_missed=[], last_harvest=NOW, live_page=HEAT_PUMP,
        live_read_at=NOW + timedelta(minutes=1), live_panel_page=HEAT_PUMP, live_skips=0,
        live_reason=None,
    )
    runtime = SimpleNamespace(web=web, device={"identifiers": {("ctc_ecozenith", "pump")}})
    attributes = sensor.CtcHarvestSensor(runtime).extra_state_attributes
    assert attributes["direktläst sida"] == HEAT_PUMP
    assert attributes["direktläsning senast"] == "2026-10-26T17:22:00+00:00"
    assert attributes["direktläsning panelen visade"] == HEAT_PUMP
    assert attributes["direktläsning uteblivna i rad"] == 0
    assert attributes["direktläsning skäl"] is None
    # Without a live page the account is the old one, nothing more.
    web.live_page = None
    attributes = sensor.CtcHarvestSensor(runtime).extra_state_attributes
    assert not [name for name in attributes if name.startswith("direkt")]


def test_the_harvest_sensor_follows_a_live_read_that_read_nothing():
    # A try that reads nothing tells the coordinator's listeners nothing, so
    # the sensor listens on the live status channel, or its attributes keep
    # the account of the last read that went through.
    source = (COMPONENT / "sensor.py").read_text(encoding="utf-8")
    body = source[source.index("class CtcHarvestSensor") : source.index("class CtcLastAlarmSensor")]
    assert "async_add_live_status_listener" in body
    assert "self.async_on_remove(add(self.async_write_ha_state))" in body


def test_the_diagnostics_carry_the_reads_beside_what_the_panel_answered():
    diagnostics_data = load("diagnostics_data")
    web = SimpleNamespace(
        interval=1800, last_update_success=True, patience=None, last_attempt=NOW,
        next_attempt=None, last_harvest=NOW, last_failure=None, pages_read=[HEAT_PUMP],
        pages_missed=[], skips_in_a_row=0, last_skip_reason=None, home_page=HOME,
        _expected_page=HEAT_PUMP, read_at={}, data={}, live_page=HEAT_PUMP, live_reads=3,
        live_read_at=NOW + timedelta(minutes=3), live_attempt=NOW + timedelta(minutes=3),
        live_skips=0, live_reason=None, live_panel_page=HEAT_PUMP,
        live_menu_vars=[HEAT_PUMP, 0, 0],
    )
    found = diagnostics_data.display_section(SimpleNamespace(web=web, web_client=None))
    assert found["live"] == {
        "page": HEAT_PUMP, "reads": 3, "last_read": NOW + timedelta(minutes=3),
        "last_attempt": NOW + timedelta(minutes=3), "skips_in_a_row": 0, "last_reason": None,
        "panel_page": HEAT_PUMP, "menu_vars": [HEAT_PUMP, 0, 0],
    }
    web.live_page = None
    found = diagnostics_data.display_section(SimpleNamespace(web=web, web_client=None))
    assert found["live"] is None


def test_the_source_line_says_a_live_row_is_read_live(explanations):
    pages = [{"title": "Driftinfo Värmepump", "page": HEAT_PUMP, "values": [
        {"key": "p22_avgiven_varme", "label": "Avgiven värme", "unit": "kW",
         "screen": 220, "row": 1},
    ]}]
    assert explanations.source("p22_avgiven_varme", pages, 1800, "sv", live=(HEAT_PUMP, 60)) == (
        "Displaysidan Driftinfo Värmepump, skärm 220, rad 1, direktläst var 60:e sekund medan "
        "panelen visar sidan, annars var 30:e minut"
    )
    assert explanations.source("p22_avgiven_varme", pages, 1800, "en", live=(HEAT_PUMP, 60)) == (
        "Display page Driftinfo Värmepump, screen 220, row 1, read live every 60 seconds "
        "while the panel shows it, else every 30 minutes"
    )
    # Another page, or no live page at all: the line as it was.
    assert explanations.source("p22_avgiven_varme", pages, 1800, "sv", live=(HISTORY, 60)) == (
        "Displaysidan Driftinfo Värmepump, skärm 220, rad 1, hämtas var 30:e minut"
    )
    assert explanations.source("p22_avgiven_varme", pages, 1800, "sv").endswith(
        "hämtas var 30:e minut"
    )


@pytest.mark.parametrize("name", ("strings.json", "translations/en.json", "translations/sv.json"))
def test_the_texts_name_both_options_and_the_none_choice(name):
    texts = json.loads((COMPONENT / name).read_text(encoding="utf-8"))
    for step in (texts["config"]["step"]["slow"], texts["options"]["step"]["init"]):
        assert step["data"]["live_page"] and step["data"]["live_interval"]
        assert step["data_description"]["live_page"] and step["data_description"]["live_interval"]
    assert texts["selector"]["live_page"]["options"]["none"]


def test_the_swedish_option_texts_use_no_dash_as_punctuation():
    texts = json.loads((COMPONENT / "translations/sv.json").read_text(encoding="utf-8"))
    said = [
        texts["options"]["step"]["init"]["data"]["live_page"],
        texts["options"]["step"]["init"]["data"]["live_interval"],
        texts["options"]["step"]["init"]["data_description"]["live_page"],
        texts["options"]["step"]["init"]["data_description"]["live_interval"],
        texts["selector"]["live_page"]["options"]["none"],
    ]
    for text in said:
        assert "–" not in text and "—" not in text and " - " not in text, text
    # The Swedish is the text itself, the English rides beside it.
    explanation = load("explanations").HARVEST["display_harvest"]
    assert "direktläst sida" in str(explanation) and "live page" in explanation.en
    assert "–" not in explanation and "—" not in explanation and " - " not in explanation
