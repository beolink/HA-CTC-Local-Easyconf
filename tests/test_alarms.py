"""Alarms with their E-code, read off the harvest without a step of the panel (R30).

Modbus says only "Av, larm". The display prints the alarm itself, "[E017]
Givare solpaneler ut", in the header icon at the top left of a page and in
the status field of a heat pump page, both of which the panel fills from a
variable. These tests hold the reading to its promises: the two places and
nothing else, and only where the panel picks the text by a variable, so a
caption the screen fixes can never pass for the alarm, and a page of past
alarms cannot pass one off as current; resolved from the values the harvest
has just read, against the cached screen definition and the text cache, so
the panel is never walked or asked for anything beyond the harvest's own
reads; and ten episodes kept with code, start, end and the outdoor
temperature. A round closes an episode only when it reads every page that can
show an alarm, and an episode nothing has seen for a while is not confirmed.

The screens are the real screen 118 of an i255, served by the fake session of
test_text_cache.py, which counts every request, and the real screen 116 of
VSH's i255, the heat pump page that holds a fixed "[E050]" label on its header
icon, served from the fixtures captured on that panel.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
import pathlib

import pytest

from conftest import FIXTURES, load
import ha_stub
from test_seen import FakeStore
from test_text_cache import SCREEN, FakeResponse, FakeSession

alarms = load("alarms")
web_api = load("web_api")
const = load("const")

E017 = "[E017] Givare solpaneler ut"
E045 = "[E045] Högtryck"
#: The label VSH's heat pump page 19 fixes on its header icon, which the panel
#: never picks by a variable; see VSHPage19Session.
E050 = "[E050] Stopp, låg överhettning exp.ventil"

#: Where screen 118 keeps what the test needs: the header icon picks between
#: two catalogue entries by variable 81, the status field between thirty two
#: by variable 36, and the caption to its left is catalogue entry 970.
HEADER_VAR, HEADER_PLAIN, HEADER_ALARM = 81, 284, 285
STATUS_VAR, STATUS_CAPTION = 36, 970
STATUS_PLAIN, STATUS_ALARM_ORDINAL, STATUS_ALARM = 497, 7, 489

#: VSH's heat pump page: its screen, and the catalogue entries the test reads
#: there: the fixed label on the header icon, the Status caption and the value
#: the status field shows.
VSH_PAGE_19 = 116
VSH_LABEL, VSH_STATUS_CAPTION, VSH_STATUS_VALUE = 233, 1391, 1394


def run(coro):
    return asyncio.run(coro)


def now() -> datetime:
    return datetime(2026, 10, 9, 12, 0, tzinfo=UTC)


def texts_of(client, screen: int, values: list) -> list[str]:
    return run(client.async_alarm_readout(screen, values)).texts


# ------------------------------------------------------------------ parsing


def test_an_alarm_text_is_split_into_code_and_text():
    assert alarms.parse_alarm(E017) == ("E017", "Givare solpaneler ut")
    assert alarms.parse_alarm("  [E045]Högtryck ") == ("E045", "Högtryck")
    assert alarms.parse_alarm("[E12]") == ("E12", "")


def test_information_texts_captions_and_nothing_are_no_alarm():
    for text in ("[I003] Avfrostning", "Status", "VV-tank", "Dansk", "", None, "E017 utan hakar"):
        assert alarms.parse_alarm(text) is None, text
    assert alarms.alarm_text(["VV-tank", "Från, startklar"]) is None
    assert alarms.alarm_text([]) is None


def test_the_first_alarm_among_the_candidates_wins_as_the_panel_printed_it():
    assert alarms.alarm_text(["VV-tank", f" {E017} ", E045]) == E017
    assert alarms.alarm_text([None, "", "[I001] Info", E045]) == E045


# ---------------------------------------------- the readout off a screen


class CatalogueSession(FakeSession):
    """Serves screen 118 and gives the few catalogue entries that matter real words."""

    TEXTS = {
        HEADER_PLAIN: "VV-tank",
        HEADER_ALARM: E017,
        STATUS_CAPTION: "Status",
        STATUS_PLAIN: "Från, startklar",
        STATUS_ALARM: E045,
    }

    async def get(self, url, timeout=None):
        response = await super().get(url, timeout)
        if "/txt/" in url:
            text_id = int(url.rsplit("/", 1)[1])
            if text_id in self.TEXTS:
                return type(response)(self.TEXTS[text_id].encode())
        return response


@pytest.fixture()
def display():
    session = CatalogueSession()
    # A documentation address: nothing is ever sent, the session is fake.
    client = web_api.CtcWebClient(session, "192.0.2.10")
    values = web_api.parse_vars((FIXTURES / "vars_118.txt").read_text(encoding="utf-8"))
    assert values[HEADER_VAR] == 0 and values[STATUS_VAR] == 28, "fixturen som den fångades"
    return client, session, values


def test_the_header_icon_comes_first_and_the_status_field_second(display):
    client, _session, values = display
    assert texts_of(client, SCREEN, values) == ["VV-tank", "Från, startklar"]


def test_the_readout_costs_no_reading_of_the_panel_and_nothing_twice(display):
    client, session, values = display
    run(client.async_alarm_readout(SCREEN, values))
    assert session.requests_for("/wp/") == 1, "skärmdefinitionen en gång"
    assert session.requests_for("/vars/") == 0, "värdena är skördens, inga egna"
    assert session.requests_for("/click/") == 0, "panelen flyttas aldrig"
    # Only the header icon, the captions beside the variable texts and the
    # status text are looked up, not the screen's ninety odd labels.
    texts = session.requests_for("/txt/")
    assert 3 <= texts <= 12, texts
    # Warm: the same values again cost nothing at all.
    before = len(session.calls)
    run(client.async_alarm_readout(SCREEN, values))
    assert len(session.calls) == before


def test_a_new_status_text_costs_one_catalogue_entry_and_no_more(display):
    client, session, values = display
    run(client.async_alarm_readout(SCREEN, values))
    before = len(session.calls)
    changed = list(values)
    changed[STATUS_VAR] = STATUS_ALARM_ORDINAL
    assert texts_of(client, SCREEN, changed) == ["VV-tank", E045]
    assert session.calls[before:] == [f"/txt/1/{STATUS_ALARM}"]


def test_an_alarm_in_the_header_icon_is_read_off_the_fresh_values(display):
    client, _session, values = display
    alarmed = list(values)
    alarmed[HEADER_VAR] = 1
    texts = texts_of(client, SCREEN, alarmed)
    assert texts == [E017, "Från, startklar"]
    assert alarms.alarm_text(texts) == E017


def test_an_alarm_in_the_status_field_is_found_behind_a_plain_header(display):
    client, _session, values = display
    alarmed = list(values)
    alarmed[STATUS_VAR] = STATUS_ALARM_ORDINAL
    assert alarms.alarm_text(texts_of(client, SCREEN, alarmed)) == E045


def _definition(rows, index: int = 900):
    """A screen with one text per row: (kind, x, y, selector kind, variable, group).

    Every geometry is a literal. A row's text is picked by ``selector kind``:
    2 a literal, the caption case, 0 a global and 1 the screen's own variable.
    The group is the catalogue entries the selector picks among.
    """
    v0: list[int] = []
    c1: list[list] = []
    t0: list[int] = []
    t1: list[list[int]] = []

    def literal(value: int) -> int:
        v0.extend([2, value])
        return len(v0) // 2 - 1

    for kind, x, y, selector, variable, group in rows:
        xr, yr, wr, hr, vis = literal(x), literal(y), literal(120), literal(28), literal(1)
        t1.append([item for text_id in group for item in (3, text_id)])
        slot = len(t0) // 3
        t0.extend([selector, variable, len(t1) - 1])
        entry: list = [kind, f"w{len(c1)}", xr, yr, wr, hr, 0, 0, vis, 0, 0, 0, 0]
        entry[10 if kind in (0, 1) else 12] = slot
        c1.append(entry)
    return web_api.ScreenDef(index=index, c0=[], c1=c1, v0=v0, t0=t0, t1=t1, t2=[])


class WordSession(FakeSession):
    """A catalogue that answers with the words the test gives it, else "Text n"."""

    def __init__(self, words):
        super().__init__()
        self.words = words

    async def get(self, url, timeout=None):
        response = await super().get(url, timeout)
        if "/txt/" in url:
            text_id = int(url.rsplit("/", 1)[1])
            return type(response)(self.words.get(text_id, f"Text {text_id}").encode())
        return response


def _client_for(definition, words):
    session = WordSession(words)
    client = web_api.CtcWebClient(session, "192.0.2.10")
    client._screen_cache[definition.index] = definition
    return client, session


def test_a_label_the_screen_fixes_on_the_header_icon_is_no_alarm_and_no_place_for_one():
    # The header icon's caption is a literal, so the panel shows that label
    # whatever the plant does: it is not an alarm, and the screen has no place
    # the panel fills with one.
    label = 233
    definition = _definition([(1, 5, 4, 2, 0, [label])])
    client, _session = _client_for(definition, {label: E050})
    reading = run(client.async_alarm_readout(900, [1]))
    assert reading == web_api.AlarmReading(False, [])


def test_a_page_of_past_alarms_passes_none_of_them_off_as_current():
    # Every row a date on the left and the alarm text on the right, picked by
    # a variable as the status field is. Nothing captioned Status: nothing read.
    definition = _definition([
        (3, 5, 55, 2, 0, [700]),
        (3, 200, 55, 1, 0, [801, 802]),
        (3, 5, 83, 2, 0, [701]),
        (3, 200, 83, 1, 1, [801, 802]),
    ])
    client, session = _client_for(definition, {700: "2026-10-01", 701: "2026-09-12",
                                               801: E017, 802: E045})
    assert texts_of(client, 900, [0, 1]) == []
    assert session.requests_for("/txt/") == 2, "bara raderna till vänster slogs upp"


def test_only_the_status_row_is_read_among_the_variable_texts():
    definition = _definition([
        (1, 5, 4, 1, 0, [10, 11]),            # the header icon
        (3, 5, 55, 2, 0, [20]),               # "Status"
        (3, 195, 55, 1, 1, [30, 31]),         # the status field
        (3, 5, 83, 2, 0, [21]),               # "Modell"
        (3, 195, 83, 1, 2, [40, 41]),         # the model, never read
        (3, 5, 111, 2, 0, [22]),              # a caption on a row with no variable text
    ])
    words = {10: "Dansk", 11: E017, 20: "Status", 21: "Modell", 30: "Till värme",
             31: E045, 40: "EA720M", 41: E045}
    client, session = _client_for(definition, words)
    assert texts_of(client, 900, [0, 0, 1]) == ["Dansk", "Till värme"]
    assert session.requests_for("/txt/1/41") == 0 and session.requests_for("/txt/1/40") == 0
    assert texts_of(client, 900, [1, 1, 1]) == [E017, E045]


def test_a_hidden_header_icon_or_one_elsewhere_is_not_a_candidate():
    hidden = _definition([(1, 5, 4, 1, 0, [10, 11])])
    hidden.v0[hidden.c1[0][8] * 2 + 1] = 0
    client, _ = _client_for(hidden, {11: E017})
    assert texts_of(client, 900, [1]) == []
    elsewhere = _definition([(1, 350, 55, 1, 0, [10, 11])])   # an icon in the schematic
    client, _ = _client_for(elsewhere, {11: E017})
    assert texts_of(client, 900, [1]) == []


def test_a_hidden_header_icon_still_says_the_screen_has_a_place_for_an_alarm():
    hidden = _definition([(1, 5, 4, 1, 0, [10, 11])])
    hidden.v0[hidden.c1[0][8] * 2 + 1] = 0
    client, _ = _client_for(hidden, {11: E017})
    assert run(client.async_alarm_readout(900, [1])) == web_api.AlarmReading(True, [])


def test_a_text_picked_by_a_global_variable_reads_as_none_rather_than_wrong():
    # The globals are not fetched for this, so such a text cannot be resolved;
    # it is left out, not guessed, and no request is made for the globals.
    definition = _definition([(1, 5, 4, 0, 0, [10, 11])])
    client, session = _client_for(definition, {10: "VV-tank", 11: E017})
    assert texts_of(client, 900, [1]) == []
    assert session.requests_for("/vars/") == 0


class VSHPage19Session(FakeSession):
    """VSH's i255 on heat pump page 19: screen 116 as the panel answered it, from the fixtures."""

    WORDS = {VSH_LABEL: E050, VSH_STATUS_CAPTION: "Status", VSH_STATUS_VALUE: "Värme"}

    async def get(self, url, timeout=None):
        path = "/" + url.split("//", 1)[1].split("/", 1)[1]
        self.calls.append(path)
        if path == f"/wp/{VSH_PAGE_19}":
            return FakeResponse((FIXTURES / "alarm_label_116.js").read_bytes())
        if path == f"/vars/{VSH_PAGE_19}":
            return FakeResponse((FIXTURES / "alarm_label_vars_116.txt").read_bytes())
        if path.startswith("/txt/"):
            text_id = int(path.rsplit("/", 1)[1])
            return FakeResponse(self.WORDS.get(text_id, f"Text {text_id}").encode())
        return FakeResponse(b"", 404)


