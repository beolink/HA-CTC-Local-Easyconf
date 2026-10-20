"""The coefficient of performance estimated from the display's two powers, best effort.

The counters the coefficient of performance is worked out from stay at zero on
some installations, and on others the display does not show them at all. The
display does show two powers: the heat delivered, "Avgiven värme (kW)", and the
electricity the inverter drives the compressor with, "Inverter Motoreffekt (kW)".
Both are read in one harvest, and :class:`integrate.EstimateIntegrator` adds them
up over time. Their quotient is the estimate, and the three sensors show it: over
today, over the whole time the sums have been kept, and as one reading.

It is an estimate, and the sensors say so. The electricity is the inverter's power to
the compressor, not the heat pump's whole supplied electricity: on VSH the inverter's
power is a median of 0.95 of the supplied power, which on its own makes the quotient
about 5 per cent high. The heat is the display's own figure, not a flow times a
temperature difference; a flow based heat is not part of this version. What the display
counts as delivered heat, and whether the immersion heater's heat is in it, is not
established here. Against the counters over the same hours the estimate read 9 per cent
high on VSH, of which about 4 per cent is the display's heat and about 5 per cent the
electricity.

Nothing here writes to the heat pump, and no figure here replaces a counter-based
one: each lives on a sensor of its own.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import Any

from .const import PERIOD_MARKERS
from .cop import row_read_at
from .integrate import Sums

#: The inverter's power row, matched on the label the panel printed.
INVERTER_LABEL = "inverter motoreffekt"

#: An estimate outside these is no figure. Narrower than cop.COP_MIN and COP_MAX,
#: which guard the counters' quotients: this one is noisier, and a heat pump in
#: steady operation gives more heat than the electricity it is given, so a quotient
#: below one is not a performance figure, and above eight is not one either.
ESTIMATE_MIN = 1.0
ESTIMATE_MAX = 8.0

#: What the attribute method says, the same on all three sensors.
METHOD = "integrerad effekt, approximation"

#: The spans the three sensors report: today, the whole lifetime, and one reading.
ESTIMATE_SPANS = ("day", "lifetime", "now")

#: Why an estimate has no figure, by key. The words are in explanations.py.
NO_HEAT_ROW = "heat_row_missing"
NO_INVERTER_ROW = "inverter_row_missing"
#: The page answers nothing at all, as a heat pump switched off does.
SILENT = "page_silent"
#: One reading is a power and two are an energy: the sums start at the next harvest.
FIRST_READ = "first_read"
#: The sums have begun, but not today: no reading of today has come yet.
NO_DAY_READINGS = "no_readings_today"
#: The heat pump has never reported any heat output in the measured time.
NO_HEAT = "no_heat"
#: Heat with no electricity, or electricity with no heat, is no quotient to show.
NO_ELECTRICITY = "no_electricity"
IMPLAUSIBLE = "implausible"


@dataclass(frozen=True)
class Estimate:
    """What one sensor shows: the figure or the reason it has none, and what it rests on."""

    value: float | None
    #: One of the reason keys above, where there is no figure.
    reason: str | None = None
    #: The hours of measured time the sums behind the figure rest on. The single
    #: reading integrates nothing, so it has none.
    heat_hours: float = 0.0
    electric_hours: float = 0.0
    #: When the harvest the single reading comes from was read off the panel.
    read_at: datetime | None = None


def _power(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def find_inverter_power(pages: list[Any]) -> Any | None:
    """The inverter's power row, Inverter Motoreffekt in kilowatts, where the pages are harvested.

    Matched as cop.find_power_pair matches the heat row: on the label the panel
    printed, and only on a row in kilowatts, so a period row of the same name on a
    history page cannot pass for the power.
    """
    for page in pages:
        for value in page.values:
            label = (getattr(value, "label", None) or "").strip().casefold()
            if (
                (getattr(value, "unit", None) or "").casefold() == "kw"
                and label.startswith(INVERTER_LABEL)
                and not any(marker in label for marker in PERIOD_MARKERS)
            ):
                return value
    return None


def pair_now(
    data: dict[str, Any] | None, heat_key: str, inverter_key: str
) -> tuple[float, float] | None:
    """The heat and the electricity of one reading, or None unless both are numbers."""
    values = data or {}
    heat = _power(values.get(heat_key))
    electric = _power(values.get(inverter_key))
    if heat is None or electric is None:
        return None
    return heat, electric


def _quotient(heat: float, electric: float, *, never_heat: bool) -> tuple[float | None, str | None]:
    """The quotient of two sums or one reading, or the reason it is none."""
    if never_heat:
        return None, NO_HEAT
    if electric <= 0:
        # Heat without electricity cannot be right. Neither heat nor electricity
        # is simply nothing to divide.
        return None, NO_ELECTRICITY if heat <= 0 else IMPLAUSIBLE
    value = heat / electric
    if not ESTIMATE_MIN <= value <= ESTIMATE_MAX:
        return None, IMPLAUSIBLE
    return round(value, 2), None


def estimate(
    span: str,
    life: Sums,
    today: Sums,
    pair: tuple[float, float] | None,
    *,
    heat_row: bool = True,
    inverter_row: bool = True,
) -> Estimate:
    """The figure for one span, or the reason it has none, from the sums and one reading.

    ``life`` is every interval counted so far, ``today`` the intervals of the
    local day, and ``pair`` the two powers of the latest harvest, or None where
    the page answered nothing. A missing row comes first: it is the one thing the
    owner can act on, and every other answer is read off it. A figure is shown
    only where the heat was not zero all the time, and where the quotient is
    between :data:`ESTIMATE_MIN` and :data:`ESTIMATE_MAX`.
    """
    if not heat_row:
        return Estimate(None, NO_HEAT_ROW)
    if not inverter_row:
        return Estimate(None, NO_INVERTER_ROW)
    if span == "now":
        if pair is None:
            return Estimate(None, SILENT)
        heat, electric = pair
        value, reason = _quotient(heat, electric, never_heat=heat == 0 and life.heat == 0)
        return Estimate(value, reason)
    sums = life if span == "lifetime" else today
    if sums.hours <= 0:
        if span == "day" and life.hours > 0:
            return Estimate(None, NO_DAY_READINGS)
        return Estimate(None, SILENT if pair is None else FIRST_READ)
    value, reason = _quotient(sums.heat, sums.electric, never_heat=life.heat == 0)
    return Estimate(value, reason, sums.hours, sums.hours)


def estimate_for(runtime: Any, span: str, when: datetime | None = None) -> Estimate:
    """What the sensor for ``span`` shows, read off the runtime the integration keeps.

    The two powers are the ones of one harvest or of none: a heat row and an
    inverter row read at different moments are two readings, and no quotient is
    taken across them.
    """
    integrator = getattr(runtime, "heat_estimate", None)
    if integrator is None:
        return Estimate(None, SILENT)
    heat_row = getattr(runtime, "power_out", None)
    inverter_row = getattr(runtime, "inverter_power", None)
    web = getattr(runtime, "web", None)
    now = when or datetime.now(UTC)
    life = integrator.lifetime()
    today = integrator.today(integrator.local_day(now))
    pair = None
    read_at = None
    if web is not None and heat_row is not None and inverter_row is not None:
        read_at = row_read_at(web, heat_row.key)
        if read_at is not None and row_read_at(web, inverter_row.key) == read_at:
            pair = pair_now(web.data, heat_row.key, inverter_row.key)
    result = estimate(
        span, life, today, pair,
        heat_row=heat_row is not None, inverter_row=inverter_row is not None,
    )
    return replace(result, read_at=read_at) if span == "now" else result


def estimate_section(runtime: Any, when: datetime | None = None) -> dict[str, Any]:
    """The estimate's sums and their hours, for the diagnostics and the debug report.

    Numbers and whether the two rows were found, and nothing the owner typed.
    """
    integrator = getattr(runtime, "heat_estimate", None)
    section: dict[str, Any] = {
        "heat_row_found": getattr(runtime, "power_out", None) is not None,
        "inverter_row_found": getattr(runtime, "inverter_power", None) is not None,
    }
    if integrator is None:
        section["lifetime"] = None
        section["today"] = None
        return section
    now = when or datetime.now(UTC)
    life = integrator.lifetime()
    today = integrator.today(integrator.local_day(now))
    section["lifetime"] = _sums_section(life)
    section["today"] = _sums_section(today)
    return section


def _sums_section(sums: Sums) -> dict[str, float]:
    return {
        "heat_kwh": round(sums.heat, 1),
        "electric_kwh": round(sums.electric, 1),
        "hours": round(sums.hours, 1),
    }
