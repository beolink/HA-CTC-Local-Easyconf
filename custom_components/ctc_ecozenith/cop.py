"""Coefficient of performance over a rolling year.

Modbus reports what the unit consumes but never what it delivers, so a real
coefficient of performance needs the display's lifetime counter for delivered
heat. The consumed side comes from the display's own counter where it has one,
as the i255 and the i550 Pro do, and otherwise from Modbus register 62341, which
holds the same number: 9166 kWh against the i255 display's 9166,0 on 2026-09-15.
CTC's manual for the i360 lists delivered energy on its stored operation data
page but no consumed energy at all.

Dividing those two gives the figure for the whole life of the machine, which
flatters or punishes it for years nobody is asking about. A yearly figure needs
the difference across a window, so one sample a day is kept and the oldest one
inside the window is used as the starting point. Each span stands on its own:
the lifetime figure is never dressed up as a year, and until a year of samples
exists the yearly figure says how far the samples have come instead of showing
nothing. The commissioning day, worked out from the powered-on hours, serves
the first year alone: the counters stood at zero that day, so a sample from
about a year later holds the whole first year.

Since roadmap R28 the samples also carry the immersion heater's counter,
Modbus 62191, taken at the moment the display's counters were read, and the
same spans give the immersion heater's share of the electricity, the share of
auxiliary heat; see the section of that name below.
"""

from __future__ import annotations

from collections.abc import Collection
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
import logging
from typing import Any

from .const import (
    COP_HISTORY_DAYS,
    COP_WINDOW_DAYS,
    MODBUS_SENSORS,
    PERIOD_MARKERS,
    SENTINELS,
)

_LOGGER = logging.getLogger(__name__)

STORAGE_VERSION = 1

#: Minor 1 held each sample as delivered heat and consumed energy. Minor 2
#: adds the immersion heater's counter, Modbus 62191, as a third number where
#: it was read with the other two (roadmap R28); a sample without it, every
#: sample minor 1 wrote among them, is one whose immersion heater is unknown.
#: The major version stays at 1: the release before reads the first two
#: numbers of a sample and passes over the third, so a return to it keeps
#: every sample, and only the third numbers are lost when it writes back.
STORAGE_MINOR_VERSION = 2

#: Below this the divisor is noise rather than a measurement. The two floors are
#: not the same kind of number, which is why they are so far apart. A lifetime
#: total is thousands of kilowatt hours, so rounding is irrelevant and the floor
#: only asks whether the machine has done anything at all: ten is enough, and
#: fifty, which this used to be, held back figures that were perfectly good. A
#: single day's delta is a handful of whole kilowatt hours, and since the display
#: counts in whole ones, two readings carry up to a kilowatt hour of rounding
#: between them. At three that is a third at worst, which is the most a daily
#: figure can carry and still mean something; at one it would be all of it.
MIN_CONSUMPTION_KWH = 10.0
MIN_CONSUMPTION_KWH_DAY = 3.0

#: A quotient outside this is not a performance figure. It is two counters that
#: do not belong together, or one of them standing still: a heat pump does not
#: deliver less than it is given, and nothing delivers ten times its input.
COP_MIN = 0.5
COP_MAX = 10.0

#: How long the unit must have been switched on before a counter standing at
#: zero is a fault rather than a machine that has not got going yet.
RUNNING_HOURS = 24

#: A sample has to be this old before it can serve as yesterday. The counters
#: are whole kilowatt hours, so a shorter span divides two small integers and
#: the answer swings wildly.
DAY_MIN_HOURS = 20
DAY_MAX_HOURS = 30

#: How long the short run of samples behind the daily figure is kept.
RECENT_DAYS = 4

#: A yearly figure has to stand on a span close to a year. A gap in the samples
#: must not quietly turn "the last year" into "the last fourteen months".
YEAR_MAX_DAYS = 380

#: The shorter windows, by the span the sensors report them under: how many
#: days back the base sample lies, and how many more days back it may lie
#: before the figure stops meaning what its name says. Read against the daily
#: samples the tracker already keeps for the year, so they cost nothing new.
#: A week may stretch to nine days and a month to thirty-five: a day or two of
#: missing samples, which a Home Assistant that was down over a weekend leaves
#: behind, should not cost the figure, but the yearly band's fifteen days would
#: turn a week into a fortnight.
WINDOWS: dict[str, tuple[int, int]] = {"week": (7, 2), "month": (30, 5)}

#: What each span is called in the attribute underlag, for the coefficient of
#: performance and the share of auxiliary heat alike.
BASIS_WORDS: dict[str, str] = {
    "year": "rullande år",
    "day": "senaste dygnet",
    "week": "senaste 7 dygnen",
    "month": "senaste 30 dygnen",
    "first_year": "första året",
    "lifetime": "hela livslängden",
}


@dataclass
class CopResult:
    """A coefficient of performance and how it was arrived at."""

    value: float | None
    #: The span the figure stands on: "day", "week", "month", "year",
    #: "first_year" or "lifetime". Every method of the tracker answers under
    #: the span it was asked for, so a figure never wears another span's name.
    basis: str
    days: int
    energy_out: float | None = None
    energy_in: float | None = None
    #: What the tracker alone can say about an empty figure: how far its
    #: samples have come towards the window, or that the start is unknown.
    #: The counters' own reasons are :func:`cop_reason`'s business.
    reason: str | None = None

    def as_attributes(self) -> dict[str, Any]:
        basis = BASIS_WORDS.get(self.basis, self.basis)
        return {
            "underlag": basis,
            "dygn i underlaget": self.days,
            "avgiven värme kWh": self.energy_out,
            "tillförd energi kWh": self.energy_in,
        }


def _parse(stamp: str) -> datetime:
    """Read a stored timestamp, treating a naive one as UTC."""
    value = datetime.fromisoformat(stamp)
    return value if value.tzinfo else value.replace(tzinfo=UTC)


def implausible(value: float | None) -> bool:
    """Whether a quotient is outside what a heat pump can actually do."""
    return value is not None and not (COP_MIN <= value <= COP_MAX)


def _ratio(out: float, consumed: float, floor: float = MIN_CONSUMPTION_KWH) -> float | None:
    if consumed < floor:
        return None
    value = round(out / consumed, 2)
    return None if implausible(value) else value


def powered_on_hours(runtime: Any) -> float | None:
    """The hours the unit says it has been switched on, if the page is harvested."""
    if getattr(runtime, "web", None) is None or not getattr(runtime, "operating_hours", None):
        return None
    data = runtime.web.data or {}
    readings = [data.get(value.key) for value in runtime.operating_hours]
    return max(
        (h for h in readings if isinstance(h, (int, float)) and not isinstance(h, bool) and h > 0),
        default=None,
    )


#: Why a sum of the integration's own has no figure yet. The first is what a
#: heat pump switched off looks like: the rows are on the page, the page answers
#: nothing. The second is the half hour between the first harvest and the second,
#: since one reading is a power and two are an energy.
POWER_SILENT = "värmepumpens sida ger inga värden, vilket en avstängd värmepump inte gör"
POWER_FIRST_READ = "väntar på en andra läsning, summeringen börjar vid nästa skörd"


def power_now(runtime: Any) -> tuple[float, float] | None:
    """The two powers as the last harvest read them, or None where it read neither.

    Both or neither: a quotient wants the pair, and the sums are only ever added
    to from a pair read in the same instant. None is what a heat pump switched
    off looks like, since its page then answers nothing at all.
    """
    if getattr(runtime, "web", None) is None:
        return None
    out_row = getattr(runtime, "power_out", None)
    in_row = getattr(runtime, "power_in", None)
    if out_row is None or in_row is None:
        return None
    data = runtime.web.data or {}
    out = data.get(out_row.key)
    consumed = data.get(in_row.key)
    if not isinstance(out, (int, float)) or not isinstance(consumed, (int, float)):
        return None
    if isinstance(out, bool) or isinstance(consumed, bool):
        return None
    return float(out), float(consumed)