@pytest.fixture()
def vsh_page_19():
    session = VSHPage19Session()
    client = web_api.CtcWebClient(session, "192.0.2.10")
    values = web_api.parse_vars((FIXTURES / "alarm_label_vars_116.txt").read_text(encoding="utf-8"))
    return client, session, values


def test_the_label_the_panel_fixes_on_vsh_heat_pump_page_is_no_alarm(vsh_page_19):
    # Live on 2026-10-20: "[E050] Stopp, låg överhettning exp.ventil" is fixed on
    # the header icon of this page, and the panel shows no alarm there. The
    # readout used to take the label, so an open E050 never closed.
    client, _session, values = vsh_page_19
    assert run(client.async_text(VSH_LABEL, language=1)) == E050
    reading = run(client.async_alarm_readout(VSH_PAGE_19, values))
    assert alarms.alarm_text(reading.texts) is None
    assert reading.texts == ["Värme"], "statusfältet läses, rubriken inte"
    assert reading.carries, "sidan har en plats där panelen skriver ett larm"


# ------------------------------------------------------------- the watch


class FakeClient:
    """Reads the alarm texts per screen the way the client does, and counts what it was asked.

    A screen listed in ``by_screen`` is one that has a place the panel fills
    with an alarm; the list is what that place says now.
    """

    def __init__(self, by_screen, broken=()):
        self.by_screen = dict(by_screen)
        self.broken = set(broken)
        self.asked: list[int] = []

    async def async_alarm_readout(self, screen, values):
        self.asked.append(screen)
        if screen in self.broken:
            raise web_api.CtcWebError(f"/wp/{screen} timed out")
        if screen not in self.by_screen:
            return web_api.AlarmReading(False, [])
        return web_api.AlarmReading(True, list(self.by_screen[screen]))


