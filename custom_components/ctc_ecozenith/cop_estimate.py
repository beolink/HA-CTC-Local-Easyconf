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

Where the heat pump reports no delivered heat at all, the heat is worked out from the
refrigerant circuit instead (0.28.0). A heat pump without the flow meter, as an EcoAir
600 series without the accessory, shows "Avgiven värme" as zero whatever it does: on
a 700 series, which has the flow meter built in, the row is exactly flow times 4.18
times the temperature rise over the condenser, so without a flow there is nothing to
multiply. The display does show the evaporating and the condensing temperatures, the
dew points the controller works out from the two pressures, and those give the Carnot
coefficient of performance, T_cond / (T_cond - T_evap) in kelvin, the bound no heat
pump can pass. A real one gives a near constant share of it:
:data:`CARNOT_EFFICIENCY`, calibrated on a 700 series whose delivered heat and
supplied power are both on its display. The heat is that share of Carnot times the
supplied electricity, which is the inverter's power divided by
:data:`INVERTER_SHARE`. The calibration was found not to depend on the compressor
speed, and a constant held the energy to a few per cent on data it was not fitted
to; on another model it is rougher, which the sensors say. A defrost runs the
circuit backwards, so a reading taken during one counts its electricity and no heat.

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
#: What it says where the heat is worked out from the refrigerant circuit.
METHOD_CARNOT = "Carnot ur köldmediekretsen, approximation"
#: How far the refrigerant based figure can be off, in the attribute uncertainty.
#: The calibration held to within a few per cent on the heat pump it was made on;
#: on another model and another refrigerant it is rougher than that.
CARNOT_UNCERTAINTY = "±20 %"

#: Where the figure comes from: the display's delivered heat, or the circuit.
BASIS_MEASURED = "measured"
BASIS_CARNOT = "carnot"

#: The refrigerant circuit's rows on the heat pump page. Each is printed as a pair,
#: "Förångning °C/bar 1" and "... 2", and the first of them is the temperature.
EVAPORATION_LABEL = "förångning"
CONDENSATION_LABEL = "kondensering"

#: The share of the Carnot coefficient of performance a heat pump gives, against
#: its whole supplied electricity. Median of 194 readings with the compressor
#: running on an EcoAir 720M, delivered heat over supplied power each against the
#: Carnot figure of the same harvest; the middle 80 per cent lay between 0.43 and
#: 0.55, with no trend over 20 to 80 rps.
CARNOT_EFFICIENCY = 0.50
#: The inverter's power as a share of the supplied power, median of the same readings.
INVERTER_SHARE = 0.95
#: Less than this between the two temperatures is no circuit at work, a start or
#: a stop caught half way, and is given no heat.
MIN_LIFT = 10.0
_KELVIN = 273.15

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
    #: The display's delivered heat, or the refrigerant circuit: BASIS_MEASURED or BASIS_CARNOT.
    basis: str = BASIS_MEASURED


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


def _circuit_row(pages: list[Any], name: str) -> Any | None:
    for page in pages:
        for value in page.values:
            # No period check here: the label's own "°C/bar" carries the slash
            # a period row is told by, and no history row reads like these.
            label = (getattr(value, "label", None) or "").strip().casefold()
            if label.startswith(name) and "°c" in label and label.endswith(" 1"):
                return value
    return None


def find_refrigerant_rows(pages: list[Any]) -> tuple[Any, Any] | None:
    """The evaporating and the condensing temperature rows, or None unless both are harvested.

    The panel prints each as "Förångning °C/bar" and "Kondensering °C/bar" with a
    temperature and a pressure, which the catalogue keeps as two rows ending in 1
    and 2; the one ending in 1 is the temperature.
    """
    evaporation = _circuit_row(pages, EVAPORATION_LABEL)
    condensation = _circuit_row(pages, CONDENSATION_LABEL)
    if evaporation is None or condensation is None:
        return None
    return evaporation, condensation


def carnot_heat(evaporating: float, condensing: float, electric: float) -> float | None:
    """The heat :data:`CARNOT_EFFICIENCY` of Carnot gives for ``electric``, None below the lift."""
    lift = condensing - evaporating
    if lift < MIN_LIFT:
        return None
    return CARNOT_EFFICIENCY * (condensing + _KELVIN) / lift * electric