def compressor_hours(runtime: Any) -> float | None:
    """The hours the compressor has run, Modbus 62214.

    What decides whether a counter at zero is a fault: the compressor's own
    hours, not the unit's. See :func:`counter_fault`.
    """
    return _modbus_number(runtime, "compressor_hours")


def counter_fault(
    out: float | None,
    consumed: float | None,
    hours: float | None,
    floor: float = MIN_CONSUMPTION_KWH,
) -> str | None:
    """Which fault the two counters show, or None when they merely need time.

    ``stuck`` is a counter standing at zero where the COMPRESSOR has run for a
    day or more, which is what CTC's controllers do on installations where these
    counters are never written. The hours have to be the compressor's, Modbus
    62214, and not the hours the unit has been switched on: a machine can stand
    powered on for years without the compressor ever running, and then two zeroes
    are an honest account of it rather than a fault. Judging it on the powered-on
    hours told Portugal, whose display counts no compressor hours at all, that
    its controller was at fault. ``implausible`` is two numbers whose quotient no
    heat pump could produce, so they are not the pair they are taken for. A
    machine that has simply not run far enough yet is neither.

    The quotient is judged only above the same floor :func:`_ratio` divides
    above. Below it the divisor is a kilowatt hour or two of whole-number
    rounding, and 11 against 1 is a quotient of nothing rather than a quotient
    of eleven: calling it a fault sent the two totals to the statistics
    backend for a machine that had merely started counting.
    """
    if out is None or consumed is None:
        return None
    if hours is not None and hours >= RUNNING_HOURS and (out <= 0 or consumed <= 0):
        return "stuck"
    if consumed >= floor and implausible(round(out / consumed, 2)):
        return "implausible"
    return None


def cop_reason(
    value: float | None,
    basis: str,
    energy_out: float | None,
    energy_in: float | None,
    hours: float | None = None,
    modbus_answered: bool = True,
    *,
    days: int = 1,
) -> str | None:
    """Why a figure is missing, in words the owner can act on or dismiss.

    An empty sensor that says nothing is the thing people ask about, so it says
    which of the reasons it is: no sample yet, too little energy so far, a
    counter standing still, or two counters that do not add up. Where the
    consumed side comes from Modbus, ``modbus_answered`` says whether register
    62341 has answered at all, so a model that lacks it is told so instead of
    waiting forever for a reading. For a week or a month ``days`` is the span
    the deltas cover, which sets their floor the way it sets the figure's.
    """
    if value is not None:
        return None
    if energy_in is None and not modbus_answered:
        return "registret 62341 har inte svarat, så tillförd energi saknas"
    # Only no sample at all means the first year is unfinished: a sample with
    # nothing consumed in it is a counter that stood still for a year.
    if basis == "first_year" and energy_in is None:
        return "första året är inte fullt ännu"
    if energy_out is None or energy_in is None:
        if basis == "day":
            return "väntar på ett prov som är 20 till 30 timmar gammalt"
        return "räknarna har inte lästs"
    if basis == "day":
        floor = MIN_CONSUMPTION_KWH_DAY
    elif basis in WINDOWS:
        floor = MIN_CONSUMPTION_KWH_DAY * max(1, days)
    else:
        floor = MIN_CONSUMPTION_KWH
    too_little = f"för lite energi ännu, {energy_in:.1f} av {floor:.0f} kWh"
    if basis == "day" or basis in WINDOWS:
        # A day's deltas are not the lifetime totals, so the lifetime rules do
        # not apply to them. Deltas of nothing are a day the compressor did not
        # run, not a counter the controller never writes, and below the floor
        # the pair is rounding noise, since the display counts whole kilowatt
        # hours and rounds the two counters independently: nothing is judged on
        # it. Above the floor a pair no heat pump could produce is still said so.
        # A week or a month of deltas is the same kind of number, only larger.
        if energy_in < floor:
            return too_little
        fault = counter_fault(energy_out, energy_in, None, floor)
    else:
        fault = counter_fault(energy_out, energy_in, hours)
    if fault == "stuck":
        # counter_fault says stuck only where the hours are known, so they are.
        return (
            f"räknaren står på noll fast kompressorn har gått {hours:.0f} timmar, "
            "styrenheten fyller den inte"
        )
    if fault == "implausible":
        return f"kvoten {round(energy_out / energy_in, 2)} är orimlig, räknarna hör inte ihop"
    if energy_in < floor:
        return too_little
    return None


#: The web coordinator's attribute for the moment of the last harvest that read
#: a page: CtcWebCoordinator.last_harvest, kept across a restart by the display
#: store. Read with a default so a test double without it still gets a notice:
#: the newest read_at then serves, which is the same moment to within seconds,
#: since read_at is only ever written by a read that worked.
LAST_HARVEST_ATTRIBUTE = "last_harvest"


def row_read_at(web: Any, key: str) -> datetime | None:
    """When the display row ``key`` was last read off the panel, or None.

    Through the coordinator's ``last_read`` where there is one; a double
    without it, as the sensor tests build, has never read anything.
    """
    read = getattr(web, "last_read", None)
    if not callable(read):
        return None
    moment = read(key)
    return moment if isinstance(moment, datetime) else None


def row_is_fresh(web: Any, key: str) -> bool | None:
    """Whether the row is fresh by the coordinator's own rule, or None if it cannot say."""
    fresh = getattr(web, "is_fresh", None)
    if not callable(fresh):
        return None
    return bool(fresh(key))


def stale_rows(web: Any, rows: Collection[Any]) -> list[tuple[Any, datetime]]:
    """The rows among ``rows`` that were read once and have since gone stale.

    A row that has never been read is not stale, it is unread, and the sensors
    say so in other words. Stale is the coordinator's judgement (R5): the
    page the row sits on has not been reached for as long as a whole harvest
    may fail in a row, which is when the row's own display sensor goes
    unavailable. Each stale row comes with the moment it was last read.
    """
    found: list[tuple[Any, datetime]] = []
    for row in rows:
        if row is None:
            continue
        read = row_read_at(web, row.key)
        if read is not None and row_is_fresh(web, row.key) is False:
            found.append((row, read))
    return found


def _row_name(row: Any) -> str:
    return (getattr(row, "label", None) or getattr(row, "key", None) or "?").strip()


def display_silence(web: Any, *rows: Any) -> str | None:
    """Why the display's figures are old: the reason text, or None while it answers.

    Two silences. After three failed harvests in a row the web coordinator
    reports failure and every display sensor goes unavailable; the figures
    that rest on stored samples stay available instead, and this is what they
    say about it, dated with the last harvest that read a page. Since R5 a
    page that is not reached is no failure of the harvest: the other pages
    are read, the coordinator reports success, and only that page's rows age
    in the data until their own sensors go unavailable. The counters the
    figures are read off are such rows, so they are handed in as ``rows``
    and a stale one gives the notice too, naming the row and dated with its
    own last read, instead of a figure read off a value nobody has read.
    """
    if web is None:
        return None
    if not getattr(web, "last_update_success", True):
        moment = getattr(web, LAST_HARVEST_ATTRIBUTE, None)
        if not isinstance(moment, datetime):
            stamps = [
                stamp for stamp in (getattr(web, "read_at", None) or {}).values()
                if isinstance(stamp, datetime)
            ]
            moment = max(stamps, default=None)
        if moment is None:
            return "displayen har inte svarat sedan Home Assistant startade"
        return f"displayen har inte svarat sedan {moment.isoformat(timespec='seconds')}"
    for row, read in stale_rows(web, rows):
        return (
            f"displayens rad {_row_name(row)} har inte lästs sedan "
            f"{read.isoformat(timespec='seconds')}"
        )
    return None


