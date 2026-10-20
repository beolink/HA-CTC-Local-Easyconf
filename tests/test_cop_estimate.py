"""The coefficient of performance estimated from the display's two powers, without Home Assistant.

The estimate is the quotient of two integrated powers, delivered heat and the
inverter's electricity, read in one harvest. These tests hold the arithmetic of
the sums (integrate.EstimateIntegrator), the rules that turn the sums into a
figure or into a reason there is none (cop_estimate.py), the way the runtime's
two rows are paired, and the words the explanations and the reasons give, in both
languages. The sensors and the reload rule are held under a real core in
test_homeassistant_cop_estimate.py.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from conftest import load

STOCKHOLM = ZoneInfo("Europe/Stockholm")
EN_DASH = "–"
EM_DASH = "—"
DASHES = (EN_DASH, EM_DASH, " - ")


class FakeStore:
    def __init__(self) -> None:
        self.data = None
        self.delayed = 0

    async def async_load(self):
        return self.data

    async def async_save(self, data):
        self.data = data

    def async_delay_save(self, maker, _delay):
        self.delayed += 1
        self.data = maker()


def run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


@pytest.fixture(scope="module")
def estimate_mod():
    return load("cop_estimate")


@pytest.fixture(scope="module")
def integrate():
    return load("integrate")


def _at(minutes: int) -> datetime:
    return datetime(2026, 10, 15, 6, 0, tzinfo=UTC) + timedelta(minutes=minutes)


def _local(day: int, hour: int, minute: int = 0) -> datetime:
    """A moment on 2026 October ``day``, in Stockholm, as UTC."""
    return datetime(2026, 10, day, hour, minute, tzinfo=STOCKHOLM).astimezone(UTC)


# ------------------------------------------------------- the sums, in kilowatt hours


def test_the_first_reading_only_sets_the_baseline(integrate):
    sums = integrate.EstimateIntegrator(FakeStore())
    assert sums.note(_at(0), 4.0, 1.0) is False
    assert sums.lifetime() == (0.0, 0.0, 0.0)


def test_power_over_time_is_energy_by_the_trapezoid(integrate):
    sums = integrate.EstimateIntegrator(FakeStore())
    sums.note(_at(0), 4.0, 1.0)
    # Half an hour at the mean of 4 and 6 kW, and of 1 and 2 kW.
    assert sums.note(_at(30), 6.0, 2.0) is True
    assert sums.lifetime() == (2.5, 0.75, 0.5)


def test_a_gap_of_more_than_two_hours_adds_nothing_and_begins_again(integrate):
    sums = integrate.EstimateIntegrator(FakeStore())
    sums.note(_at(0), 6.0, 2.0)
    assert sums.note(_at(180), 6.0, 2.0) is False
    assert sums.lifetime() == (0.0, 0.0, 0.0)
    # The sample after the gap is the baseline for the next one.
    assert sums.note(_at(210), 6.0, 2.0) is True
    assert sums.lifetime() == (3.0, 1.0, 0.5)


def test_exactly_two_hours_is_still_measured(integrate):
    sums = integrate.EstimateIntegrator(FakeStore())
    sums.note(_at(0), 6.0, 2.0)
    assert sums.note(_at(120), 6.0, 2.0) is True
    assert sums.lifetime() == (12.0, 4.0, 2.0)


def test_the_same_harvest_twice_adds_nothing(integrate):
    sums = integrate.EstimateIntegrator(FakeStore())
    sums.note(_at(0), 4.0, 1.0)
    sums.note(_at(30), 6.0, 2.0)
    before = sums.lifetime()
    assert sums.note(_at(30), 6.0, 2.0) is False
    assert sums.lifetime() == before


def test_a_clock_that_went_back_adds_nothing(integrate):
    sums = integrate.EstimateIntegrator(FakeStore())
    sums.note(_at(30), 6.0, 2.0)
    assert sums.note(_at(0), 6.0, 2.0) is False
    assert sums.lifetime() == (0.0, 0.0, 0.0)


def test_a_reading_that_is_no_number_or_no_moment_is_no_reading(integrate):
    sums = integrate.EstimateIntegrator(FakeStore())
    sums.note(_at(0), 4.0, 1.0)
    sums.note(_at(30), 6.0, 2.0)
    before = sums.lifetime()
    assert sums.note(_at(60), None, 2.0) is False
    assert sums.note(_at(60), 6.0, "2,0") is False
    assert sums.note(_at(60), True, 2.0) is False
    assert sums.note(None, 6.0, 2.0) is False
    assert sums.lifetime() == before
    # Nor does it move the baseline: the next real reading pairs with the last real one.
    assert sums.note(_at(60), 6.0, 2.0) is True
    assert sums.lifetime() == (before[0] + 3.0, before[1] + 1.0, 1.0)


def test_the_sums_survive_a_restart_with_the_last_reading_and_today(integrate):
    store = FakeStore()
    sums = integrate.EstimateIntegrator(store, zone=STOCKHOLM)
    run(sums.async_load())
    sums.note(_local(20, 9), 4.0, 1.0)
    sums.note(_local(20, 9, 30), 6.0, 2.0)
    sums.save_soon()
    assert store.delayed == 1

    again = integrate.EstimateIntegrator(store, zone=STOCKHOLM)
    run(again.async_load())
    assert again.lifetime() == (2.5, 0.75, 0.5)
    assert again.today(date(2026, 10, 20)) == (2.5, 0.75, 0.5)
    # The last reading came back too, so the next half hour follows on.
    assert again.note(_local(20, 10), 6.0, 2.0) is True
    assert again.lifetime() == pytest.approx((5.5, 1.75, 1.0))


def test_a_store_that_holds_nothing_or_junk_starts_from_nothing(integrate):
    for junk in (None, "x", {"out": "many", "day": {"date": "not a day"}, "last": [1, 2]}):
        store = FakeStore()
        store.data = junk
        sums = integrate.EstimateIntegrator(store, zone=STOCKHOLM)
        run(sums.async_load())
        assert sums.lifetime() == (0.0, 0.0, 0.0)
        assert sums.today(date(2026, 10, 20)) == (0.0, 0.0, 0.0)


# ---------------------------------------------------------- the sums of one day


def test_today_begins_at_the_first_reading_of_the_local_day(integrate):
    sums = integrate.EstimateIntegrator(FakeStore(), zone=STOCKHOLM)
    sums.note(_local(20, 23), 4.0, 1.0)
    sums.note(_local(20, 23, 30), 6.0, 2.0)
    assert sums.today(date(2026, 10, 20)) == (2.5, 0.75, 0.5)
    # The day has no sums of its own before a reading of it comes.
    assert sums.today(date(2026, 10, 21)) == (0.0, 0.0, 0.0)


def test_an_interval_that_crosses_midnight_counts_to_the_day_it_ends_in(integrate):
    sums = integrate.EstimateIntegrator(FakeStore(), zone=STOCKHOLM)
    sums.note(_local(20, 23), 4.0, 1.0)
    sums.note(_local(20, 23, 30), 6.0, 2.0)
    # Half past eleven to half past midnight: one hour, which is the new day's whole.
    assert sums.note(_local(21, 0, 30), 6.0, 2.0) is True
    assert sums.today(date(2026, 10, 21)) == (6.0, 2.0, 1.0)
    assert sums.today(date(2026, 10, 20)) == (0.0, 0.0, 0.0)
    # The lifetime has all of it, as it should.
    assert sums.lifetime() == pytest.approx((8.5, 2.75, 1.5))
    assert sums.note(_local(21, 0, 45), 6.0, 2.0) is True
    assert sums.today(date(2026, 10, 21)) == pytest.approx((7.5, 2.5, 1.25))


def test_a_gap_across_midnight_starts_the_day_at_the_next_reading(integrate):
    sums = integrate.EstimateIntegrator(FakeStore(), zone=STOCKHOLM)
    sums.note(_local(20, 22), 4.0, 1.0)
    # Nothing between ten at night and eight in the morning: nothing is invented.
    assert sums.note(_local(21, 8), 6.0, 2.0) is False
    assert sums.today(date(2026, 10, 21)) == (0.0, 0.0, 0.0)
    assert sums.note(_local(21, 8, 30), 6.0, 2.0) is True
    assert sums.today(date(2026, 10, 21)) == (3.0, 1.0, 0.5)


def test_today_is_kept_across_a_restart_on_the_same_day(integrate):
    store = FakeStore()
    sums = integrate.EstimateIntegrator(store, zone=STOCKHOLM)
    sums.note(_local(21, 0, 30), 6.0, 2.0)
    sums.note(_local(21, 1), 6.0, 2.0)
    sums.save_soon()
    again = integrate.EstimateIntegrator(store, zone=STOCKHOLM)
    run(again.async_load())
    assert again.today(date(2026, 10, 21)) == (3.0, 1.0, 0.5)
    assert again.local_day(_local(21, 23)) == date(2026, 10, 21)


def test_the_day_is_kept_in_the_zone_it_is_given(integrate):
    # Half past midnight in Stockholm is the day before in UTC: the day is the zone's.
    sums = integrate.EstimateIntegrator(FakeStore(), zone=STOCKHOLM)
    moment = datetime(2026, 10, 20, 22, 30, tzinfo=UTC)
    assert sums.local_day(moment) == date(2026, 10, 21)
    utc = integrate.EstimateIntegrator(FakeStore(), zone=UTC)
    assert utc.local_day(moment) == date(2026, 10, 20)


# ------------------------------------------------- the quotient, and why none


def _series(integrate, heat_for, electric_for, steps=20):
    """Half hourly readings from a function of the step, into a fresh integrator."""
    sums = integrate.EstimateIntegrator(FakeStore(), zone=STOCKHOLM)
    for step in range(steps):
        when = _local(20, 6) + timedelta(minutes=30 * step)
        sums.note(when, heat_for(step), electric_for(step))
    return sums


def test_a_heat_of_one_and_a_fifth_times_the_electricity_reads_one_point_two(
    integrate, estimate_mod
):
    # The shape of the VSH data: heat 1.2 times the electricity, however it varies.
    electric = lambda step: 1.5 + (step % 7) * 0.25  # noqa: E731
    sums = _series(integrate, lambda step: 1.2 * electric(step), electric)
    life = sums.lifetime()
    result = estimate_mod.estimate("lifetime", life, life, None)
    assert result.value == 1.2
    assert f"{result.value:.2f}" == "1.20"
    assert result.reason is None
    assert result.heat_hours == result.electric_hours == pytest.approx(9.5)
    day = estimate_mod.estimate("day", life, sums.today(date(2026, 10, 20)), None)
    assert day.value == 1.2


def test_the_single_reading_is_the_quotient_of_the_pair_and_integrates_nothing(estimate_mod):
    result = estimate_mod.estimate(
        "now", estimate_mod.Sums(10.0, 4.0, 3.0), estimate_mod.Sums(1.0, 1.0, 0.5),
        (2.4, 2.0),
    )
    assert result.value == 1.2
    assert (result.heat_hours, result.electric_hours) == (0.0, 0.0)


def test_a_pump_that_never_reports_heat_gets_no_figure(integrate, estimate_mod):
    sums = _series(integrate, lambda _s: 0.0, lambda _s: 2.0)
    life = sums.lifetime()
    today = sums.today(date(2026, 10, 20))
    for span in ("lifetime", "day"):
        result = estimate_mod.estimate(span, life, today, (0.0, 2.0))
        assert result.value is None
        assert result.reason == estimate_mod.NO_HEAT, span
    now = estimate_mod.estimate("now", life, today, (0.0, 2.0))
    assert (now.value, now.reason) == (None, estimate_mod.NO_HEAT)


def test_heat_without_electricity_is_implausible_and_says_so(integrate, estimate_mod):
    sums = _series(integrate, lambda _s: 6.0, lambda _s: 0.0)
    life = sums.lifetime()
    assert estimate_mod.estimate("lifetime", life, life, None).reason == estimate_mod.IMPLAUSIBLE
    now = estimate_mod.estimate("now", life, life, (6.0, 0.0))
    assert (now.value, now.reason) == (None, estimate_mod.IMPLAUSIBLE)


def test_no_electricity_and_no_heat_is_no_quotient_at_all(estimate_mod):
    life = estimate_mod.Sums(20.0, 5.0, 4.0)
    result = estimate_mod.estimate("lifetime", life, life, None)
    assert result.value == 4.0
    today = estimate_mod.Sums(0.0, 0.0, 2.0)
    result = estimate_mod.estimate("day", life, today, None)
    assert (result.value, result.reason) == (None, estimate_mod.NO_ELECTRICITY)


def test_a_quotient_outside_one_to_eight_is_implausible_and_its_edges_are_shown(estimate_mod):
    life = estimate_mod.Sums(9.0, 3.0, 1.0)  # a quotient of 3 with heat present
    for heat, electric in ((0.9, 1.0), (8.5, 1.0), (0.5, 1.0)):
        result = estimate_mod.estimate("now", life, life, (heat, electric))
        assert (result.value, result.reason) == (None, estimate_mod.IMPLAUSIBLE), heat
    for heat, electric in ((1.0, 1.0), (8.0, 1.0), (2.5, 1.0)):
        result = estimate_mod.estimate("now", life, life, (heat, electric))
        assert result.reason is None
    assert estimate_mod.estimate("now", life, life, (2.5, 1.0)).value == 2.5


def test_zero_heat_in_one_reading_is_implausible_or_no_heat_by_the_sums(estimate_mod):
    # Zero heat against electricity is a quotient of nothing. Where the sums have
    # heat of their own, the reading is implausible; where they have none, the pump
    # has never reported heat and says so.
    with_heat = estimate_mod.Sums(9.0, 3.0, 1.0)
    assert estimate_mod.estimate("now", with_heat, with_heat, (0.0, 2.0)).reason == (
        estimate_mod.IMPLAUSIBLE
    )
    never = estimate_mod.Sums(0.0, 3.0, 1.0)
    assert estimate_mod.estimate("now", never, never, (0.0, 2.0)).reason == estimate_mod.NO_HEAT
    # A reading with heat in it is a figure even where the sums have had none yet.
    assert estimate_mod.estimate("now", never, never, (4.0, 2.0)).value == 2.0


def test_nothing_measured_yet_says_whether_the_page_answered(estimate_mod):
    empty = estimate_mod.Sums(0.0, 0.0, 0.0)
    assert estimate_mod.estimate("lifetime", empty, empty, None).reason == estimate_mod.SILENT
    assert estimate_mod.estimate("day", empty, empty, None).reason == estimate_mod.SILENT
    assert (
        estimate_mod.estimate("lifetime", empty, empty, (4.0, 2.0)).reason
        == estimate_mod.FIRST_READ
    )
    assert estimate_mod.estimate("now", empty, empty, None).reason == estimate_mod.SILENT
    assert estimate_mod.estimate("now", empty, empty, (4.0, 2.0)).value == 2.0


def test_a_day_with_no_reading_yet_is_said_to_be_so_while_the_sums_have_begun(estimate_mod):
    life = estimate_mod.Sums(20.0, 5.0, 4.0)
    empty = estimate_mod.Sums(0.0, 0.0, 0.0)
    result = estimate_mod.estimate("day", life, empty, (4.0, 2.0))
    assert (result.value, result.reason) == (None, estimate_mod.NO_DAY_READINGS)


def test_a_missing_row_is_the_reason_before_anything_else(estimate_mod):
    life = estimate_mod.Sums(20.0, 5.0, 4.0)
    for span in estimate_mod.ESTIMATE_SPANS:
        heat = estimate_mod.estimate(span, life, life, (4.0, 2.0), heat_row=False)
        assert (heat.value, heat.reason) == (None, estimate_mod.NO_HEAT_ROW)
        inverter = estimate_mod.estimate(span, life, life, (4.0, 2.0), inverter_row=False)
        assert (inverter.value, inverter.reason) == (None, estimate_mod.NO_INVERTER_ROW)


def test_the_bounds_and_the_gap_are_the_ones_the_code_holds(estimate_mod, integrate):
    assert (estimate_mod.ESTIMATE_MIN, estimate_mod.ESTIMATE_MAX) == (1.0, 8.0)
    assert timedelta(hours=2) == integrate.MAX_GAP
    assert estimate_mod.METHOD == "integrerad effekt, approximation"
    assert estimate_mod.ESTIMATE_SPANS == ("day", "lifetime", "now")


# ---------------------------------------------------------- the rows, and their pair


def _row(const, label, key, unit="kW"):
    return const.SlowValue(key=key, label=label, page=22, screen=118,
                           fmt="%.1f", var_indices=[1], unit=unit)


def test_the_inverter_row_is_found_by_its_panel_label_in_kilowatts(const, estimate_mod):
    page = const.SlowPage(page=22, title="Driftinfo Värmepump", screens=[118])
    page.values = [
        _row(const, "Avgiven värme", "out"),
        _row(const, "Inverter Motoreffekt", "motor"),
        _row(const, "Tillförd effekt", "in"),
    ]
    assert estimate_mod.find_inverter_power([page]).key == "motor"
    page.values = [_row(const, "inverter motoreffekt", "lower")]
    assert estimate_mod.find_inverter_power([page]).key == "lower"


def test_a_period_row_or_a_row_not_in_kilowatts_is_not_the_inverter(const, estimate_mod):
    page = const.SlowPage(page=30, title="Historik", screens=[128])
    page.values = [
        _row(const, "Inverter Motoreffekt/30 dagar", "period"),
        _row(const, "Inverter Motoreffekt", "hours", unit="h"),
    ]
    assert estimate_mod.find_inverter_power([page]) is None
    assert estimate_mod.find_inverter_power([]) is None


def test_the_pair_is_read_or_it_is_not(estimate_mod):
    assert estimate_mod.pair_now({"out": 2.4, "motor": 2.0}, "out", "motor") == (2.4, 2.0)
    assert estimate_mod.pair_now({"out": 2.4}, "out", "motor") is None
    assert estimate_mod.pair_now({"out": True, "motor": 2.0}, "out", "motor") is None
    assert estimate_mod.pair_now(None, "out", "motor") is None


def _runtime(estimate_mod, integrate, *, heat_read, inverter_read, data, sums=None):
    web = SimpleNamespace(
        data=data,
        last_read=lambda key: {"out": heat_read, "motor": inverter_read}.get(key),
    )
    integrator = integrate.EstimateIntegrator(FakeStore(), zone=STOCKHOLM)
    for when, heat, electric in sums or ():
        integrator.note(when, heat, electric)
    return SimpleNamespace(
        web=web,
        power_out=SimpleNamespace(key="out", label="Avgiven värme"),
        inverter_power=SimpleNamespace(key="motor", label="Inverter Motoreffekt"),
        heat_estimate=integrator,
    )


def test_the_two_powers_of_two_moments_are_no_pair(estimate_mod, integrate):
    read = _local(20, 10)
    runtime = _runtime(
        estimate_mod, integrate, heat_read=read, inverter_read=read - timedelta(minutes=30),
        data={"out": 2.4, "motor": 2.0},
    )
    result = estimate_mod.estimate_for(runtime, "now", _local(20, 10, 5))
    assert (result.value, result.reason) == (None, estimate_mod.SILENT)
    assert result.read_at == read


def test_the_pair_of_one_harvest_gives_the_now_figure_and_its_moment(estimate_mod, integrate):
    read = _local(20, 10)
    runtime = _runtime(
        estimate_mod, integrate, heat_read=read, inverter_read=read,
        data={"out": 2.4, "motor": 2.0},
    )
    result = estimate_mod.estimate_for(runtime, "now", _local(20, 10, 5))
    assert result.value == 1.2
    assert result.read_at == read


def test_a_runtime_without_the_estimate_says_the_page_is_silent(estimate_mod):
    result = estimate_mod.estimate_for(SimpleNamespace(), "lifetime")
    assert (result.value, result.reason) == (None, estimate_mod.SILENT)


def test_a_missing_row_is_reported_through_the_runtime(estimate_mod, integrate):
    runtime = _runtime(
        estimate_mod, integrate, heat_read=None, inverter_read=None, data={},
    )
    runtime.power_out = None
    result = estimate_mod.estimate_for(runtime, "day", _local(20, 10))
    assert result.reason == estimate_mod.NO_HEAT_ROW


def test_the_sums_as_the_diagnostics_hold_them(estimate_mod, integrate):
    runtime = _runtime(
        estimate_mod, integrate, heat_read=None, inverter_read=None, data={},
        sums=[(_local(20, 9), 4.0, 1.0), (_local(20, 9, 30), 6.0, 2.0)],
    )
    section = estimate_mod.estimate_section(runtime, _local(20, 10))
    assert section["heat_row_found"] is True
    assert section["inverter_row_found"] is True
    assert section["lifetime"] == {"heat_kwh": 2.5, "electric_kwh": 0.8, "hours": 0.5}
    assert section["today"] == {"heat_kwh": 2.5, "electric_kwh": 0.8, "hours": 0.5}
    bare = estimate_mod.estimate_section(SimpleNamespace(), _local(20, 10))
    assert bare == {
        "heat_row_found": False, "inverter_row_found": False, "lifetime": None, "today": None,
    }


# --------------------------------------------------------------- the words


@pytest.fixture(scope="module")
def explanations():
    return load("explanations")


ESTIMATE_TEXTS = ("cop_estimate_day", "cop_estimate_lifetime", "cop_estimate_now")


@pytest.mark.parametrize("key", ESTIMATE_TEXTS)
def test_each_estimate_sensor_is_explained_in_both_languages(explanations, key):
    text = explanations.DERIVED[key]
    assert isinstance(text, explanations.Text)
    assert text.en.strip() and text.en != str(text)
    assert text.en[:1].isupper() and text.en.endswith(".")
    for dash in DASHES:
        assert dash not in str(text), (key, dash)
        assert dash not in text.en, (key, dash)
    assert explanations.explain(key) == str(text)
    assert explanations.explain(key, "en") == text.en


@pytest.mark.parametrize("key", ESTIMATE_TEXTS)
def test_the_estimate_texts_say_what_they_rest_on_and_what_they_are_not(explanations, key):
    swedish = str(explanations.DERIVED[key])
    english = explanations.DERIVED[key].en
    assert "Avgiven värme (kW)" in swedish and "Inverter Motoreffekt (kW)" in swedish
    assert "Avgiven värme (kW)" in english and "Inverter Motoreffekt (kW)" in english
    assert "uppskattning" in swedish.lower() and "estimate" in english.lower()
    assert "räknare" in swedish and "counter" in english
    # Rough, but said to rest on something measured. No single installation's
    # figure goes into the text: one house's number would be wrong in the next.
    assert "grov" in swedish and "rough" in english
    assert "vetenskapligt grundad" in swedish and "scientifically grounded" in english
    assert "procent" not in swedish and "percent" not in english
    assert "VSH" not in swedish and "VSH" not in english
    assert "skäl" in swedish and "reason" in english
    assert "1 till 8" in swedish and "1 to 8" in english


def test_the_source_of_each_estimate_names_the_two_powers_not_the_counters(explanations):
    for key in ESTIMATE_TEXTS:
        swedish = explanations.source(key, lang="sv")
        english = explanations.source(key, lang="en")
        assert "Avgiven värme (kW)" in swedish and "Inverter Motoreffekt (kW)" in swedish
        assert "Avgiven värme (kW)" in english and "Inverter Motoreffekt (kW)" in english
        assert "energiräknarna" not in swedish and "energy counters" not in english


def test_each_reason_has_its_words_in_both_languages(explanations, estimate_mod):
    keys = {
        estimate_mod.NO_HEAT_ROW, estimate_mod.NO_INVERTER_ROW, estimate_mod.SILENT,
        estimate_mod.FIRST_READ, estimate_mod.NO_DAY_READINGS, estimate_mod.NO_HEAT,
        estimate_mod.NO_ELECTRICITY, estimate_mod.IMPLAUSIBLE,
    }
    assert set(explanations.ESTIMATE_REASONS) == keys
    for key, text in explanations.ESTIMATE_REASONS.items():
        assert text.en.strip() and text.en != str(text), key
        for dash in DASHES:
            assert dash not in str(text) and dash not in text.en, (key, dash)


def test_the_three_reasons_the_owner_is_told_about_read_as_they_are_meant(explanations):
    reasons = explanations.ESTIMATE_REASONS
    assert str(reasons["no_heat"]) == "pumpen anger ingen avgiven värme"
    assert reasons["no_heat"].en == "the heat pump reports no heat output"
    assert str(reasons["implausible"]) == "orimligt värde"
    assert reasons["implausible"].en == "implausible value"
    assert "väntar på en andra läsning" in str(reasons["first_read"])
    assert "waiting for a second reading" in reasons["first_read"].en


def test_the_estimate_reasons_stay_out_of_the_tables_that_hold_sentences(explanations):
    for table in explanations.TABLES:
        assert not set(table) & set(explanations.ESTIMATE_REASONS)
