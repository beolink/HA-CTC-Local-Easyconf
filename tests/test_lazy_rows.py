"""Rows that never leave a number get their entity when they do (L3).

The display answers for a sensor that is not fitted with a marker, 9999, so a
row that has only ever read the marker has been an entity that was
unavailable from the day it was made: four on VSH's i255, one on PT's i550
Pro. These tests hold the new rule: such a row waits for its entity until it
first reads as a number, a row that has read as a number once, a true zero
included, is made at once, the record of which rows have is kept in the seen
store under a new version with the old content carried over, and the
registry is tidied within one rule: a row gone from a page the menu still has,
never a page the menu does not know.
"""

from __future__ import annotations

import asyncio

import pytest

from conftest import load
import ha_stub
from test_seen import FakeStore

seen = load("seen")
rows = load("rows")
const = load("const")


def run(coro):
    return asyncio.run(coro)


# ---------------------------------------------------------- the seen store


def test_a_number_is_a_number_zero_included_and_text_is_not(seen=seen):
    assert seen.is_number(0) and seen.is_number(0.0) and seen.is_number(-3.5)
    assert not seen.is_number("0") and not seen.is_number("Värme")
    assert not seen.is_number(None) and not seen.is_number(True)


def test_what_read_as_a_number_is_remembered_true_zeros_included():
    store = FakeStore()
    tracker = seen.SeenValues(store)
    tracker.note({"p22_kompressor": 0.0, "p22_utetemperatur": 7.2, "system_status": "Värme",
                  "p22_brine_in_ut_1": None})
    assert tracker.numeric == {"p22_kompressor", "p22_utetemperatur"}
    assert tracker.keys == {"p22_utetemperatur", "system_status"}
    # The marker is no number: the row that reads it is not in either set.
    assert "p22_brine_in_ut_1" not in tracker.numeric
    (saved, delay) = store.saves[-1]
    assert saved == {"keys": ["p22_utetemperatur", "system_status"],
                     "numeric": ["p22_kompressor", "p22_utetemperatur"]}
    assert delay == seen.SAVE_DELAY_SECONDS
    # Nothing new, nothing saved; a new number, one save and no announcement.
    announced: list[bool] = []
    tracker._on_new = lambda: announced.append(True)
    tracker.note({"p22_kompressor": 0.0})
    assert len(store.saves) == 1
    tracker.note({"p22_brine_in_ut_1": 3.5})
    assert len(store.saves) == 2 and "p22_brine_in_ut_1" in tracker.numeric
    assert announced == [True], "ett nollskilt värde är nytt för sidan också"
    tracker.add_numeric(["p22_fläkt"])
    assert len(store.saves) == 3 and announced == [True], "bara numeriskt: ingen annonsering"


def test_a_display_row_added_from_the_recorded_statistics_counts_as_numeric():
    tracker = seen.SeenValues(FakeStore())
    assert tracker.add(["p30_total_drifttid", "hp1_rps", "system_status"]) == 3
    assert tracker.numeric == {"p30_total_drifttid"}, (
        "displayrader är tal, Modbus-nycklar kan vara etiketter"
    )


def test_minor_one_is_carried_over_with_the_display_rows_as_numeric():
    old = {"keys": ["hp1_rps", "p22_utetemperatur", "p30_total_drifttid", "system_status"]}
    assert seen.migrate(old) == {
        "keys": ["hp1_rps", "p22_utetemperatur", "p30_total_drifttid", "system_status"],
        "numeric": ["p22_utetemperatur", "p30_total_drifttid"],
    }
    # Minor 2 passes through as it is, and rubbish gives an empty record.
    two = {"keys": ["hp1_rps"], "numeric": ["hp1_rps", "p22_kompressor"]}
    assert seen.migrate(two) == {"keys": ["hp1_rps"], "numeric": ["hp1_rps", "p22_kompressor"]}
    assert seen.migrate("not a dict") == {"keys": [], "numeric": []}
    assert seen.migrate({"keys": "no", "numeric": "no"}) == {"keys": [], "numeric": []}
    # A file the release before wrote back after a return: minor 1 on the
    # outside, the numeric set still inside, and a display row it learnt
    # meanwhile among the keys. Nothing of it is thrown away.
    returned = {"keys": ["hp1_rps", "p22_fläkt"], "numeric": ["hp1_rps", "p22_kompressor"]}
    assert seen.migrate(returned) == {
        "keys": ["hp1_rps", "p22_fläkt"],
        "numeric": ["hp1_rps", "p22_fläkt", "p22_kompressor"],
    }
    # The major stays at 1 so that the release before can still read the
    # file; the new shape is a new minor.
    assert seen.STORAGE_VERSION == 1 and seen.STORAGE_MINOR_VERSION == 2


def test_loading_reads_the_old_shape_and_the_new_alike():
    old = seen.SeenValues(FakeStore({"keys": ["p22_utetemperatur", "hp1_fan"]}))
    run(old.async_load())
    assert old.keys == {"p22_utetemperatur", "hp1_fan"}
    assert old.numeric == {"p22_utetemperatur"}
    assert not old.fresh
    new = seen.SeenValues(FakeStore({"keys": ["hp1_fan"],
                                     "numeric": ["hp1_fan", "p22_kompressor"]}))
    run(new.async_load())
    assert new.numeric == {"hp1_fan", "p22_kompressor"}
    # The zero rule is untouched by all this.
    assert new.unused({"hp1_fan": "0.0", "hp1_brine_pump": "0.0"}) == {"hp1_brine_pump"}


# ------------------------------------------------------------- the rows