def reason_for(
    result: CopResult,
    out: float | None,
    consumed: float | None,
    hours: float | None = None,
    modbus_answered: bool = True,
    display: str | None = None,
) -> str | None:
    """Why the sensor for ``result`` shows nothing, the nearest cause first.

    A display that has gone quiet comes before everything, because every
    other answer would be read off counters that are no longer being read; the
    first year is the exception, since its figure is stored and the display
    cannot age it. Then a Modbus register that never answered, then the
    tracker's own account of its samples, then whether the counters have been
    read at all, and last the counters' pair itself.
    """
    if display is not None and result.basis != "first_year":
        return display
    if consumed is None and not modbus_answered:
        return "registret 62341 har inte svarat, så tillförd energi saknas"
    if result.value is not None:
        return None
    if result.reason is not None:
        return result.reason
    if out is None or consumed is None:
        if result.basis == "first_year":
            return "första året är inte fullt ännu"
        return "räknarna har inte lästs"
    return cop_reason(
        None, result.basis, result.energy_out, result.energy_in, hours, modbus_answered,
        days=result.days,
    )


# ------------------------------------------------- the share of auxiliary heat
#
# How much of the electricity the unit used went to the immersion heater, the
# auxiliary heat, rather than to the compressor (roadmap R28): the immersion
# heater's counter, Modbus 62191, against the sum of it and the compressor's
# counter, the same consumed energy the coefficient of performance divides by.
# Over the coefficient of performance's own spans, from the same samples, but
# for the first year, whose figure was kept before the immersion heater's
# counter was sampled. A system coefficient of performance with the immersion
# heater counted in was left out on purpose: on an i255 whose immersion heater
# has used 1 of 9358 kWh it is the same figure, and on an i550 Pro whose
# compressor counter stands at zero it is 1.0 by definition. Never read off the
# display's "/30 dagar" rows, and never sent to the statistics backend.

#: The spans the share is given over.
SHARE_SPANS: tuple[str, ...] = ("day", "week", "month", "year", "lifetime")

#: Why a share is missing, where the words are the same for every span.
SHARE_NOT_SAMPLED = (
    "proven i underlaget saknar elpatronens räknare; nyare prov har den, så andelen kommer "
    "när ett sådant prov är gammalt nog"
)
SHARE_IMMERSION_UNREAD = (
    "elpatronens räknare har inte lästs ihop med displayens räknare sedan Home Assistant startade"
)
SHARE_IMMERSION_SILENT = "registret 62191 har inte svarat, så elpatronens räknare saknas"
SHARE_BOTH_STUCK = (
    "båda räknarna står på noll fast enheten varit igång, styrenheten fyller dem inte"
)


@dataclass
class ShareResult:
    """The immersion heater's share of the electricity, and what it rests on."""

    #: Per cent, one decimal, or None with the reason why.
    value: float | None
    #: The span, by the same names as :class:`CopResult`'s.
    basis: str
    days: int
    #: The immersion heater's and the compressor's energy across the span, kWh.
    immersion: float | None = None
    compressor: float | None = None
    reason: str | None = None

    def as_attributes(self) -> dict[str, Any]:
        return {
            "underlag": BASIS_WORDS.get(self.basis, self.basis),
            "dygn i underlaget": self.days,
            "elpatron kWh": self.immersion,
            "kompressor kWh": self.compressor,
        }


def share_value(immersion: float, compressor: float) -> float:
    """The immersion heater's per cent of the two, never 100 while the compressor used any.

    One decimal: an immersion heater that used 1 of 9358 kWh is 0.0 per cent,
    which is what it is, and the attributes carry the kilowatt hours. At the
    other end a hundred per cent says that the compressor used nothing at all,
    so a compressor that used a little is 99.9 however the rounding falls.
    """
    value = round(100.0 * immersion / (immersion + compressor), 1)
    return min(value, 99.9) if compressor > 0 else value


def _share_result(
    basis: str,
    days: int,
    immersion: float,
    compressor: float,
    floor: float,
    since: str,
) -> ShareResult:
    """The share across a span from its two deltas, or the reason there is none."""
    if immersion < 0 or compressor < 0:
        # A counter that went backwards: the unit was replaced or reset, and
        # no share across that is honest.
        return ShareResult(
            None, basis, days,
            reason=f"räknarna har gått bakåt sedan {since}, enheten är bytt eller nollställd",
        )
    shown = (round(immersion, 1), round(compressor, 1))
    total = immersion + compressor
    if total <= 0:
        return ShareResult(
            None, basis, days, *shown, reason=f"båda räknarna har stått still sedan {since}"
        )
    if total < floor:
        return ShareResult(
            None, basis, days, *shown,
            reason=f"för lite energi ännu, {total:.1f} av {floor:.0f} kWh",
        )
    return ShareResult(share_value(immersion, compressor), basis, days, *shown)


def compressor_counter_fault(
    compressor: float | None, compressor_hours: float | None
) -> str | None:
    """Why no share can be trusted: a compressor counter at zero on a compressor that has run.

    A hundred per cent auxiliary heat is the answer only where the compressor
    has really used nothing. Its counter at zero beside a running time of a
    day or more (Modbus 62214) is a controller that does not fill the counter,
    and every share would then be the immersion heater's alone, whatever the
    compressor did.
    """
    if compressor is None or compressor_hours is None:
        return None
    if compressor <= 0 and compressor_hours >= RUNNING_HOURS:
        return (
            f"kompressorns räknare står på noll fast kompressorn har gått "
            f"{compressor_hours:.0f} timmar, styrenheten fyller den inte"
        )
    return None


def _row_immersion(row: Any, index: int) -> float | None:
    """The immersion heater's counter in a stored sample, or None where it was not read."""
    if len(row) <= index:
        return None
    value = row[index]
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
        return None
    return float(value)


def _known(value: float | None) -> list[float]:
    """A sample's third number as it is stored: there where known, left out where not.

    Left out rather than written as null, so a sample whose immersion heater
    is unknown is stored exactly as minor 1 stored every sample.
    """
    return [] if value is None else [value]