def _page(number, screens, content):
    return const.SlowPage(
        page=number, title=f"Sida {number}", screens=list(screens),
        values=[const.SlowValue(key=f"p{number}_rad", label="Rad", page=number,
                                screen=content, fmt="%d", var_indices=[0])],
    )


def _round(watch, log, client, pages, when, outdoor=7.0):
    """One harvest round: every page read is noted, then the log is told, as at runtime."""
    for page in pages:
        run(watch.async_note(client, page, {screen: [0] for screen in page.screens}, when))
    seen = watch.fresh()
    if seen.read:
        log.note(seen.shown, when, outdoor, complete=seen.complete)
    return seen


def test_the_watch_reads_only_the_screens_that_carry_a_row():
    client = FakeClient({220: ["VV-tank"], 1: [E017]})
    watch = alarms.AlarmWatch()
    page = _page(22, [1, 0, 220], 220)
    values = {1: [0], 0: [0], 220: [0]}
    assert run(watch.async_note(client, page, values, now())) is None
    assert client.asked == [220], "kromskärmarna 1 och 0 läses inte"


def test_the_watch_hands_out_what_was_read_since_it_was_last_asked():
    client = FakeClient({220: [E017], 230: ["Dansk"]})
    watch = alarms.AlarmWatch([22, 23])
    first = now()
    run(watch.async_note(client, _page(22, [220], 220), {220: [0]}, first))
    run(watch.async_note(client, _page(23, [230], 230), {230: [0]}, first))
    assert watch.fresh() == (True, E017, True)
    assert watch.fresh() == (False, None, False), "inget nytt sedan sist"
    # Next round only the page without the alarm is reached: that is a round
    # without an alarm, but not one that reads every page, so it closes nothing.
    run(watch.async_note(client, _page(23, [230], 230), {230: [0]}, first + timedelta(minutes=30)))
    assert watch.fresh() == (True, None, False)


