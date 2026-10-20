"""A restart costs the panel nothing: the last harvest is kept and the next is owed (R6).

Set-up used to harvest the display inside itself, so every restart of Home
Assistant walked the panel before the platforms existed, and nothing of the
display survived the restart, so the sensors stood unavailable until the walk
was done. Now the last harvest, values and moments alike, is written to a
store, the coordinator comes up with it, and the first harvest is scheduled
one interval after the last reading, at once only where nothing was stored.
The run under a real core is in test_homeassistant_harvest.py; these hold the
rules themselves and the coordinator's start.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
import json
import pathlib

import pytest

from conftest import load
import ha_stub

COMPONENT = pathlib.Path(__file__).resolve().parent.parent / "custom_components" / "ctc_ecozenith"

INTERVAL = 1800
NOW = datetime(2026, 10, 9, 12, 0, tzinfo=UTC)


def run(coro):
    return asyncio.run(coro)


@pytest.fixture(scope="module")
def harvest():
    return load("harvest")


class FakeStore:
    """Stands in for Home Assistant's Store: what was loaded, what was scheduled."""

    def __init__(self, stored=None) -> None:
        self.stored = stored
        self.saves: list[tuple[dict, int]] = []

    async def async_load(self):
        return self.stored

    def async_delay_save(self, data_func, delay):
        self.saves.append((data_func(), delay))


def _stored(harvest, age: timedelta, **extra):
    read = NOW - age
    return harvest.StoredHarvest(
        values={"p22_avgiven_varme": 11.1, "p30_total_drifttid": 8044.0},
        read_at={"p22_avgiven_varme": read, "p30_total_drifttid": read},
        harvested_at=read,
        **extra,
    )


# ------------------------------------------------------------- the store


def test_a_harvest_survives_the_round_trip_through_json(harvest):
    before = _stored(harvest, timedelta(minutes=10), consumption=9166.0)
    payload = before.as_dict()
    # Plain JSON types only: floats, strings, None.
    assert payload["values"] == {"p22_avgiven_varme": 11.1, "p30_total_drifttid": 8044.0}
    assert all(isinstance(moment, str) for moment in payload["read_at"].values())
    assert isinstance(payload["harvested_at"], str)
    after = harvest.StoredHarvest.from_dict(payload)
    assert after.values == before.values
    assert after.read_at == before.read_at
    assert after.harvested_at == before.harvested_at
    assert after.consumption == 9166.0
    assert after.latest == before.harvested_at


def test_what_cannot_be_judged_is_left_out_of_the_seed(harvest):
    payload = {
        "values": {"a": 1.0, "b": "text", "c": True, "d": 4.0},
        "read_at": {"a": "2026-10-09T11:50:00+00:00", "d": "not a moment"},
        "harvested_at": None,
        "consumption": "9166",
    }
    stored = harvest.StoredHarvest.from_dict(payload)
    # b is no number, c is a bool, d has no moment to age by.
    assert stored.values == {"a": 1.0}
    assert stored.read_at == {"a": datetime(2026, 10, 9, 11, 50, tzinfo=UTC)}
    assert stored.consumption is None, "en text är inget tal"
    assert harvest.StoredHarvest.from_dict(None) is None
    assert harvest.StoredHarvest.from_dict("junk") is None
    assert harvest.StoredHarvest.from_dict({"values": {}, "read_at": {}}) is None, (
        "inget läst: inget att så"
    )


def test_the_immersion_heater_is_written_down_beside_the_consumption(harvest):
    # The pair of 62191 for the heat counter's moment (R28, F2.2).
    before = _stored(harvest, timedelta(minutes=10), consumption=9166.0, immersion=1.0)
    payload = before.as_dict()
    assert payload["immersion"] == 1.0
    assert harvest.StoredHarvest.from_dict(payload).immersion == 1.0
    # A store 0.21.1 wrote has no such key, and its harvest reads as before.
    older = {key: value for key, value in payload.items() if key != "immersion"}
    stored = harvest.StoredHarvest.from_dict(older)
    assert stored.immersion is None
    assert (stored.values, stored.read_at, stored.consumption) == (
        before.values, before.read_at, 9166.0
    )
    # The same check as the consumption's: no number, no counter.
    for junk in ("1", True, None, [1.0]):
        assert harvest.StoredHarvest.from_dict({**payload, "immersion": junk}).immersion is None
    assert harvest.StoredHarvest.from_dict({**payload, "immersion": 0}).immersion == 0.0
    # It moves with the rows when their keys move (L2).
    moved = before.renamed({"p22_avgiven_varme": "p22_s220_v0"})
    assert (moved.immersion, moved.consumption) == (1.0, 9166.0)
    # The display store stays at version 1: the key is new, the shape is not.
    assert harvest.STORAGE_VERSION == 1
    assert 'Store(hass, HARVEST_STORAGE_VERSION, f"{DOMAIN}_{entry.entry_id}_display")' in (
        _source("__init__.py")
    )