class CopTracker:
    """Keeps one sample a day of the two lifetime counters, and the immersion heater's.

    A sample is delivered heat and consumed energy, and since roadmap R28 the
    immersion heater's counter (Modbus 62191) as a third number where it was
    read with them. A sample without the third number, as every sample from
    before then is, has an immersion heater that is unknown: the coefficient
    of performance reads it as always, and the share of auxiliary heat passes
    it over.
    """

    def __init__(self, store: Any) -> None:
        self._store = store
        self._samples: dict[str, list[float]] = {}
        #: A short run of timestamped samples. The daily figure needs finer
        #: spacing than one a day, or "yesterday" could be anything from 12 to
        #: 36 hours ago depending on when the samples happened to land. Each
        #: is (moment, delivered, consumed) or, with the immersion heater's
        #: counter read, (moment, delivered, consumed, immersion heater).
        self._recent: list[tuple[Any, ...]] = []
        #: The day the lifetime counters started from zero, when known. CTC's
        #: counters begin at nothing the day the unit is commissioned, which
        #: makes that day a sample of its own: zero delivered, zero consumed.
        self._anchor: str | None = None
        #: The first year of the machine's life, kept once it has been seen.
        self._first_year: list[float] | None = None
        self._loaded = False

    async def async_load(self) -> None:
        if self._loaded:
            return
        data = await self._store.async_load()
        if isinstance(data, dict) and isinstance(data.get("samples"), dict):
            # Minor 1 wrote two numbers a sample, minor 2 a third where the
            # immersion heater's counter was read; the shape decides, not the
            # version, since a release before minor 2 may have written the
            # file back.
            self._samples = {
                day: [float(values[0]), float(values[1]), *_known(_row_immersion(values, 2))]
                for day, values in data["samples"].items()
                if isinstance(values, (list, tuple)) and len(values) >= 2
            }
        if isinstance(data, dict):
            anchor = data.get("anchor")
            self._anchor = anchor if isinstance(anchor, str) else None
            first = data.get("first_year")
            if isinstance(first, (list, tuple)) and len(first) >= 3:
                self._first_year = [float(first[0]), float(first[1]), float(first[2])]
        if isinstance(data, dict) and isinstance(data.get("recent"), list):
            for row in data["recent"]:
                if isinstance(row, (list, tuple)) and len(row) >= 3:
                    try:
                        self._recent.append(
                            (str(row[0]), float(row[1]), float(row[2]),
                             *_known(_row_immersion(row, 3)))
                        )
                    except (TypeError, ValueError):
                        continue
        self._loaded = True

    async def async_record(
        self,
        energy_out: float | None,
        energy_in: float | None,
        today: date | None = None,
        now: datetime | None = None,
        immersion: float | None = None,
    ) -> None:
        """Store the counters: one per day for the year, and a timestamped run.

        ``now`` is the moment the counters were read off the panel, where the
        caller knows it. A reading is recorded once: a moment no newer than
        the newest sample already kept is the same reading coming round again,
        as it does when the page the counters sit on has not been reached
        since, or when the pair comes back out of the display store after a
        restart, and recording it again would stamp a day's sample with a
        pair that was read on another day.

        ``immersion`` is the immersion heater's counter as it stood at that
        same moment, where it was read then; without it the sample is kept
        as before, and its immersion heater is unknown.
        """
        if energy_out is None or energy_in is None:
            return
        await self.async_load()
        if now is not None:
            newest = self.last_sample_at
            if newest is not None and now <= newest:
                return
        when = now or datetime.now(UTC)
        stamp = (today or when.date()).isoformat()
        heater = _known(_row_immersion((immersion,), 0))
        self._samples[stamp] = [float(energy_out), float(energy_in), *heater]
        cutoff = ((today or when.date()) - timedelta(days=COP_HISTORY_DAYS)).isoformat()
        self._samples = {d: v for d, v in self._samples.items() if d >= cutoff}

        self._recent.append((when.isoformat(), float(energy_out), float(energy_in), *heater))
        keep = when - timedelta(days=RECENT_DAYS)
        self._recent = [r for r in self._recent if _parse(r[0]) >= keep]

        self._capture_first_year(when.date())
        await self._async_save()

    async def _async_save(self) -> None:
        await self._store.async_save(
            {
                "samples": self._samples,
                "recent": self._recent,
                "anchor": self._anchor,
                "first_year": self._first_year,
            }
        )

    async def async_set_anchor(self, commissioned: date) -> None:
        """Record the day the counters started from zero.

        Only ever moved earlier, never later: the operating hours it is worked
        out from stop counting while the unit is switched off, so the earliest
        answer seen is the closest to the truth.
        """
        await self.async_load()
        stamp = commissioned.isoformat()
        if self._anchor is None or stamp < self._anchor:
            self._anchor = stamp
            await self._async_save()

    @property
    def anchor(self) -> date | None:
        return date.fromisoformat(self._anchor) if self._anchor else None

    @property
    def last_sample_at(self) -> datetime | None:
        """When the newest timestamped sample was read, or None without one."""
        stamps = []
        for row in self._recent:
            try:
                stamps.append(_parse(row[0]))
            except (TypeError, ValueError):
                continue
        return max(stamps, default=None)

    def _capture_first_year(self, today: date) -> None:
        """Keep the first year's figure once a sample from its end exists.

        The counters were zero on the anchor day, so a sample taken about a year
        later holds the whole first year on its own. It is kept for good: the
        machine only ever has one first year.
        """
        if self._first_year is not None or self._anchor is None:
            return
        start = date.fromisoformat(self._anchor)
        for day in sorted(self._samples):
            span = (date.fromisoformat(day) - start).days
            if COP_WINDOW_DAYS <= span <= YEAR_MAX_DAYS:
                out, consumed = self._samples[day][:2]
                self._first_year = [float(out), float(consumed), float(span)]
                return

    def result_first_year(self) -> CopResult:
        """The machine's first year, or nothing until it has had one.

        Without the commissioning day there will never be one: it is worked
        out from the display's powered-on hours, and a page without that row,
        as the i550 Pro's history page is, leaves the start unknown. Said so,
        rather than promising a year that is not coming.
        """
        if self._first_year is None:
            reason = "driftstarten är okänd" if self._anchor is None else None
            return CopResult(None, "first_year", 0, reason=reason)
        out, consumed, span = self._first_year
        return CopResult(
            _ratio(out, consumed), "first_year", int(span), round(out, 1), round(consumed, 1)
        )

    def result_day(
        self,
        energy_out: float | None,
        energy_in: float | None,
        now: datetime | None = None,
    ) -> CopResult:
        """The figure over the last day, from the newest sample old enough to be
        yesterday. Nothing is returned until such a sample exists."""
        if energy_out is None or energy_in is None:
            return CopResult(None, "day", 0)
        when = now or datetime.now(UTC)
        oldest_allowed = when - timedelta(hours=DAY_MAX_HOURS)
        newest_allowed = when - timedelta(hours=DAY_MIN_HOURS)
        window = [
            r for r in self._recent if oldest_allowed <= _parse(r[0]) <= newest_allowed
        ]
        if not window:
            return CopResult(
                None, "day", 0, reason="väntar på ett prov som är 20 till 30 timmar gammalt"
            )
        stamp, base_out, base_in = max(window, key=lambda r: _parse(r[0]))[:3]
        delta_out = energy_out - base_out
        delta_in = energy_in - base_in
        if delta_out < 0 or delta_in < 0:
            return CopResult(None, "day", 0)
        hours = (when - _parse(stamp)).total_seconds() / 3600
        value = _ratio(delta_out, delta_in, MIN_CONSUMPTION_KWH_DAY)
        if value is None:
            return CopResult(
                None, "day", round(hours / 24), round(delta_out, 1), round(delta_in, 1)
            )
        return CopResult(
            value,
            "day",
            max(1, round(hours / 24)),
            round(delta_out, 1),
            round(delta_in, 1),
        )

    def _window(
        self,
        energy_out: float,
        energy_in: float,
        basis: str,
        days: int,
        tolerance: int,
        floor: float,
        now: date,
        per_day: bool = False,
    ) -> CopResult:
        """The figure across a window, from the newest sample at least ``days`` old.

        The base sample may lie up to ``tolerance`` days further back, so a gap
        of a day or two in the samples does not cost the figure; beyond that
        the span would no longer be what the name says. Without a sample in
        that band the result says how far the samples have come, or that a gap
        in them covers the band, which is the tracker's own knowledge. What
        the counters themselves have to say is left to :func:`cop_reason`.

        With ``per_day`` the floor is the daily one times the days the figure
        actually spans: a week's deltas are a week of whole kilowatt hours, and
        the rounding they carry is the same as a day's, so what holds for three
        over a day holds for twenty-one over seven.
        """
        window_start = (now - timedelta(days=days)).isoformat()
        earliest = (now - timedelta(days=days + tolerance)).isoformat()
        older = sorted(d for d in self._samples if earliest <= d <= window_start)
        if older:
            base_out, base_in = self._samples[older[-1]][:2]
            span = (now - date.fromisoformat(older[-1])).days
            delta_out = energy_out - base_out
            delta_in = energy_in - base_in
            if delta_out < 0 or delta_in < 0:
                # A counter that went backwards means the unit was replaced or
                # reset. No quotient across that is honest.
                return CopResult(
                    None, basis, span,
                    reason=(
                        f"räknarna har gått bakåt sedan {older[-1]}, "
                        "enheten är bytt eller nollställd"
                    ),
                )
            if per_day:
                floor = floor * max(1, span)
            return CopResult(
                _ratio(delta_out, delta_in, floor), basis, span,
                round(delta_out, 1), round(delta_in, 1),
            )
        collected, reason = self._progress(days, tolerance, now)
        return CopResult(None, basis, collected, reason=reason)

    def _progress(
        self,
        days: int,
        tolerance: int,
        now: date,
        sampled: Collection[str] | None = None,
    ) -> tuple[int, str]:
        """How far the samples have come towards a window without a sample in its band.

        The days to report and the reason: nothing kept yet, a gap in the
        samples that covers the band, or so many days collected since the
        oldest sample. The same for every figure that reads the daily samples.
        ``sampled`` narrows the samples counted to those days, for a figure
        that can only stand on some of them; without it every daily sample
        counts, as the coefficient of performance's do.
        """
        kept = self._samples if sampled is None else sampled
        if not kept:
            return 0, f"0 av {days} dygn samlade, inget prov sparat ännu"
        oldest = min(kept)
        collected = (now - date.fromisoformat(oldest)).days
        if collected > days + tolerance:
            return days, f"inget sparat prov är {days} till {days + tolerance} dygn gammalt"
        collected = max(0, min(collected, days))
        return collected, f"{collected} av {days} dygn samlade, äldsta provet {oldest}"

    def result_year(
        self,
        energy_out: float | None,
        energy_in: float | None,
        today: date | None = None,
    ) -> CopResult:
        """The rolling year, or how far the samples have come towards one.

        Only the tracker's own samples count. The commissioning day is not
        one of them here: it made the first anniversary look like a rolling
        year for the fifteen days the anchor lay inside the band, after which
        the figure vanished until the samples had reached a year of their own.
        The first year carries that figure instead, for good.
        """
        if energy_out is None or energy_in is None:
            return CopResult(None, "year", 0)
        now = today or date.today()
        return self._window(
            energy_out, energy_in, "year",
            COP_WINDOW_DAYS, YEAR_MAX_DAYS - COP_WINDOW_DAYS, MIN_CONSUMPTION_KWH, now,
        )

    def result_window(
        self,
        energy_out: float | None,
        energy_in: float | None,
        basis: str,
        today: date | None = None,
    ) -> CopResult:
        """The figure over one of the shorter windows in :data:`WINDOWS`.

        Between the day and the year there was nothing: a lifetime figure says
        nothing about this autumn, and the display's own "/30 dagar" rows
        stand at zero on both units they have been read from. The daily
        samples the tracker keeps for the year carry a week and a month from
        the day they are old enough, which on a fresh installation is a week
        and a month after it was set up.
        """
        days, tolerance = WINDOWS[basis]
        if energy_out is None or energy_in is None:
            return CopResult(None, basis, 0)
        now = today or date.today()
        return self._window(
            energy_out, energy_in, basis, days, tolerance, MIN_CONSUMPTION_KWH_DAY, now,
            per_day=True,
        )

    def result_lifetime(
        self,
        energy_out: float | None,
        energy_in: float | None,
        today: date | None = None,
    ) -> CopResult:
        """The figure over everything the counters have counted.

        The one span that needs no waiting, and the one that must never be
        reported as anything else. The days it covers are counted from the
        commissioning day where that is known, since the counters started from
        zero then, and otherwise from the oldest sample the tracker holds.
        """
        if energy_out is None or energy_in is None:
            return CopResult(None, "lifetime", 0)
        now = today or date.today()
        since = self._anchor or (min(self._samples) if self._samples else None)
        span = max(0, (now - date.fromisoformat(since)).days) if since else 0
        return CopResult(
            _ratio(energy_out, energy_in),
            "lifetime",
            span,
            round(energy_out, 1),
            round(energy_in, 1),
        )

    def result(
        self,
        energy_out: float | None,
        energy_in: float | None,
        today: date | None = None,
    ) -> CopResult:
        """The rolling year where there is one, otherwise the lifetime figure.

        For callers that want one number whatever its span, with the span
        stated in ``basis``. The sensors ask for each span by name instead.
        """
        year = self.result_year(energy_out, energy_in, today)
        if year.value is not None:
            return year
        return self.result_lifetime(energy_out, energy_in, today)

    # --------------------------------------------- the share of auxiliary heat

    def share_day(
        self,
        compressor: float | None,
        immersion: float | None,
        now: datetime | None = None,
    ) -> ShareResult:
        """The immersion heater's share over the last day.

        Against the newest sample 20 to 30 hours old, as the day's coefficient
        of performance, among those that carry the immersion heater's counter:
        a sample from before it was sampled, or one written down at a restart
        before the counter had been read with the display, is passed over
        rather than taken for zero.
        """
        if compressor is None or immersion is None:
            return ShareResult(None, "day", 0)
        when = now or datetime.now(UTC)
        oldest_allowed = when - timedelta(hours=DAY_MAX_HOURS)
        newest_allowed = when - timedelta(hours=DAY_MIN_HOURS)
        window = [
            r for r in self._recent if oldest_allowed <= _parse(r[0]) <= newest_allowed
        ]
        if not window:
            return ShareResult(
                None, "day", 0,
                reason=(
                    f"väntar på ett prov som är {DAY_MIN_HOURS} till {DAY_MAX_HOURS} "
                    "timmar gammalt"
                ),
            )
        known = [r for r in window if _row_immersion(r, 3) is not None]
        if not known:
            return ShareResult(None, "day", 0, reason=SHARE_NOT_SAMPLED)
        base = max(known, key=lambda r: _parse(r[0]))
        moment = _parse(base[0])
        hours = (when - moment).total_seconds() / 3600
        return _share_result(
            "day",
            max(1, round(hours / 24)),
            immersion - (_row_immersion(base, 3) or 0.0),
            compressor - base[2],
            MIN_CONSUMPTION_KWH_DAY,
            moment.isoformat(timespec="minutes"),
        )

    def _share_window(
        self,
        compressor: float,
        immersion: float,
        basis: str,
        days: int,
        tolerance: int,
        floor: float,
        now: date,
        per_day: bool = False,
    ) -> ShareResult:
        """The share across a window, from the newest daily sample in its band that has the counter.

        The band and the floor are the coefficient of performance's for the
        same span (:meth:`_window`). A sample without the immersion heater's
        counter is passed over: the share starts a week, a month and a year
        after the counter is first sampled, never across a sample that does
        not have it. So is the waiting counted: without a sample in the band,
        the days collected and the oldest sample are those of the samples
        with the counter, since a year of samples from before it was sampled
        brings the share no nearer.
        """
        window_start = (now - timedelta(days=days)).isoformat()
        earliest = (now - timedelta(days=days + tolerance)).isoformat()
        older = sorted(d for d in self._samples if earliest <= d <= window_start)
        if older:
            known = [d for d in older if _row_immersion(self._samples[d], 2) is not None]
            if not known:
                span = (now - date.fromisoformat(older[-1])).days
                return ShareResult(None, basis, span, reason=SHARE_NOT_SAMPLED)
            base = known[-1]
            span = (now - date.fromisoformat(base)).days
            if per_day:
                floor = floor * max(1, span)
            return _share_result(
                basis,
                span,
                immersion - (_row_immersion(self._samples[base], 2) or 0.0),
                compressor - self._samples[base][1],
                floor,
                base,
            )
        sampled = [d for d in self._samples if _row_immersion(self._samples[d], 2) is not None]
        if self._samples and not sampled:
            # Not SHARE_NOT_SAMPLED, which says that newer samples have the
            # counter: none has yet, as on the day of the update that brought
            # it, when every sample kept is from before.
            return ShareResult(
                None, basis, 0,
                reason=(
                    f"0 av {days} dygn samlade, inget prov med elpatronens räknare sparat ännu"
                ),
            )
        collected, reason = self._progress(days, tolerance, now, sampled)
        return ShareResult(None, basis, collected, reason=reason)

    def share_window(
        self,
        compressor: float | None,
        immersion: float | None,
        basis: str,
        today: date | None = None,
    ) -> ShareResult:
        """The share over the last week or month, the spans in :data:`WINDOWS`."""
        days, tolerance = WINDOWS[basis]
        if compressor is None or immersion is None:
            return ShareResult(None, basis, 0)
        return self._share_window(
            compressor, immersion, basis, days, tolerance, MIN_CONSUMPTION_KWH_DAY,
            today or date.today(), per_day=True,
        )

    def share_year(
        self,
        compressor: float | None,
        immersion: float | None,
        today: date | None = None,
    ) -> ShareResult:
        """The share over a rolling year, where a year of samples with the counter exists."""
        if compressor is None or immersion is None:
            return ShareResult(None, "year", 0)
        return self._share_window(
            compressor, immersion, "year",
            COP_WINDOW_DAYS, YEAR_MAX_DAYS - COP_WINDOW_DAYS, MIN_CONSUMPTION_KWH,
            today or date.today(),
        )

    def share_lifetime(
        self,
        compressor: float | None,
        immersion: float | None,
        today: date | None = None,
        hours: float | None = None,
    ) -> ShareResult:
        """The share over everything the two counters have counted.

        Both at zero where the compressor has run for a day (``hours``, the
        compressor's own hours from Modbus 62214) is a controller that fills
        neither, said as such; before that it is too little energy so far. The
        compressor's hours and not the unit's, for the reason counter_fault
        gives: a machine can stand powered on for years without running. The days are
        counted as the lifetime coefficient of performance counts them.
        """
        if compressor is None or immersion is None:
            return ShareResult(None, "lifetime", 0)
        now = today or date.today()
        since = self._anchor or (min(self._samples) if self._samples else None)
        span = max(0, (now - date.fromisoformat(since)).days) if since else 0
        if compressor < 0 or immersion < 0:
            return ShareResult(None, "lifetime", span, reason="räknarna har inte lästs")
        if compressor + immersion <= 0:
            if hours is not None and hours >= RUNNING_HOURS:
                return ShareResult(None, "lifetime", span, 0.0, 0.0, reason=SHARE_BOTH_STUCK)
            return ShareResult(
                None, "lifetime", span, 0.0, 0.0,
                reason=f"för lite energi ännu, 0.0 av {MIN_CONSUMPTION_KWH:.0f} kWh",
            )
        return _share_result(
            "lifetime", span, immersion, compressor, MIN_CONSUMPTION_KWH, since or "driftstarten"
        )