def _page(number, keys, title="Värmepump"):
    return const.SlowPage(
        page=number, title=title, screens=[number * 10],
        values=[const.SlowValue(key=f"p{number}_{k}", label=k, page=number,
                                screen=number * 10, fmt="%.1f", var_indices=[i], scale=0.1)
                for i, k in enumerate(keys)],
    )


HEATPUMP = _page(22, ["utetemperatur", "vp_in_ut_1", "brine_in_ut_1", "brine_in_ut_2"])


def test_a_row_with_a_number_or_a_past_as_one_is_made_now_and_the_rest_wait():
    data = {"p22_utetemperatur": 7.2, "p22_vp_in_ut_1": 21.5}
    now, pending = rows.split_rows([HEATPUMP], data, numeric={"p22_brine_in_ut_2"})
    assert [v.key for _p, v in now] == ["p22_utetemperatur", "p22_vp_in_ut_1", "p22_brine_in_ut_2"]
    assert list(pending) == ["p22_brine_in_ut_1"]
    page, value = pending["p22_brine_in_ut_1"]
    assert page.title == "Värmepump" and value.label == "brine_in_ut_1"


def test_a_page_not_read_yet_waits_whole_and_comes_with_its_first_harvest():
    now, pending = rows.split_rows([HEATPUMP], None, numeric=set())
    assert now == [] and len(pending) == 4
    assert rows.due_rows(pending, None) == []
    assert rows.due_rows(pending, {"p22_utetemperatur": 7.2, "p22_brine_in_ut_1": 0.0}) == [
        "p22_utetemperatur", "p22_brine_in_ut_1",
    ], "en äkta nolla är ett tal"


def test_only_rows_gone_from_a_page_the_menu_still_has_are_vanished():
    menu = [HEATPUMP, _page(30, ["total_drifttid", "drift_24_h_m"], "Historik")]
    registry = [
        "p22_utetemperatur",        # still a row
        "p22_brine_in_ut_2",        # still a row, however long it reads the marker
        "p22_overhettning_s_h_4",   # gone from page 22, which the menu has
        "p30_drift_24_h_m_1",       # the i255's folded clock halves
        "p30_drift_24_h_m_2",
        "p23_varmvatten",           # page 23 is not in the menu: not reached, kept
        "outdoor_temp",             # not a display row at all
        "cop_day",
    ]
    assert rows.vanished_display_keys(menu, registry) == {
        "p22_overhettning_s_h_4", "p30_drift_24_h_m_1", "p30_drift_24_h_m_2",
    }
    assert rows.vanished_display_keys([], registry) == set(), "utan meny försvinner inget"


def test_a_selected_page_and_its_menu_copy_cannot_disagree_about_a_row():
    # The menu copy lacks a row the selected copy has: the union decides.
    menu_copy = _page(22, ["utetemperatur"])
    assert rows.vanished_display_keys([menu_copy, HEATPUMP], ["p22_brine_in_ut_1"]) == set()


# ------------------------------------------- the harvest, with two sentinel rows


class FakePanelDisplay:
    """A display on the heat pump page, two of whose rows read the marker."""

    def __init__(self, values):
        self.panel = asyncio.Lock()
        self.values = values

    async def async_current_page(self):
        return 22

    async def async_vars(self, screen):
        return list(self.values) if screen == 220 else [0]

    async def async_goto_page(self, target, route=None):
        return target == 22

    async def async_alarm_readout(self, screen, values):
        return load("web_api").AlarmReading(False, [])


@pytest.fixture()
def harvest():
    ha_stub.skip_unless_stubbed()
    coordinator = load("coordinator")

    def build(values):
        display = FakePanelDisplay(values)
        web = coordinator.CtcWebCoordinator(object(), display, [HEATPUMP], 1800)
        return web, display

    return build


def test_two_sentinel_rows_wait_and_get_their_entities_when_they_leave_a_number(harvest):
    web, display = harvest([72, 215, 9999, 9999])
    store = FakeStore()
    tracker = seen.SeenValues(store)

    data = run(web._async_update_data())
    web.data = data
    tracker.note(data)
    assert data == {"p22_utetemperatur": 7.2, "p22_vp_in_ut_1": 21.5}
    now, pending = rows.split_rows([HEATPUMP], web.data, tracker.numeric)
    assert [v.key for _p, v in now] == ["p22_utetemperatur", "p22_vp_in_ut_1"]
    assert sorted(pending) == ["p22_brine_in_ut_1", "p22_brine_in_ut_2"]

    # The outdoor unit is switched on: both rows read, both are due, and the
    # record remembers them as numbers from now on.
    display.values = [72, 215, 35, 0]
    data = run(web._async_update_data())
    web.data = data
    tracker.note(data)
    assert rows.due_rows(pending, web.data) == ["p22_brine_in_ut_1", "p22_brine_in_ut_2"]
    assert {"p22_brine_in_ut_1", "p22_brine_in_ut_2"} <= tracker.numeric

    # Switched off again, and Home Assistant restarted with nothing harvested
    # yet: the rows read the marker, but having been numbers they are made at
    # set-up all the same, so nothing flaps.
    display.values = [72, 215, 9999, 9999]
    assert run(web._async_update_data()) == {
        "p22_utetemperatur": 7.2, "p22_vp_in_ut_1": 21.5,
        "p22_brine_in_ut_1": 3.5, "p22_brine_in_ut_2": 0.0,
    }, "skörden bär förra värdet genom ett varv utan tal, som förut"
    now, pending = rows.split_rows([HEATPUMP], None, tracker.numeric)
    assert len(now) == 4 and pending == {}