def test_a_moment_without_a_zone_is_taken_as_utc(harvest):
    assert harvest.parse_moment("2026-10-09T11:50:00") == datetime(
        2026, 10, 9, 11, 50, tzinfo=UTC
    )
    assert harvest.parse_moment("") is None and harvest.parse_moment(5) is None


def test_the_memory_writes_once_per_harvest(harvest):
    store = FakeStore()
    memory = harvest.HarvestMemory(store)
    assert run(memory.async_load()) is None
    harvested = NOW
    values = {"p22_avgiven_varme": 11.1, "gone": None}
    read_at = {"p22_avgiven_varme": harvested, "stale_key": harvested - timedelta(days=1)}
    assert memory.remember(values, read_at, harvested, 9166.0, 1.0)
    # Every refresh calls again, a skipped or failed one included: no new save.
    assert not memory.remember(values, read_at, harvested, 9166.0, 1.0)
    assert not memory.remember(values, read_at, None)
    assert memory.remember(values, read_at, harvested + timedelta(minutes=30))
    assert len(store.saves) == 2
    payload, delay = store.saves[0]
    assert delay == harvest.SAVE_DELAY_SECONDS
    # Only numbers, and only moments of keys that are in the data.
    assert payload["values"] == {"p22_avgiven_varme": 11.1}
    assert set(payload["read_at"]) == {"p22_avgiven_varme"}
    assert (payload["consumption"], payload["immersion"]) == (9166.0, 1.0)
    assert store.saves[1][0]["immersion"] is None


def test_the_memory_knows_what_it_loaded(harvest):
    stored = _stored(harvest, timedelta(minutes=10))
    store = FakeStore(stored.as_dict())
    memory = harvest.HarvestMemory(store)
    loaded = run(memory.async_load())
    assert loaded.values == stored.values
    # The harvest it loaded is already remembered: no save for it.
    assert not memory.remember(loaded.values, loaded.read_at, loaded.harvested_at)
    assert store.saves == []


def test_a_store_that_cannot_be_read_is_an_empty_one(harvest):
    class Broken(FakeStore):
        async def async_load(self):
            raise OSError("disk")

    assert run(harvest.HarvestMemory(Broken()).async_load()) is None


# -------------------------------------------------------- the first harvest


def test_without_a_store_the_first_harvest_is_at_once(harvest):
    assert harvest.first_harvest_delay(None, INTERVAL, NOW) == harvest.MIN_FIRST_DELAY


def test_a_restart_within_the_interval_waits_out_the_rest_of_it(harvest):
    stored = _stored(harvest, timedelta(minutes=10))
    assert harvest.first_harvest_delay(stored, INTERVAL, NOW) == 20 * 60


def test_a_longer_absence_harvests_as_soon_as_the_platforms_are_up(harvest):
    stored = _stored(harvest, timedelta(hours=3))
    assert harvest.first_harvest_delay(stored, INTERVAL, NOW) == harvest.MIN_FIRST_DELAY


def test_a_moment_from_the_future_puts_the_first_harvest_off_by_one_interval_at_most(harvest):
    # The host's clock was a day ahead when the harvest was written and has
    # been set right since: an interval from now, not an interval and a day.
    stored = _stored(harvest, timedelta(days=-1))
    assert harvest.first_harvest_delay(stored, INTERVAL, NOW) == INTERVAL
    # Seconds ahead, as a virtual machine's clock drifts: the same.
    stored = _stored(harvest, timedelta(seconds=-90))
    assert harvest.first_harvest_delay(stored, INTERVAL, NOW) == INTERVAL
    # And the floor still holds under the cap.
    stored = _stored(harvest, timedelta(days=-1))
    assert harvest.first_harvest_delay(stored, 2, NOW) == harvest.MIN_FIRST_DELAY


def test_the_latest_moment_counts_whichever_it_is(harvest):
    older = NOW - timedelta(hours=2)
    stored = harvest.StoredHarvest(
        values={"a": 1.0}, read_at={"a": older}, harvested_at=NOW - timedelta(minutes=5)
    )
    assert stored.latest == NOW - timedelta(minutes=5)
    assert harvest.first_harvest_delay(stored, INTERVAL, NOW) == 25 * 60