#: The older name of the delivered heat counter. The display's text catalogue
#: holds both generations (read off an i255 on 2026-09-15): text 935 "Energy
#: output (kWh)", in Swedish "Avgiven energi (kWh)", which is the row CTC's
#: manual shows on the i360's stored operation data page, beside 1808 "Energy
#: output total (kWh)" / "Avgiven värme totalt (kWh)" that the i255 shows. The
#: older generation has no consumed energy counter at all. The name is only
#: trusted on a row in kWh: 1807 "Energy output (kW)" is power, and sits on the
#: heat pump's operation data page.
_HEAT_NAMES = ("energy output", "avgiven energi")


def find_energy_totals(pages: list[Any]) -> tuple[Any | None, Any | None]:
    """Pick the two lifetime counters out of the harvested pages.

    Matched on the label the display itself printed, in English first and then
    in Swedish, so it works whichever language the integration reads the panel
    in. The newer names win wherever they appear on the page; only without them
    is the older name for delivered heat accepted. Consumed energy has no older
    name, and comes from Modbus instead where the display lacks it.
    """
    from .const import (
        LABEL_ENERGY_IN_EN,
        LABEL_ENERGY_IN_SV,
        LABEL_ENERGY_OUT_EN,
        LABEL_ENERGY_OUT_SV,
    )

    def label(value: Any) -> str:
        # The catalogue strips a trailing unit from the row name, so the stored
        # label is "Avgiven värme totalt" rather than "... (kWh)".
        return (value.label or "").strip().casefold()

    def exact(value: Any, english: str, swedish: str) -> bool:
        return label(value).startswith(english.casefold()) or label(value).startswith(
            swedish.casefold()
        )

    def loose(value: Any, names: tuple[str, ...]) -> bool:
        text = label(value)
        return (
            (getattr(value, "unit", None) or "").casefold() == "kwh"
            and text.startswith(names)
            and not any(marker in text for marker in PERIOD_MARKERS)
        )

    values = [value for page in pages for value in page.values]
    out = next(
        (v for v in values if exact(v, LABEL_ENERGY_OUT_EN, LABEL_ENERGY_OUT_SV)), None
    ) or next((v for v in values if loose(v, _HEAT_NAMES)), None)
    consumed = next(
        (v for v in values if exact(v, LABEL_ENERGY_IN_EN, LABEL_ENERGY_IN_SV)), None
    )
    return out, consumed