def test_a_screen_that_would_not_give_its_definition_leaves_the_page_unnoted():
    client = FakeClient({220: [E017]}, broken={220})
    watch = alarms.AlarmWatch([22])
    assert run(watch.async_note(client, _page(22, [220], 220), {220: [0]}, now())) is None
    assert watch.fresh() == (False, None, False)
    # And a screen whose values the harvest did not get is not asked at all.
    client = FakeClient({220: [E017]})
    assert run(watch.async_note(client, _page(22, [220], 220), {}, now())) is None
    assert client.asked == []


def test_a_round_that_reads_every_alarm_page_and_sees_none_closes_the_episode():
    client = FakeClient({220: [E017]})
    watch = alarms.AlarmWatch([22, 30])
    log = alarms.AlarmLog(FakeStore())
    pages = [_page(22, [220], 220), _page(30, [300], 300)]
    start = now()
    assert _round(watch, log, client, pages, start) == (True, E017, True)
    assert log.active and log.latest["code"] == "E017"
    client.by_screen[220] = []
    assert _round(watch, log, client, pages, start + timedelta(minutes=30)) == (True, None, True)
    assert not log.active
    assert log.latest["end"] == "2026-10-09T12:30:00+00:00"


def test_a_page_judged_to_carry_no_alarm_is_not_needed_for_a_round_to_close_an_episode():
    client = FakeClient({220: [E017]})
    watch = alarms.AlarmWatch([22, 30])
    log = alarms.AlarmLog(FakeStore())
    pages = [_page(22, [220], 220), _page(30, [300], 300)]
    _round(watch, log, client, pages, now())
    client.by_screen[220] = []
    # The history page carries no alarm place and was judged so: the page the
    # round reads, the alarm page, is all a clean round needs.
    seen = _round(watch, log, client, [pages[0]], now() + timedelta(minutes=30))
    assert seen == (True, None, True)
    assert not log.active


