"""Energy of the integration's own making, where the machine's counters stay at zero.

Four installations in the fleet answer a clean zero on both lifetime counters.
The rows are there on the history page and they hand over numbers; nothing ever
fills them, and no coefficient of performance can be worked out from two zeroes.
**It follows the outdoor unit, not the indoor one:** the one installation that
fills them has a 700 series outdoor unit, and the ones that do not are a 400
series and two in the 600 series, behind two different indoor models. Four data
points, so it is where the evidence points, not something CTC documents.

The heat pump's own page carries the two powers instead, delivered heat and
supplied power in kilowatts, and energy is what power does over time, so the
integration adds them up itself. Whether those rows carry anything on a 400
or 600 series is the open question, and the report answers it within a day:
``power_rows`` says the pair was found, ``cop_integrated`` says the sums came to
something.

What makes the quotient worth having is that both powers are read in the same
harvest, at the same instant. A compressor that cycles makes a single sample too
high or too low, but it makes BOTH too high or too low by nearly the same factor,
so the error largely cancels in a ratio while it does not cancel in either total.
The totals are therefore rough on purpose and say so where they are shown; the
figure worked out from them is the point.

Nothing is invented across a gap: a sample that arrives after too long a silence
starts the sum again from itself rather than filling in hours nobody measured.

The coefficient of performance estimate (cop_estimate.py) takes its two powers
from the same page, the delivered heat and the inverter's electricity, and needs
the same sums with one more thing: today's. :class:`EstimateIntegrator` keeps
both, under a store of its own, so the sums above never change shape.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta, tzinfo
import logging
from typing import Any, NamedTuple

_LOGGER = logging.getLogger(__name__)

STORAGE_VERSION = 1

#: Longer than this between two harvests and what the pump did in between is
#: unknown. The display is harvested every half hour by default, and a cycle that
#: is skipped keeps its previous values, so two hours leaves room for a few missed
#: turns without ever stretching one sample across a night.
MAX_GAP = timedelta(hours=2)

#: The sums are written down with a delay, so a harvest every half hour does not
#: mean a disk write every half hour.
SAVE_DELAY = 600


class Sums(NamedTuple):
    """Two sums of kilowatt hours, and the hours of measured time they rest on."""

    heat: float
    electric: float
    hours: float


def _parse(stamp: str) -> datetime:
    value = datetime.fromisoformat(stamp)
    return value if value.tzinfo else value.replace(tzinfo=UTC)


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _step(
    previous: tuple[datetime, float, float], when: datetime, out: float, consumed: float
) -> tuple[float, float, float] | None:
    """The energy between two readings as (hours, out, in), or None where nothing may be added.

    The one set of rules every sum the integration makes is held to: the
    trapezoid between the two readings, and nothing across a gap longer than
    :data:`MAX_GAP`, nor for the same harvest twice or a clock that went back.
    """
    hours = (when - previous[0]).total_seconds() / 3600
    if hours <= 0 or hours > MAX_GAP.total_seconds() / 3600:
        # The same harvest twice, a clock that went backwards, or a silence
        # too long to account for. Begin again from this sample.
        if hours > 0:
            _LOGGER.debug("%.1f h since the last power reading, too long to add", hours)
        return None
    return hours, (previous[1] + out) / 2 * hours, (previous[2] + consumed) / 2 * hours


class PowerIntegrator:
    """Two running sums of kilowatt hours, from two kilowatt readings over time."""

    def __init__(self, store: Any) -> None:
        self._store = store
        self._out = 0.0
        self._in = 0.0
        #: How much time the sums actually rest on, gaps excluded.
        self._hours = 0.0
        self._last: tuple[datetime, float, float] | None = None
        self._loaded = False

    async def async_load(self) -> None:
        if self._loaded:
            return
        self._loaded = True
        data = await self._store.async_load()
        if not isinstance(data, dict):
            return
        self._out = _number(data.get("out")) or 0.0
        self._in = _number(data.get("in")) or 0.0
        self._hours = _number(data.get("hours")) or 0.0
        last = data.get("last")
        if isinstance(last, (list, tuple)) and len(last) == 3:
            try:
                self._last = (_parse(str(last[0])), float(last[1]), float(last[2]))
            except (TypeError, ValueError):
                self._last = None

    @property
    def totals(self) -> tuple[float | None, float | None]:
        """The two sums, or nothing at all until they rest on measured time."""
        if self._hours <= 0:
            return None, None
        return round(self._out, 1), round(self._in, 1)

    @property
    def hours(self) -> float:
        return round(self._hours, 1)

    def note(self, when: Any, out_kw: Any, in_kw: Any) -> bool:
        """Take one pair of readings. True when it added to the sums.

        The pair has to come from the same read, which is what the caller passes:
        both rows sit on the same page, so one timestamp covers them.
        """
        out = _number(out_kw)
        consumed = _number(in_kw)
        if not isinstance(when, datetime) or out is None or consumed is None:
            return False
        previous = self._last
        self._last = (when, out, consumed)
        if previous is None:
            return False
        step = _step(previous, when, out, consumed)
        if step is None:
            return False
        hours, out_kwh, in_kwh = step
        self._out += out_kwh
        self._in += in_kwh
        self._hours += hours
        return True

    def as_dict(self) -> dict[str, Any]:
        last = self._last
        return {
            "out": round(self._out, 3),
            "in": round(self._in, 3),
            "hours": round(self._hours, 4),
            "last": [last[0].isoformat(), last[1], last[2]] if last else None,
        }

    def save_soon(self) -> None:
        """Write the sums down, batched: the store delays and coalesces."""
        delay = getattr(self._store, "async_delay_save", None)
        if delay is not None:
            delay(self.as_dict, SAVE_DELAY)

    async def async_save(self) -> None:
        await self._store.async_save(self.as_dict())


class EstimateIntegrator:
    """The lifetime sums of the estimate, and the sums of today as well.

    The same two powers and the same rules as :class:`PowerIntegrator`, which is left as it is.
    It keeps a store of its own, holding what that one holds and the day beside it. The day is
    the addition: each interval counts to the local day its later reading falls on, so an
    interval that crosses midnight counts wholly to the new day, which is at most one harvest's
    worth of energy and is said here rather than apportioned. A reading that begins a new local
    day starts today's sums from nothing.

    ``zone`` is the time zone the days are kept in; None means the system's.
    """

    def __init__(self, store: Any, zone: tzinfo | None = None) -> None:
        self._store = store
        self._zone = zone
        self._out = 0.0
        self._in = 0.0
        self._hours = 0.0
        self._last: tuple[datetime, float, float] | None = None
        self._day: date | None = None
        self._day_out = 0.0
        self._day_in = 0.0
        self._day_hours = 0.0
        self._loaded = False

    async def async_load(self) -> None:
        if self._loaded:
            return
        self._loaded = True
        data = await self._store.async_load()
        if not isinstance(data, dict):
            return
        self._out = _number(data.get("out")) or 0.0
        self._in = _number(data.get("in")) or 0.0
        self._hours = _number(data.get("hours")) or 0.0
        last = data.get("last")
        if isinstance(last, (list, tuple)) and len(last) == 3:
            try:
                self._last = (_parse(str(last[0])), float(last[1]), float(last[2]))
            except (TypeError, ValueError):
                self._last = None
        today = data.get("day")
        if isinstance(today, dict):
            try:
                self._day = date.fromisoformat(str(today.get("date")))
            except ValueError:
                self._day = None
            if self._day is not None:
                self._day_out = _number(today.get("out")) or 0.0
                self._day_in = _number(today.get("in")) or 0.0
                self._day_hours = _number(today.get("hours")) or 0.0

    def local_day(self, when: datetime) -> date:
        """The calendar day a moment falls on, in the zone the days are kept in."""
        return when.astimezone(self._zone).date()

    def lifetime(self) -> Sums:
        """Every interval the sums have counted, since the integration began to keep them."""
        return Sums(self._out, self._in, self._hours)

    def today(self, day: date) -> Sums:
        """The sums of ``day``, which are empty for a day no reading has begun yet."""
        if self._day != day:
            return Sums(0.0, 0.0, 0.0)
        return Sums(self._day_out, self._day_in, self._day_hours)

    def note(self, when: Any, heat_kw: Any, electric_kw: Any) -> bool:
        """Take one pair of readings. True when it added to the sums.

        The pair has to come from one harvest, which is what the caller checks
        before passing it. A reading that is not a number, or a moment that is
        not one, is no reading and leaves every sum as it was.
        """
        heat = _number(heat_kw)
        electric = _number(electric_kw)
        if not isinstance(when, datetime) or heat is None or electric is None:
            return False
        previous = self._last
        self._last = (when, heat, electric)
        day = self.local_day(when)
        if self._day is None or day > self._day:
            self._day = day
            self._day_out = 0.0
            self._day_in = 0.0
            self._day_hours = 0.0
        if previous is None:
            return False
        step = _step(previous, when, heat, electric)
        if step is None:
            return False
        hours, out_kwh, in_kwh = step
        self._out += out_kwh
        self._in += in_kwh
        self._hours += hours
        if day == self._day:
            self._day_out += out_kwh
            self._day_in += in_kwh
            self._day_hours += hours
        return True

    def as_dict(self) -> dict[str, Any]:
        last = self._last
        today = self._day
        return {
            "out": round(self._out, 3),
            "in": round(self._in, 3),
            "hours": round(self._hours, 4),
            "last": [last[0].isoformat(), last[1], last[2]] if last else None,
            "day": {
                "date": today.isoformat(),
                "out": round(self._day_out, 3),
                "in": round(self._day_in, 3),
                "hours": round(self._day_hours, 4),
            }
            if today is not None
            else None,
        }

    def save_soon(self) -> None:
        """Write the sums down, batched: the store delays and coalesces."""
        delay = getattr(self._store, "async_delay_save", None)
        if delay is not None:
            delay(self.as_dict, SAVE_DELAY)

    async def async_save(self) -> None:
        await self._store.async_save(self.as_dict())