# ----------------------------------------------------- the coordinator


@pytest.fixture(scope="module")
def coordinator():
    ha_stub.skip_unless_stubbed()
    return load("coordinator")


class IdlePanel:
    """A display nobody talks to: the start must not."""

    def __init__(self) -> None:
        self.panel = asyncio.Lock()
        self.asked = 0

    def __getattr__(self, name):
        if name.startswith("async_"):
            async def refuse(*args, **kwargs):
                self.asked += 1
                raise AssertionError(f"{name} asked the display during the start")

            return refuse
        raise AttributeError(name)


def _page(const, number):
    return const.SlowPage(
        page=number,
        title=f"Sida {number}",
        screens=[number * 10],
        values=[const.SlowValue(key=f"p{number}_v", label="V", page=number, screen=number * 10,
                                fmt="%d", var_indices=[0])],
    )


def test_the_coordinator_comes_up_with_the_stored_harvest(coordinator, harvest, const):
    read = datetime.now(UTC) - timedelta(minutes=10)
    stored = harvest.StoredHarvest(
        values={"p21_v": 21.0}, read_at={"p21_v": read}, harvested_at=read
    )
    panel = IdlePanel()
    web = coordinator.CtcWebCoordinator(
        hass=object(), client=panel, pages=[_page(const, 21)], interval=INTERVAL, stored=stored
    )
    assert web.data == {"p21_v": 21.0}, "sensorerna har något att visa från start"
    assert web.last_read("p21_v") == read
    assert web.last_harvest == read
    assert web.is_fresh("p21_v")
    # The first harvest is owed one interval after the last reading.
    wait = web.update_interval.total_seconds()
    assert 19 * 60 < wait <= 20 * 60
    assert web.next_attempt is not None
    assert panel.asked == 0, "starten frågar inte displayen"


def test_an_old_store_seeds_stale_values_and_an_early_harvest(coordinator, harvest, const):
    read = datetime.now(UTC) - timedelta(days=2)
    stored = harvest.StoredHarvest(values={"p21_v": 21.0}, read_at={"p21_v": read},
                                   harvested_at=read)
    web = coordinator.CtcWebCoordinator(
        hass=object(), client=IdlePanel(), pages=[_page(const, 21)], interval=INTERVAL,
        stored=stored
    )
    assert web.data == {"p21_v": 21.0}
    assert not web.is_fresh("p21_v"), "två dygn gammalt visas inte som nu"
    assert web.update_interval == timedelta(seconds=harvest.MIN_FIRST_DELAY)


def test_without_a_store_the_coordinator_starts_empty_and_soon(coordinator, harvest, const):
    web = coordinator.CtcWebCoordinator(
        hass=object(), client=IdlePanel(), pages=[_page(const, 21)], interval=INTERVAL
    )
    assert web.data is None
    assert web.last_harvest is None
    assert web.update_interval == timedelta(seconds=harvest.MIN_FIRST_DELAY)


def test_the_consumption_pair_comes_back_as_a_pair(cop):
    snapshot = cop.ConsumptionSnapshot()
    read = NOW - timedelta(minutes=10)
    snapshot.seed(read, 9166.0)
    assert snapshot.value == 9166.0
    # The same moment again, with a fresh Modbus reading: the pair stands.
    snapshot.update(read, {"compressor_kwh": 9170.0})
    assert snapshot.value == 9166.0
    # A new reading of the display takes the new Modbus value.
    snapshot.update(NOW, {"compressor_kwh": 9170.0})
    assert snapshot.value == 9170.0
    # Nothing stored: nothing seeded.
    empty = cop.ConsumptionSnapshot()
    empty.seed(None, 9166.0)
    assert empty.value is None


class SampleStore:
    """The coefficient of performance's store, through JSON like the file."""

    def __init__(self) -> None:
        self.data = None

    async def async_load(self):
        return None if self.data is None else json.loads(json.dumps(self.data))

    async def async_save(self, data):
        self.data = json.loads(json.dumps(data))