def test_a_round_that_misses_an_alarm_page_keeps_the_episode_and_does_not_confirm_it():
    client = FakeClient({220: [E017]})
    watch = alarms.AlarmWatch([22, 30])
    log = alarms.AlarmLog(FakeStore())
    pages = [_page(22, [220], 220), _page(30, [300], 300)]
    start = now()
    _round(watch, log, client, pages, start)
    client.by_screen[220] = []
    # Only the page without an alarm place is read, and it shows none: no
    # round has read every page that can show one, so the episode stays.
    seen = _round(watch, log, client, [pages[1]], start + timedelta(minutes=30))
    assert seen == (True, None, False)
    assert log.active
    assert log.last_confirmed == start, "a round that read nothing of the alarm confirms nothing"
    assert not log.confirmed(start + timedelta(minutes=30))


def test_a_round_that_reads_no_page_says_nothing_about_the_episode():
    client = FakeClient({220: [E017]})
    watch = alarms.AlarmWatch([22])
    log = alarms.AlarmLog(FakeStore())
    start = now()
    _round(watch, log, client, [_page(22, [220], 220)], start)
    assert _round(watch, log, client, [], start + timedelta(hours=1)) == (False, None, False)
    assert log.active and log.latest["end"] is None


def test_after_a_restart_a_round_must_reach_every_page_before_it_may_close_an_episode():
    # Nothing judged yet: the restored episode is closed only by a round that
    # reads both pages, not by one that happens to reach the history page only.
    client = FakeClient({})
    watch = alarms.AlarmWatch([22, 30])
    log = alarms.AlarmLog(FakeStore())
    log.note(E017, now(), 7.0)
    pages = [_page(22, [220], 220), _page(30, [300], 300)]
    assert _round(watch, log, client, [pages[1]], now() + timedelta(hours=1)) == (True, None, False)
    assert log.active
    assert _round(watch, log, client, pages, now() + timedelta(hours=2)) == (True, None, True)
    assert not log.active