#: Modbus register 62341, the energy the compressor has consumed, in kWh.
MODBUS_CONSUMPTION_KEY = "compressor_kwh"
MODBUS_CONSUMPTION_ADDRESS = next(
    d.address for d in MODBUS_SENSORS if d.key == MODBUS_CONSUMPTION_KEY
)


def modbus_consumption_answered(answered: Collection[int] | None) -> bool:
    """Whether the controller has answered for register 62341 at all.

    Judged on the raw addresses the coordinator keeps in ``answered``, never on
    its decoded data. A key is left out of the data both when the block was
    silent this round and when the pair decoded to CTC's marker for a counter
    that is not fitted, so read off the data, a register that answered with the
    marker and a block that was quiet once would both pass for a register the
    model lacks. The set is cumulative over the run: answered means answered at
    least once since Home Assistant started, and a block the model lacks never
    joins it. Whether the answer is a number worth dividing by is a different
    question, which :func:`modbus_consumption` settles, and the two are kept
    apart on purpose: a register that answers zero is fitted and can be found
    to be stuck, while one that never answers has nothing to say about the
    machine.
    """
    return MODBUS_CONSUMPTION_ADDRESS in (answered or ())


def modbus_consumption(data: dict[str, Any] | None) -> float | None:
    """The consumed energy from Modbus, or None where there is no reading.

    Zero is a reading and is kept. A counter standing at zero on a unit that
    has been switched on for a day is the fault called ``stuck``, and the only
    way to find it is to carry the zero through to :func:`counter_fault`.
    Turning it into None here, as this once did, made such a machine look as
    if the register were missing: on an i360, whose display has no consumed
    energy counter, that meant no coefficient of performance sensors at all
    until somebody reloaded the entry after the counter had moved. None is for
    a register that was not read, a value that is not a number, and CTC's
    sentinels for "no sensor fitted"; a counter below zero is none of those
    and no reading either.
    """
    value = (data or {}).get(MODBUS_CONSUMPTION_KEY)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if value < 0 or value in SENTINELS:
        return None
    return float(value)