def _restart_after_a_timer_sample(harvest, cop, *, kept: bool):
    """A timer sample with the counter, a harvest after it, a restart, the start's sample.

    As set-up does it: the display store loaded, the immersion heater's
    snapshot seeded with the moment the heat counter came back with and
    what was written down beside it, and the sample of that moment taken.
    """
    heat, consumed = "p25_avgiven_varme_totalt", "p25_tillford_energi_totalt"
    samples = SampleStore()
    tracker = cop.CopTracker(samples)
    timer = datetime(2026, 10, 13, 20, 0, tzinfo=UTC)
    run(tracker.async_record(22020.0, 9020.0, now=timer, immersion=1.0))
    # The harvest at 20:45 reads the counters, and 62191 with them.
    read = timer + timedelta(minutes=45)
    snapshot = cop.ImmersionSnapshot()
    snapshot.update(read, {"immersion_kwh": 1.0})
    store = FakeStore()
    memory = harvest.HarvestMemory(store)
    assert memory.remember(
        {heat: 22021.0, consumed: 9021.0}, {heat: read, consumed: read}, read, None,
        snapshot.value if kept else None,
    )
    # Home Assistant starts again at 21:00, or the entry is reloaded.
    stored = run(harvest.HarvestMemory(FakeStore(store.saves[-1][0])).async_load())
    seeded = cop.ImmersionSnapshot()
    seeded.seed(stored.read_at[heat], stored.immersion)
    # A Modbus poll after the start, with the display not read again: the pair stands.
    seeded.update(stored.read_at[heat], {"immersion_kwh": 5.0})
    again = cop.CopTracker(samples)
    moment = stored.read_at[heat]
    run(again.async_record(
        stored.values[heat], stored.values[consumed], now=moment,
        immersion=seeded.value_at(moment),
    ))
    return samples.data, timer.date().isoformat()


def test_the_sample_at_a_restart_keeps_the_immersion_heater_written_down_with_the_harvest(
    harvest, cop
):
    # F2.2: the start's sample replaces the day's, and keeps its third number.
    data, day = _restart_after_a_timer_sample(harvest, cop, kept=True)
    assert data["samples"][day] == [22021.0, 9021.0, 1.0]
    assert [len(row) for row in data["recent"]] == [4, 4]
    # A store from before the counter was written down, as at the first
    # start after the update, still gives a day without it: expected.
    data, day = _restart_after_a_timer_sample(harvest, cop, kept=False)
    assert data["samples"][day] == [22021.0, 9021.0]


# ------------------------------------------------------------- in code


def _source(name: str) -> str:
    return (COMPONENT / name).read_text(encoding="utf-8")


def test_the_set_up_seeds_the_coordinator_and_remembers_after_each_harvest():
    setup = _source("__init__.py").split("async def async_setup_entry")[1].split("\nasync def ")[0]
    assert "HarvestMemory(" in setup
    assert "stored=stored" in setup, "skördaren sås ur storen"
    assert "memory.remember(" in setup
    assert "web.async_add_listener(_remember)" in setup
    # The energy counters are recorded with the moment they were read.
    assert "_record_cop(read_at=" in setup
    assert "snapshot.seed(" in setup
    # The immersion heater's counter is written down with the harvest and
    # seeded back with it (F2.2), and is taken before it is written down:
    # the coordinator calls its listeners in the order they were added.
    seed = setup.split("immersion.seed(")[1].split("\n\n")[0]
    assert "web.last_read(immersion_key)" in seed
    assert "stored.immersion if stored is not None else None" in seed
    remember = setup.split("def _remember()")[1].split("entry.async_on_unload(")[0]
    assert "runtime.immersion_snapshot" in remember
    assert setup.index("web.async_add_listener(_take_immersion)") < setup.index(
        "web.async_add_listener(_remember)"
    )


def test_removing_the_entry_takes_all_three_stores_with_it():
    remove = _source("__init__.py").split("async def async_remove_entry")[1]
    for suffix in ("_seen", "_display", "_cop", "_cop_estimate"):
        assert f'_{{entry.entry_id}}{suffix}"' in remove, f"storen {suffix} tas inte bort"
    # seen, display, cop, the estimate's sums and alarms (L3 and R30 added the
    # fourth, the estimate the fifth)
    assert remove.count(".async_remove()") == 5


def test_the_identity_is_read_in_the_background_and_swept_once_per_run():
    source = _source("__init__.py")
    catch_up = source.split("async def _async_catch_up")[1].split("\ndef ")[0]
    assert "await async_read_identity(" in catch_up
    assert "_SWEPT" in catch_up
    # And the options are written with the panel free, so the reload that
    # follows cuts no harvest short.
    writing = catch_up.split("if changed:")[1]
    assert writing.index("async with client.panel:") < writing.index("async_update_entry(")