def test_the_first_alarm_among_the_pages_read_wins_in_page_order():
    client = FakeClient({220: [E017], 230: [E045]})
    watch = alarms.AlarmWatch([22, 23])
    run(watch.async_note(client, _page(22, [220], 220), {220: [0]}, now()))
    run(watch.async_note(client, _page(23, [230], 230), {230: [0]}, now()))
    assert watch.fresh().shown == E017


def test_the_real_label_on_vsh_page_19_closes_an_open_episode_after_a_clean_round(vsh_page_19):
    client, _session, values = vsh_page_19
    watch = alarms.AlarmWatch([19])
    log = alarms.AlarmLog(FakeStore())
    log.note(E050, now() - timedelta(hours=2), 7.0)
    assert log.active
    page = _page(19, [VSH_PAGE_19], VSH_PAGE_19)
    run(watch.async_note(client, page, {VSH_PAGE_19: values}, now()))
    seen = watch.fresh()
    assert seen == (True, None, True)
    log.note(seen.shown, now(), 7.0, complete=seen.complete)
    assert not log.active and log.latest["end"] == "2026-10-09T12:00:00+00:00"


# --------------------------------------------------------- the confirmation


def test_a_sighting_confirms_the_open_episode_for_the_window_only():
    log = alarms.AlarmLog(FakeStore(), confirm_within=timedelta(minutes=15))
    start = now()
    log.note(E017, start, 7.0)
    assert log.confirmed(start) and log.confirmed(start + timedelta(minutes=14))
    assert not log.confirmed(start + timedelta(minutes=15)), "the window is held to its edge"
    assert log.confirmed_until() == start + timedelta(minutes=15)


def test_a_later_sighting_moves_the_confirmation_and_a_round_without_one_does_not():
    log = alarms.AlarmLog(FakeStore(), confirm_within=timedelta(minutes=15))
    start = now()
    log.note(E017, start, 7.0)
    assert log.note(E017, start + timedelta(minutes=10), 7.0) is False, "samma larm, samma episod"
    assert log.last_confirmed == start + timedelta(minutes=10)
    assert log.confirmed(start + timedelta(minutes=20))
    log.note(None, start + timedelta(minutes=20), 7.0, complete=False)
    assert log.active and log.last_confirmed == start + timedelta(minutes=10)
    assert not log.confirmed(start + timedelta(minutes=30))


def test_closing_an_episode_takes_its_confirmation_with_it_and_a_restart_forgets_it():
    store = FakeStore()
    log = alarms.AlarmLog(store, confirm_within=timedelta(minutes=15))
    start = now()
    log.note(E017, start, 7.0)
    assert log.note(None, start + timedelta(minutes=5), 7.0, complete=True)
    assert log.last_confirmed is None and not log.confirmed(start + timedelta(minutes=5))

    log.note(E045, start + timedelta(hours=1), 7.0)
    again = alarms.AlarmLog(FakeStore(store.saves[-1][0]), confirm_within=timedelta(minutes=15))
    run(again.async_load())
    assert again.active and again.latest["code"] == "E045"
    assert again.last_confirmed is None, "ett omstart är ingen syn av larmet"
    assert not again.confirmed(start + timedelta(hours=1))