class ConsumptionSnapshot:
    """Modbus's consumed energy as it stood when the display was last read.

    The display is harvested every half hour by default and keeps its previous
    value when a cycle is skipped, while Modbus is read every thirty seconds.
    Dividing a stale delivered heat by a fresh consumption would move the daily
    figure by up to half an hour of compressor running, so the consumption is
    taken at the moment the delivered heat counter was actually read.
    """

    def __init__(self) -> None:
        self.value: float | None = None
        self._read_at: Any = None

    def seed(self, read_at: Any, value: float | None) -> None:
        """Start from a pair written down before a restart.

        The display value comes back from its store with the moment it was
        read, and the Modbus reading that was taken at that moment comes back
        here, so the pair is the same pair; a fresh Modbus reading paired with
        the old display value would be off by however long Home Assistant
        was away.
        """
        if read_at is None:
            return
        self._read_at = read_at
        self.value = value

    def update(self, read_at: Any, modbus_data: dict[str, Any] | None) -> None:
        """Take the Modbus reading if the display has been read since last time."""
        if read_at is None or read_at == self._read_at:
            return
        self._read_at = read_at
        # A Modbus value that is not usable right now must not be paired with
        # the new display reading either; better no sample than a wrong one.
        self.value = self._read(modbus_data)

    def value_at(self, read_at: Any) -> float | None:
        """The value taken at ``read_at``, or None where the snapshot is of another moment."""
        if read_at is None or read_at != self._read_at:
            return None
        return self.value

    @staticmethod
    def _read(modbus_data: dict[str, Any] | None) -> float | None:
        return modbus_consumption(modbus_data)


#: Modbus register 62191, the energy the immersion heater has used, in kWh.
MODBUS_IMMERSION_KEY = "immersion_kwh"
MODBUS_IMMERSION_ADDRESS = next(d.address for d in MODBUS_SENSORS if d.key == MODBUS_IMMERSION_KEY)


def modbus_immersion_answered(answered: Collection[int] | None) -> bool:
    """Whether the controller has answered for register 62191 at all, as for 62341."""
    return MODBUS_IMMERSION_ADDRESS in (answered or ())


def modbus_immersion(data: dict[str, Any] | None) -> float | None:
    """The immersion heater's energy from Modbus, or None where there is no reading.

    By the same rules as :func:`modbus_consumption`: zero is a reading, and
    a value that is no number, below zero or one of CTC's markers is none.
    """
    value = (data or {}).get(MODBUS_IMMERSION_KEY)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if value < 0 or value in SENTINELS:
        return None
    return float(value)


class ImmersionSnapshot(ConsumptionSnapshot):
    """Modbus 62191 as it stood when the display's delivered heat was last read (R28).

    The share of auxiliary heat sets the immersion heater's counter against
    the compressor's, which is read with the display, so the two are taken at
    the same moment for the reason :class:`ConsumptionSnapshot` gives. It is
    written down with the harvest, as the consumption is, and seeded back as
    the pair of the moment the display store comes back with, so the sample
    taken at start-up keeps it rather than replacing the day's sample with
    one that has none. Where the store has none, as one written before it
    was kept, the snapshot is empty until the display is read again, and a
    sample written down meanwhile has no immersion heater, which the share
    passes over.
    """

    @staticmethod
    def _read(modbus_data: dict[str, Any] | None) -> float | None:
        return modbus_immersion(modbus_data)


def find_power_pair(pages: list[Any]) -> tuple[Any | None, Any | None]:
    """The display's two power rows, where the heat pump's page is harvested.

    Matched the way the lifetime counters are, on the label the display printed
    and only on a row in kilowatts, so the counters on the history page and the
    rows for a period cannot be taken for a power. These two are what the
    integration can make its own energy out of where the machine's counters stay
    at zero; see integrate.py.
    """
    from .const import (
        LABEL_POWER_IN_EN,
        LABEL_POWER_IN_SV,
        LABEL_POWER_OUT_EN,
        LABEL_POWER_OUT_SV,
        LABEL_POWER_OUT_SV_OLD,
    )

    def match(value: Any, names: tuple[str, ...]) -> bool:
        label = (getattr(value, "label", None) or "").strip().casefold()
        return (
            (getattr(value, "unit", None) or "").casefold() == "kw"
            and label.startswith(names)
            and not any(marker in label for marker in PERIOD_MARKERS)
        )

    values = [value for page in pages for value in page.values]
    out = next(
        (
            v
            for v in values
            if match(v, (LABEL_POWER_OUT_EN.casefold(), LABEL_POWER_OUT_SV.casefold(),
                         LABEL_POWER_OUT_SV_OLD.casefold()))
        ),
        None,
    )
    supplied = (LABEL_POWER_IN_EN.casefold(), LABEL_POWER_IN_SV.casefold())
    consumed = next((v for v in values if match(v, supplied)), None)
    return out, consumed


def live_page_default(pages: list[Any]) -> int | None:
    """The page a new set-up offers as the live page: the heat pump's, or none.

    The page with the two power rows, "Driftinfo Värmepump" on an i255 and
    "Kompressor VP1" on an i550 Pro, is the one whose values change by the
    minute and the one the owner asked to follow live; the counters on the
    history page move too slowly to be worth a panel that rests on them.
    Where no page carries both powers nothing is offered.
    """
    out, consumed = find_power_pair(pages)
    if out is None or consumed is None:
        return None
    return int(out.page)


def find_operating_hours(pages: list[Any]) -> Any | None:
    """Pick the unit's total powered-on hours out of the harvested pages.

    Two rows look alike, "Total drifttid" for the hours the unit has been
    powered and "Drifttid total" for the compressor alone, and in English they
    are both "Total operation time". Powered-on hours can never be fewer than
    compressor hours, so of the rows that match, the largest one is the right
    one; the choice is made at read time, from the values themselves.
    """
    matches = []
    for page in pages:
        for value in page.values:
            label = (value.label or "").strip().casefold()
            if (value.unit or "") != "h":
                continue
            if label.startswith("total drifttid") or label.startswith("total operation time"):
                matches.append(value)
    return matches or None



def current_totals(runtime: Any) -> tuple[float | None, float | None]:
    """The two energy totals behind every figure, as they stood at the last read.

    Consumption comes from the display's own counter where there is one, and
    otherwise from the Modbus reading taken when delivered heat was read.

    Where the machine's counters stand at zero, which is what every installation
    in the fleet with a 400 or 600 series outdoor unit does, the integration's own
    sums step in: the two powers on the
    heat pump's page, added up over time (integrate.py). **Both sides always come
    from the same source.** A counter for one and an integral for the other would
    be two different spans divided by each other, and the quotient would mean
    nothing at all.
    """
    if getattr(runtime, "web", None) is None:
        return None, None
    data = runtime.web.data or {}
    out = data.get(runtime.energy_out.key) if runtime.energy_out else None
    if runtime.energy_in is not None:
        consumed = data.get(runtime.energy_in.key)
    else:
        snapshot = getattr(runtime, "consumption_snapshot", None)
        consumed = snapshot.value if snapshot is not None else None
    if out and consumed:
        return out, consumed
    integrator = getattr(runtime, "integrator", None)
    if integrator is not None:
        own_out, own_in = integrator.totals
        if own_out and own_in:
            return own_out, own_in
    return out, consumed