def carnot_pair(
    data: dict[str, Any] | None,
    evaporation_key: str,
    condensation_key: str,
    inverter_key: str,
    *,
    defrosting: bool = False,
) -> tuple[float, float] | None:
    """The worked out heat and the supplied electricity of one reading, in kilowatts.

    None where the inverter's power, or with the compressor running either
    temperature, is no number. A compressor at rest gives nothing and uses
    nothing. A defrost, or two temperatures too close for a circuit at work,
    counts the electricity and no heat: it is what the heat pump spent then.
    """
    values = data or {}
    inverter = _power(values.get(inverter_key))
    if inverter is None:
        return None
    if inverter <= 0:
        return 0.0, 0.0
    electric = inverter / INVERTER_SHARE
    if defrosting:
        return 0.0, electric
    evaporating = _power(values.get(evaporation_key))
    condensing = _power(values.get(condensation_key))
    if evaporating is None or condensing is None:
        return None
    heat = carnot_heat(evaporating, condensing, electric)
    return (0.0 if heat is None else heat), electric


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
    basis: str = BASIS_MEASURED,
) -> Estimate:
    """The figure for one span, or the reason it has none, from the sums and one reading.

    ``life`` is every interval counted so far, ``today`` the intervals of the
    local day, and ``pair`` the two powers of the latest harvest, or None where
    the page answered nothing. A missing row comes first: it is the one thing the
    owner can act on, and every other answer is read off it. A figure is shown
    only where the heat was not zero all the time, and where the quotient is
    between :data:`ESTIMATE_MIN` and :data:`ESTIMATE_MAX`. A heat worked out from
    the refrigerant circuit is never the pump reporting no heat: it is zero only
    where the compressor stood still, which is no electricity either.
    """
    if not heat_row:
        return Estimate(None, NO_HEAT_ROW, basis=basis)
    if not inverter_row:
        return Estimate(None, NO_INVERTER_ROW, basis=basis)
    measured = basis == BASIS_MEASURED
    if span == "now":
        if pair is None:
            return Estimate(None, SILENT, basis=basis)
        heat, electric = pair
        value, reason = _quotient(
            heat, electric, never_heat=measured and heat == 0 and life.heat == 0
        )
        return Estimate(value, reason, basis=basis)
    sums = life if span == "lifetime" else today
    if sums.hours <= 0:
        if span == "day" and life.hours > 0:
            return Estimate(None, NO_DAY_READINGS, basis=basis)
        return Estimate(None, SILENT if pair is None else FIRST_READ, basis=basis)
    value, reason = _quotient(sums.heat, sums.electric, never_heat=measured and life.heat == 0)
    return Estimate(value, reason, sums.hours, sums.hours, basis=basis)


def _defrosting(runtime: Any) -> bool:
    watch = getattr(runtime, "transitions", None)
    return bool(getattr(watch, "defrosting", False))


def uses_carnot(runtime: Any) -> bool:
    """Whether the figure is worked out from the refrigerant circuit on this installation.

    Only where the circuit's rows and the inverter's are harvested and the pump has
    never reported any delivered heat: a heat pump that does report it keeps its
    own measured figure, which is the closer one. A pump with a flow meter that has
    not yet run since the sums began shows the circuit's figure until its first heat.
    """
    if getattr(runtime, "carnot_estimate", None) is None:
        return False
    if getattr(runtime, "refrigerant_rows", None) is None:
        return False
    if getattr(runtime, "inverter_power", None) is None:
        return False
    measured = getattr(runtime, "heat_estimate", None)
    return measured is None or measured.lifetime().heat == 0


def estimate_for(runtime: Any, span: str, when: datetime | None = None) -> Estimate:
    """What the sensor for ``span`` shows, read off the runtime the integration keeps.

    The two powers are the ones of one harvest or of none: a heat row and an
    inverter row read at different moments are two readings, and no quotient is
    taken across them.
    """
    if uses_carnot(runtime):
        return _carnot_estimate_for(runtime, span, when)
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


def _carnot_estimate_for(runtime: Any, span: str, when: datetime | None) -> Estimate:
    """As estimate_for, with the heat worked out from the refrigerant circuit."""
    integrator = runtime.carnot_estimate
    evaporation, condensation = runtime.refrigerant_rows
    inverter_row = runtime.inverter_power
    web = getattr(runtime, "web", None)
    now = when or datetime.now(UTC)
    life = integrator.lifetime()
    today = integrator.today(integrator.local_day(now))
    pair = None
    read_at = None
    if web is not None:
        read_at = row_read_at(web, inverter_row.key)
        if read_at is not None and all(
            row_read_at(web, row.key) == read_at for row in (evaporation, condensation)
        ):
            pair = carnot_pair(
                web.data, evaporation.key, condensation.key, inverter_row.key,
                defrosting=_defrosting(runtime),
            )
    result = estimate(span, life, today, pair, basis=BASIS_CARNOT)
    return replace(result, read_at=read_at) if span == "now" else result


def estimate_section(runtime: Any, when: datetime | None = None) -> dict[str, Any]:
    """The estimate's sums and their hours, for the diagnostics and the debug report.

    Numbers and whether the rows were found, and nothing the owner typed. The
    refrigerant circuit's sums sit under ``carnot``, and ``basis`` says which of
    the two the sensors show.
    """
    integrator = getattr(runtime, "heat_estimate", None)
    section: dict[str, Any] = {
        "heat_row_found": getattr(runtime, "power_out", None) is not None,
        "inverter_row_found": getattr(runtime, "inverter_power", None) is not None,
        "refrigerant_rows_found": getattr(runtime, "refrigerant_rows", None) is not None,
        "basis": BASIS_CARNOT if uses_carnot(runtime) else BASIS_MEASURED,
    }
    now = when or datetime.now(UTC)
    for name, sums_of in (("", integrator), ("carnot", getattr(runtime, "carnot_estimate", None))):
        target = section if not name else section.setdefault(name, {})
        if sums_of is None:
            target["lifetime"] = None
            target["today"] = None
            continue
        target["lifetime"] = _sums_section(sums_of.lifetime())
        target["today"] = _sums_section(sums_of.today(sums_of.local_day(now)))
    return section


def _sums_section(sums: Sums) -> dict[str, float]:
    return {
        "heat_kwh": round(sums.heat, 1),
        "electric_kwh": round(sums.electric, 1),
        "hours": round(sums.hours, 1),
    }