def test_the_window_is_at_least_a_harvest_and_a_margin_and_fifteen_minutes_for_a_short_one():
    assert alarms.confirm_window(1800) == timedelta(minutes=35), "standardhämtningen var 30:e minut"
    assert alarms.confirm_window(300) == timedelta(minutes=15)
    assert alarms.confirm_window(0) == timedelta(minutes=15)


def test_a_round_closes_nothing_unless_the_caller_says_it_read_every_alarm_page():
    log = alarms.AlarmLog(FakeStore())
    log.note(E017, now(), 7.0)
    assert log.note(None, now() + timedelta(hours=1), 7.0) is False
    assert log.active


# --------------------------------------------------------------- the log


def test_an_alarm_opens_an_episode_and_its_absence_closes_it():
    store = FakeStore()
    log = alarms.AlarmLog(store)
    start = now()
    assert log.note(E017, start, -3.4) is True
    assert log.active and log.latest["code"] == "E017" and log.latest["outdoor"] == -3.4
    assert log.latest["shown"] == E017 and log.latest["text"] == "Givare solpaneler ut"
    assert log.note(E017, start + timedelta(minutes=30), -4.0) is False, "samma larm, samma episod"
    assert len(log.episodes) == 1
    assert log.note(None, start + timedelta(hours=1), -4.0, complete=True) is True
    assert not log.active and log.latest["end"] == "2026-10-09T13:00:00+00:00"
    assert log.latest["start"] == "2026-10-09T12:00:00+00:00"
    assert store.saves and store.saves[-1][0]["episodes"][0]["code"] == "E017"
    assert store.saves[-1][1] == alarms.SAVE_DELAY_SECONDS


def test_another_code_ends_the_open_episode_and_begins_its_own():
    log = alarms.AlarmLog(FakeStore())
    log.note(E017, now(), 1.0)
    log.note(E045, now() + timedelta(minutes=30), 2.0)
    assert [e["code"] for e in log.episodes] == ["E045", "E017"], "nyast först"
    assert log.episodes[1]["end"] is not None and log.episodes[0]["end"] is None


def test_ten_episodes_are_kept_and_the_rest_fall_off():
    log = alarms.AlarmLog(FakeStore())
    when = now()
    for n in range(14):
        log.note(f"[E{n:03d}] Larm {n}", when + timedelta(hours=n), None)
    assert len(log.episodes) == const.ALARM_EPISODES == 10
    assert log.episodes[0]["code"] == "E013" and log.episodes[-1]["code"] == "E004"


def test_the_log_survives_a_restart_and_shrugs_at_rubbish():
    first = alarms.AlarmLog(FakeStore())
    first.note(E017, now(), 5.5)
    first.note(None, now() + timedelta(hours=2), 5.0, complete=True)
    saved = first._store.saves[-1][0]
    again = alarms.AlarmLog(FakeStore(saved))
    run(again.async_load())
    assert again.episodes == first.episodes
    assert again.attributes() == [{
        "kod": "E017", "text": "Givare solpaneler ut",
        "start": "2026-10-09T12:00:00+00:00", "slut": "2026-10-09T14:00:00+00:00",
        "utetemperatur": 5.5,
    }]
    for rubbish in ("not a dict", {"episodes": "no"}, {"episodes": [{"text": "no code"}, 7]}):
        broken = alarms.AlarmLog(FakeStore(rubbish))
        run(broken.async_load())
        assert broken.episodes == [] and broken.latest is None and not broken.active


# ------------------------------------------ the hook in the harvest loop