def totals_source(runtime: Any) -> tuple[str, str]:
    """Where the pair above came from, in the words the attributes show.

    Said per side, because the consumed side has a route of its own: the register
    Modbus holds is the same counter the display prints, only fresher.
    """
    out, consumed = current_totals(runtime)
    integrator = getattr(runtime, "integrator", None)
    if integrator is not None and (out, consumed) == integrator.totals and out:
        own = "egen summering av displayens momentana effekt"
        return own, own
    return (
        "displayen",
        "displayen"
        if getattr(runtime, "energy_in", None) is not None
        else "Modbus 62341",
    )


def counter_rows(runtime: Any) -> list[Any]:
    """The display rows the two lifetime counters are read off.

    Delivered heat always; consumed energy only where the display has the
    row, since on the Modbus route the consumption is a register taken at
    the moment delivered heat was read, and shares that row's age.
    """
    rows = [getattr(runtime, "energy_out", None), getattr(runtime, "energy_in", None)]
    return [row for row in rows if row is not None]


def counters_read_at(runtime: Any) -> datetime | None:
    """When the pair :func:`current_totals` returns was read off the panel.

    None unless every counter row has been read and is still fresh by the
    coordinator's rule (R5), so a page that has stopped being reached gives
    no sample at all rather than yesterday's pair stamped with today. The
    moment is the delivered heat row's, which is also the moment the Modbus
    consumption was paired with it; where both counters sit on the display
    they sit on the same page and share it.
    """
    web = getattr(runtime, "web", None)
    rows = counter_rows(runtime)
    if web is None or not rows:
        return None
    for row in rows:
        if row_read_at(web, row.key) is None or row_is_fresh(web, row.key) is False:
            return None
    return row_read_at(web, rows[0].key)


def immersion_now(runtime: Any) -> float | None:
    """The immersion heater's counter as it stood when the display's counters were last read."""
    snapshot = getattr(runtime, "immersion_snapshot", None)
    return snapshot.value if snapshot is not None else None


def immersion_at(runtime: Any, read_at: datetime | None) -> float | None:
    """The immersion heater's counter taken at ``read_at``, for a sample of that moment."""
    snapshot = getattr(runtime, "immersion_snapshot", None)
    return snapshot.value_at(read_at) if snapshot is not None else None


def _modbus_number(runtime: Any, key: str) -> float | None:
    modbus = getattr(runtime, "modbus", None)
    value = (getattr(modbus, "data", None) or {}).get(key)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def share_reason(
    result: ShareResult,
    compressor: float | None,
    immersion: float | None,
    *,
    compressor_hours: float | None = None,
    consumption_answered: bool = True,
    immersion_answered: bool = True,
    display: str | None = None,
) -> str | None:
    """Why the share for ``result`` shows nothing, the nearest cause first, as :func:`reason_for`.

    A display that has gone quiet first, since the compressor's counter is
    read with it; then a counter that has not been read, by register where a
    register never answered; then a compressor counter that a running
    compressor never moves, which empties any share; and last the tracker's
    own account of its samples and the deltas.
    """
    if display is not None:
        return display
    if compressor is None:
        if not consumption_answered:
            return "registret 62341 har inte svarat, så tillförd energi saknas"
        return "räknarna har inte lästs"
    if immersion is None:
        return SHARE_IMMERSION_UNREAD if immersion_answered else SHARE_IMMERSION_SILENT
    fault = compressor_counter_fault(compressor, compressor_hours)
    if fault is not None:
        return fault
    if result.value is not None:
        return None
    return result.reason or "räknarna har inte lästs"


def share_for(runtime: Any, span: str) -> tuple[ShareResult, str | None]:
    """The share over ``span`` for a heat pump, and the reason where it has no figure.

    The compressor's counter as the coefficient of performance reads it, the
    display's own or Modbus 62341 taken with the display, and the immersion
    heater's taken at the same moment. Where a reason stands the figure is
    empty, whatever the deltas gave: a quiet display or a compressor counter
    that does not count leaves nothing to set the immersion heater against.
    """
    tracker = runtime.cop
    _out, compressor = current_totals(runtime)
    immersion = immersion_now(runtime)
    if span == "day":
        result = tracker.share_day(compressor, immersion)
    elif span in WINDOWS:
        result = tracker.share_window(compressor, immersion, span)
    elif span == "year":
        result = tracker.share_year(compressor, immersion)
    else:
        result = tracker.share_lifetime(compressor, immersion, hours=compressor_hours(runtime))
    modbus = getattr(runtime, "modbus", None)
    answered = getattr(modbus, "answered", None)
    reason = share_reason(
        result,
        compressor,
        immersion,
        compressor_hours=_modbus_number(runtime, "compressor_hours"),
        consumption_answered=getattr(runtime, "energy_in", None) is not None
        or modbus_consumption_answered(answered),
        immersion_answered=modbus_immersion_answered(answered),
        display=display_silence(getattr(runtime, "web", None), *counter_rows(runtime)),
    )
    if reason is not None and result.value is not None:
        result = ShareResult(
            None, result.basis, result.days, result.immersion, result.compressor, reason
        )
    return result, reason


def sample_summary(runtime: Any, recorded: bool) -> dict[str, Any]:
    """What the cop_sample action answers for one heat pump (roadmap R76).

    The two lifetime counters as they stood when the panel was last read,
    where the consumed energy came from, the moment they were read, the
    moment of the newest sample the tracker keeps, and whether this call kept
    a new one. A pair that has not been read again since the last sample is
    the same reading coming round and is not kept twice, which is why
    ``recorded`` can be false with both counters there; ``read_at`` is empty
    where a counter row has not been read or has gone stale, which gives no
    sample at all. Plain JSON under English keys, the names the sensors'
    English twins use, since what reads it is an automation.
    """
    out, consumed = current_totals(runtime)
    read_at = counters_read_at(runtime)
    tracker = getattr(runtime, "cop", None)
    newest = tracker.last_sample_at if tracker is not None else None
    display = getattr(runtime, "energy_in", None) is not None
    return {
        "recorded": bool(recorded),
        "heat_out_kwh": out,
        "energy_in_kwh": consumed,
        "energy_in_from": "display" if display else "modbus",
        "read_at": read_at.isoformat(timespec="seconds") if read_at is not None else None,
        "last_sample_at": newest.isoformat(timespec="seconds") if newest is not None else None,
    }


#: The coefficient of performance figures the report carries, by the name the
#: report builder takes them under. Returned as a mapping rather than a tuple:
#: adding a figure to a tuple silently breaks every caller that unpacks it, and
#: that once emptied a whole day's reports without a single error in sight.
COP_REPORT_KEYS = ("cop_day", "cop_year", "cop_first_year", "cop_lifetime")


def cop_for_report(runtime: Any) -> dict[str, float | None]:
    """Each figure only when it stands on its own span.

    Sending the lifetime figure under a yearly name would be a different number
    wearing the wrong label, so a figure without its span is left out.
    """
    figures: dict[str, float | None] = dict.fromkeys(COP_REPORT_KEYS)
    tracker = getattr(runtime, "cop", None)
    if tracker is None:
        return figures
    out, consumed = current_totals(runtime)
    figures["cop_year"] = tracker.result_year(out, consumed).value
    figures["cop_day"] = tracker.result_day(out, consumed).value
    figures["cop_first_year"] = tracker.result_first_year().value
    if out is not None and consumed is not None and consumed >= MIN_CONSUMPTION_KWH:
        figures["cop_lifetime"] = round(out / consumed, 2)
    return figures