class FakeDisplay:
    """The client as the harvest sees it: one page, never moved, every call counted."""

    def __init__(self, vars_by_screen, candidates):
        self.panel = asyncio.Lock()
        self.vars_by_screen = vars_by_screen
        self.candidates = candidates
        self.calls: list[tuple] = []

    async def async_current_page(self):
        self.calls.append(("page",))
        return 22

    async def async_vars(self, screen):
        self.calls.append(("vars", screen))
        return list(self.vars_by_screen[screen])

    async def async_goto_page(self, target, route=None):
        self.calls.append(("goto", target))
        return target == 22

    async def async_click(self, *args):
        self.calls.append(("click", args))
        return []

    async def async_alarm_readout(self, screen, values):
        self.calls.append(("alarm", screen, tuple(values)))
        return web_api.AlarmReading(True, list(self.candidates))


@pytest.fixture()
def harvest():
    ha_stub.skip_unless_stubbed()
    coordinator = load("coordinator")

    def build(candidates):
        page = const.SlowPage(
            page=22, title="Värmepump", screens=[1, 0, 220],
            values=[
                const.SlowValue(key="p22_utetemperatur", label="Utetemperatur", page=22,
                                screen=220, fmt="%.1f", var_indices=[0], unit="°C", scale=0.1),
                const.SlowValue(key="p22_brine_in_ut_1", label="Brine in/ut 1", page=22,
                                screen=220, fmt="%.1f", var_indices=[1], unit="°C", scale=0.1),
            ],
        )
        # A second page the display never reaches, so the rule that skips a
        # round when the panel has been moved applies (it asks for two pages
        # until R8 lands).
        history = const.SlowPage(
            page=30, title="Historik", screens=[1, 0, 300],
            values=[const.SlowValue(key="p30_total_drifttid", label="Total drifttid",
                                    page=30, screen=300, fmt="%d", var_indices=[0], unit="h")],
        )
        display = FakeDisplay({1: [0], 0: [0], 220: [72, 9999]}, candidates)
        web = coordinator.CtcWebCoordinator(object(), display, [page, history], 1800)
        return web, display

    return build


def test_the_harvest_asks_once_per_content_screen_and_moves_nothing_for_it(harvest):
    web, display = harvest([E017])
    data = run(web._async_update_data())
    assert data == {"p22_utetemperatur": 7.2}, "sentinellen lämnar inget tal"
    asked = [call for call in display.calls if call[0] == "alarm"]
    assert asked == [("alarm", 220, (72, 9999))], "kromskärmarna frågas inte, värdena är skördens"
    assert [call for call in display.calls if call[0] == "click"] == []
    assert [call for call in display.calls if call[0] == "vars"] == [
        ("vars", 1), ("vars", 0), ("vars", 220)
    ], "inga egna avläsningar utöver skördens"
    seen = web.alarms.fresh()
    assert seen.read and seen.shown == E017
    # The history page is out of reach in this harvest, so the round is not
    # one that reads every page that can show an alarm.
    assert seen.complete is False


def test_a_round_without_an_alarm_says_so_and_a_skipped_round_says_nothing(harvest):
    web, display = harvest([])
    run(web._async_update_data())
    assert web.alarms.fresh() == (True, None, False)
    # The panel has been moved by somebody: the round is skipped and the
    # watch has nothing new to say, in either direction.
    web.data = {"p22_utetemperatur": 7.2}
    web._expected_page = 30
    display.candidates = [E017]
    run(web._async_update_data())
    assert web.alarms.fresh() == (False, None, False)


def test_the_watch_is_given_the_pages_of_the_harvest_it_judges():
    # The coordinator hands the watch its pages, so a page the harvest has never
    # reached still counts as one that may show an alarm.
    source = (pathlib.Path(__file__).resolve().parent.parent / "custom_components"
              / "ctc_ecozenith" / "coordinator.py").read_text(encoding="utf-8")
    assert "AlarmWatch([page.page for page in pages])" in source


def test_the_hook_is_one_line_in_the_harvest_loop():
    # Another track rewrites the harvest; the merge is meant to be one line.
    source = (pathlib.Path(__file__).resolve().parent.parent / "custom_components"
              / "ctc_ecozenith" / "coordinator.py").read_text(encoding="utf-8")
    assert source.count("self.alarms.async_note(") == 1
    assert source.count("AlarmWatch(") == 1
