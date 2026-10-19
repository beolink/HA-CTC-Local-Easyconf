"""The voluntary debug report: every value, for the developer, while the owner wants it.

Some questions cannot be answered from the anonymous statistics, by design, nor
from one diagnostics download taken by hand: whether a coefficient of
performance can be worked out at all on a model whose lifetime counters never
move turns on what its display shows while the compressor runs, hour after
hour. So the owner can switch this on: the switch Report all values to the
developer (Rapportera alla värden till utvecklaren) on the CTC page, or the
option of that name under Configure, which are the one setting. Off unless
switched on, and once on it stays on until it is switched off.

While it is on, a report goes to stats.rnet.se a few seconds after it is
switched on (SWITCHED_ON_DELAY), a short while after each set-up (START_DELAY,
so that the set-up and the first harvest are in it), and every half hour after
that; nothing is collected or sent while it is off. It goes through Home
Assistant's shared session, which writes the body, every number JSON cannot
carry as null, and it is weighed as that session writes it, so it stays under
the server's megabyte. A report is exactly what Download diagnostics gives,
built by diagnostics.py itself (diagnostics_data.build, Home Assistant's
redaction and the wash), and beside it the integration's own warnings and
errors since the last report the server stored: a ring of the newest MAX_LINES
on the integration's logger namespace, washed the same way when they are sent,
with the secrets of every heat pump of the integration, since a line can name
any of them. An error's traceback is not kept, only its type and message: the
lines of a traceback name the files of the installation. The installation is
named by the random id the anonymous statistics keep for this integration,
read from their own store, and created there the way stats.py creates it where
it has none yet, so that it picks the id up. stats.py is the same file in seven
repositories and is never changed for this.

Switching it on or off writes the entry's options and nothing else. The
listener that reloads the entry for a change of its options leaves a change of
this one alone (without_the_debug_report), and the options form writes it on
its own where it is all that changed (only_the_debug_report_changes): a reload
closes Modbus, and a set-up that meets the controller's one place still taken
waits for it, so a reload can cost the pump's Modbus for minutes. The reporter
hears the write itself, as one of the entry's update listeners.

It is armed like the anonymous statistics, before the first Modbus call, and
outlives a set-up attempt that does not finish: an entry waiting for its
controller is when a report says most. It is stopped from async_unload_entry
and async_remove_entry, which a failed attempt never reaches, and by itself
once its entry is gone or disabled. The lines not sent yet are handed to the
next set-up of the entry, so a reload loses none.

It must never disturb the integration: every failure is swallowed and said on
debug, and its own lines are never above info, so it never reports itself.

The parts that decide what is sent and when are free of Home Assistant, and
tests/test_debug_report.py works them on stand-ins; the calls into Home
Assistant are the small functions under "Home Assistant", each importing what
it calls where it is called, like stats.py's use in config_flow.py.
"""

from __future__ import annotations

import asyncio
from collections import deque
from collections.abc import Callable, Iterable, Mapping
import contextlib
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
import itertools
import json
import logging
import math
import re
from typing import Any
import uuid

from .const import CONF_DEBUG_REPORT, DOMAIN
from .diagnostics_data import Secrets, secrets_for

_LOGGER = logging.getLogger(__name__)

ENDPOINT = "https://stats.rnet.se/api/v1/diagnostics"
#: The version of the wire contract, which the server checks.
SCHEMA_VERSION = 1

#: How often a report goes while the report is on.
INTERVAL = timedelta(minutes=30)
#: How long the first report after a set-up waits. The reporter is armed
#: before the first Modbus call, and a report sent then would show an entry
#: halfway up; a moment later it shows the set-up done and the first harvest
#: made, or why not.
START_DELAY = timedelta(minutes=2)
#: How long the report for switching it on waits. A write of the options that
#: switches it on and changes something else as well reloads the entry, and
#: the set-up that follows reports by itself: in this while the reload stops
#: the reporter and cancels the report, so one goes rather than two.
SWITCHED_ON_DELAY = timedelta(seconds=5)
#: Seconds a report may take on the wire.
TIMEOUT = 30

#: The most a report may weigh on the wire, as the server takes it.
MAX_BYTES = 1_000_000
#: How many log lines a report carries at most, and the ring keeps.
MAX_LINES = 200
#: How long one line's message may be, after the wash.
MAX_MESSAGE = 2000
#: How much of a message the ring keeps before the wash, which can make it
#: shorter: a name or an address becomes the placeholder.
MAX_KEPT = 4 * MAX_MESSAGE

#: The server's answer for a report it stored. Only then are the lines that
#: went with it let go; any other answer leaves them for the next report.
STORED = 204
#: The server's answer for a report sooner than ten minutes after the last one
#: it took from the installation, with a Retry-After. The Retry-After is not
#: needed: the next report goes at the next turn, half an hour on, past the
#: ten minutes the server counts.
TOO_SOON = 429

#: The levels the server takes, which are the ring's: a level of its own
#: between two of them is carried under the one below.
LEVELS = ((logging.CRITICAL, "CRITICAL"), (logging.ERROR, "ERROR"), (logging.WARNING, "WARNING"))

#: The anonymous statistics' own store, which holds the installation's random
#: id: the version and the key stats.py opens it with.
STATS_STORE_VERSION = 1
STATS_STORE_KEY = f"{DOMAIN}.stats"

#: The logger every module of the integration writes through.
LOGGER_NAMESPACE = f"custom_components.{DOMAIN}"

#: Where the armed reporters live, by entry id, and the lines a stopped one
#: had not sent yet, kept for the next set-up of its entry.
DATA_KEY = f"{DOMAIN}_debug_reports"
LINES_KEY = f"{DOMAIN}_debug_report_lines"

#: The characters a name or an address the wash takes out is made of: a host
#: name, an IPv4 or IPv6 address, a MAC address, a device key ("192.0.2.55#2").
#: A message is never cut between two of them, so the wash meets them whole.
_TOKEN = re.compile(r"[\w.:#-]")


# ---------------------------------------------------------------- the rules


def debug_report_on(options: Mapping[str, Any]) -> bool:
    """Whether the owner has switched the debug report on. Off where the options say nothing."""
    return bool(options.get(CONF_DEBUG_REPORT, False))


def without_the_debug_report(options: Mapping[str, Any]) -> dict[str, Any]:
    """The options but the debug report, which is applied in place and never takes a reload."""
    return {key: value for key, value in options.items() if key != CONF_DEBUG_REPORT}


_ABSENT = object()


def only_the_debug_report_changes(
    stored: Mapping[str, Any], written: Mapping[str, Any], defaults: Mapping[str, Any]
) -> bool:
    """Whether writing ``written`` over ``stored`` changes nothing in effect but the debug report.

    A setting the options do not hold is what ``defaults`` says it is: the
    first form of Configure writes every field it has, so an entry that was
    never saved there gets the settings it lacked written with the values it
    already had, and that is no change. A setting without a default that
    comes or goes is one.
    """
    for key in {*stored, *written} - {CONF_DEBUG_REPORT}:
        before = stored.get(key, defaults.get(key, _ABSENT))
        after = written.get(key, defaults.get(key, _ABSENT))
        if before != after:
            return False
    return True


# ---------------------------------------------------------------- the log


@dataclass(frozen=True)
class LogLine:
    """One warning or error of the integration's, as it was logged, not washed yet."""

    seq: int
    at: datetime
    level: str
    logger: str
    message: str


def level_name(levelno: int) -> str:
    """The level as the server takes it: WARNING, ERROR or CRITICAL."""
    return next((name for floor, name in LEVELS if levelno >= floor), "WARNING")


def _cut(text: str, limit: int) -> str:
    """``text`` cut to at most ``limit`` characters, never inside a name or an address.

    A host name cut in two is no longer the host name the wash looks for, so a
    cut that would fall inside one falls before it. A message cut short ends
    in three dots.
    """
    if len(text) <= limit:
        return text
    end = limit - 3
    while end > 0 and _TOKEN.match(text[end - 1]) and _TOKEN.match(text[end]):
        end -= 1
    return text[:end] + "..."


class LogRing(logging.Handler):
    """The integration's own warnings and errors, the newest MAX_LINES, oldest first.

    Attached to the integration's logger namespace while the report is on,
    and nowhere else. The lines are kept as they were logged and washed when
    a report is built, with the secrets known by then: a serial number read
    after a warning was logged is washed out of it all the same.
    """

    def __init__(self, lines: Iterable[LogLine] = ()) -> None:
        super().__init__(level=logging.WARNING)
        self._seq = itertools.count(1)
        self._lines: deque[LogLine] = deque(maxlen=MAX_LINES)
        for line in lines:
            self._keep(line.at, line.level, line.logger, line.message)

    def emit(self, record: logging.LogRecord) -> None:
        # A line of the integration's must never fail because it is kept here.
        with contextlib.suppress(Exception):
            message = record.getMessage()
            error = record.exc_info[1] if record.exc_info else None
            if error is not None:
                message = f"{message}\n{type(error).__name__}: {error}"
            self._keep(
                datetime.fromtimestamp(record.created, UTC),
                level_name(record.levelno),
                record.name,
                message,
            )

    def _keep(self, at: datetime, level: str, logger: str, message: str) -> None:
        self._lines.append(
            LogLine(next(self._seq), at, level, logger, _cut(message, MAX_KEPT))
        )

    def lines(self) -> list[LogLine]:
        """The lines kept, oldest first."""
        with self.lock:
            return list(self._lines)

    def drop_through(self, seq: int) -> None:
        """Let go of the lines up to and including ``seq``: the server has them."""
        with self.lock:
            while self._lines and self._lines[0].seq <= seq:
                self._lines.popleft()


class Washer:
    """The wash with the secrets of every heat pump at once.

    One logger namespace serves every entry of the integration, so a line in
    one entry's report can name another heat pump. Without any secrets it is
    the wash of what no installation names: IPv4 and MAC addresses, serial
    numbers and the display's host name.
    """

    def __init__(self, secrets: Iterable[Any]) -> None:
        self._all = list(secrets) or [Secrets()]

    def wash_text(self, text: str) -> str:
        for secrets in self._all:
            text = secrets.wash_text(text)
        return text


def log_lines(lines: Iterable[LogLine], washer: Any) -> list[dict[str, str]]:
    """The lines as the wire carries them: the newest MAX_LINES, oldest first, washed and cut."""
    return [
        {
            "at": line.at.isoformat(timespec="seconds"),
            "level": line.level,
            "logger": _cut(washer.wash_text(line.logger), MAX_MESSAGE),
            "message": _cut(washer.wash_text(line.message), MAX_MESSAGE),
        }
        for line in list(lines)[-MAX_LINES:]
    ]


# ---------------------------------------------------------------- the report


def finite(value: Any) -> Any:
    """``value`` with every number that is not finite, NaN and the infinities, as None.

    JSON has no NaN. Home Assistant's session writes one as null, Python's own
    json module as the bare token NaN, which the server refuses as no JSON at
    all; a value the panel or a sum could give as NaN is null either way.
    """
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, Mapping):
        return {key: finite(item) for key, item in value.items()}
    if isinstance(value, list | tuple):
        return [finite(item) for item in value]
    return value


def build_payload(
    *,
    install_id: str,
    version: str,
    ha_version: str,
    diagnostics: Mapping[str, Any],
    log: list[dict[str, str]],
) -> dict[str, Any]:
    """One report, as the wire contract has it, with no number JSON cannot carry."""
    return {
        "schema": SCHEMA_VERSION,
        "install_id": install_id,
        "integration": DOMAIN,
        "version": version,
        "ha_version": ha_version,
        "diagnostics": finite(dict(diagnostics)),
        "log": list(log),
    }


def encode(value: Any) -> str:
    """``value`` as compact JSON, the way Home Assistant's session writes it.

    Where Home Assistant is there, its own json_dumps weighs the report
    (_json_dumps); this is the same shape without it: no spaces, and the
    letters as they are rather than escaped.
    """
    return json.dumps(
        value, ensure_ascii=False, separators=(",", ":"), allow_nan=False, default=str
    )


def fitted(
    payload: Mapping[str, Any], dumps: Callable[[Any], str] = encode
) -> tuple[dict[str, Any], int, int] | None:
    """The report within MAX_BYTES as ``dumps`` writes it: the report, its size, its log lines.

    The oldest lines give way first. A report whose diagnostics alone weigh
    more than the limit is not sent at all: None. ``dumps`` is what the
    session will write the report with, so the size is the size on the wire.
    """

    def weight(value: Any) -> int:
        return len(dumps(value).encode("utf-8"))

    log = list(payload.get("log") or [])
    base = weight({**payload, "log": []})
    if base > MAX_BYTES:
        return None
    room = MAX_BYTES - base
    kept = 0
    for line in reversed(log):
        size = weight(line) + (1 if kept else 0)
        if size > room:
            break
        room -= size
        kept += 1
    report = {**payload, "log": log[len(log) - kept :]}
    size = weight(report)
    while size > MAX_BYTES and kept:
        # Never taken where dumps writes a list as its items and commas.
        kept -= 1
        report = {**payload, "log": log[len(log) - kept :]}
        size = weight(report)
    return report, size, kept


async def async_install_id(store: Any) -> str:
    """The installation's random id from the anonymous statistics' store.

    Where the store has none yet, because the statistics are switched off or
    have not reported yet, one is made the way stats.py makes it and saved as
    stats.py saves it, so the statistics take the same id when they come.
    """
    data = await store.async_load()
    found = data.get("install_id") if isinstance(data, Mapping) else None
    if found:
        return str(found)
    install_id = str(uuid.uuid4())
    await store.async_save({"install_id": install_id})
    return install_id


# ---------------------------------------------------------------- Home Assistant


def _stats_store(hass: Any) -> Any:
    """The anonymous statistics' store, opened as stats.py opens it."""
    from homeassistant.helpers.storage import Store

    return Store(hass, STATS_STORE_VERSION, STATS_STORE_KEY)


def _session(hass: Any) -> Any:
    """Home Assistant's shared aiohttp session."""
    from homeassistant.helpers.aiohttp_client import async_get_clientsession

    return async_get_clientsession(hass)


def _every(hass: Any, action: Callable[..., Any], interval: timedelta) -> Callable[[], None]:
    """Run ``action`` every ``interval``; returns what cancels it."""
    from homeassistant.helpers.event import async_track_time_interval

    return async_track_time_interval(hass, action, interval)


def _json_dumps() -> Callable[[Any], str]:
    """What Home Assistant's shared session writes a body with, to weigh one by."""
    from homeassistant.helpers.json import json_dumps

    return json_dumps


def _ha_version() -> str:
    from homeassistant.const import __version__

    return __version__


async def _async_diagnostics(hass: Any, entry: Any) -> dict[str, Any]:
    """Exactly what Download diagnostics gives for the entry.

    Imported here: diagnostics.py reads the package's books of this run, and
    the package imports this module.
    """
    from .diagnostics import async_get_config_entry_diagnostics

    return await async_get_config_entry_diagnostics(hass, entry)


# ---------------------------------------------------------------- the reporter


class DebugReporter:
    """Sends the debug report for one config entry while its owner has it on.

    Hears every write of the entry's options and starts or stops as the
    option says, without the entry being reloaded. Every failure is swallowed
    and said on debug.
    """

    def __init__(
        self, hass: Any, entry: Any, version: str, lines: Iterable[LogLine] = ()
    ) -> None:
        self.hass = hass
        self.entry = entry
        self.version = version
        #: Lines an earlier set-up of the entry had not sent yet.
        self._carried: list[LogLine] = list(lines)
        self._ring: LogRing | None = None
        self._cancel_timer: Callable[[], None] | None = None
        self._task: asyncio.Task[None] | None = None
        self._install_id: str | None = None
        self._closed = False
        self._told = False
        self._unlisten: Callable[[], None] | None = entry.add_update_listener(
            self._async_options_written
        )

    @property
    def running(self) -> bool:
        """Whether the report is on and going: the ring kept, the timer armed."""
        return self._ring is not None

    async def _async_options_written(self, _hass: Any, _entry: Any) -> None:
        """The entry's options were written: the report switched on or off, or anything else."""
        self.async_apply(at_once=True)

    def async_apply(self, at_once: bool = False) -> None:
        """Start or stop as the option says.

        ``at_once`` for the owner switching it on just now, whose first report
        goes within SWITCHED_ON_DELAY; at a set-up it waits START_DELAY.
        Switched off, the lines not sent are let go: nothing is kept while it
        is off.
        """
        if self._closed:
            return
        if debug_report_on(self.entry.options):
            if not self.running:
                delay = SWITCHED_ON_DELAY if at_once else START_DELAY
                self._start(delay.total_seconds())
        elif self.running:
            self._halt()
            self._carried.clear()

    def _start(self, delay: float) -> None:
        ring = LogRing(self._carried)
        self._carried = []
        logging.getLogger(LOGGER_NAMESPACE).addHandler(ring)
        self._ring = ring
        self._cancel_timer = _every(self.hass, self._async_tick, INTERVAL)
        if not self._told:
            self._told = True
            _LOGGER.info(
                "The debug report is on: what Download diagnostics holds and the "
                "integration's own warnings and errors go to %s every %d minutes. Switch "
                "it off on the CTC page or under Settings, Devices and services, CTC Local "
                "Easyconf, Configure",
                ENDPOINT,
                int(INTERVAL.total_seconds() // 60),
            )
        self._task = self.hass.async_create_background_task(
            self._async_first(delay), f"{DOMAIN} debug report"
        )

    async def _async_first(self, delay: float) -> None:
        if delay:
            await asyncio.sleep(delay)
        await self.async_report()

    async def _async_tick(self, _now: Any = None) -> None:
        await self.async_report()

    def _halt(self) -> list[LogLine]:
        """Stop the timer, the report on its way and the ring; returns the lines not sent."""
        if self._cancel_timer is not None:
            self._cancel_timer()
            self._cancel_timer = None
        task, self._task = self._task, None
        if task is not None and not task.done() and task is not _current_task():
            task.cancel()
        ring, self._ring = self._ring, None
        if ring is None:
            return []
        logging.getLogger(LOGGER_NAMESPACE).removeHandler(ring)
        return ring.lines()

    def close(self) -> list[LogLine]:
        """Stop for good. Safe to call more than once; returns the lines not sent."""
        self._closed = True
        if self._unlisten is not None:
            with contextlib.suppress(ValueError):
                self._unlisten()
            self._unlisten = None
        lines = [*self._carried, *self._halt()]
        self._carried = []
        return lines

    def _entry_gone(self) -> bool:
        """True once the entry has been removed or disabled, as stats.py tells it.

        Home Assistant reaches neither async_unload_entry nor async_remove_entry
        for an entry disabled while it waits for another set-up attempt.
        """
        try:
            current = self.hass.config_entries.async_get_entry(self.entry.entry_id)
            if current is None:
                return True
            return isinstance(getattr(current, "disabled_by", None), str)
        except Exception:  # the report must never disturb anything
            return False

    def _retire(self) -> None:
        """Stop for good and leave hass.data, unless another reporter has taken this one's place."""
        reporters = self.hass.data.get(DATA_KEY)
        if isinstance(reporters, dict) and reporters.get(self.entry.entry_id) is self:
            reporters.pop(self.entry.entry_id, None)
        self.close()

    def _washer(self) -> Washer:
        """The wash for the log: this heat pump's secrets first, then every other one's."""
        entries = [self.entry]
        with contextlib.suppress(Exception):
            entries += [
                other
                for other in self.hass.config_entries.async_entries(DOMAIN)
                if other.entry_id != self.entry.entry_id
            ]
        return Washer(
            secrets_for(item.data, item.options, getattr(item, "runtime_data", None))
            for item in entries
        )

    async def _async_install_id(self) -> str:
        if not self._install_id:
            self._install_id = await async_install_id(_stats_store(self.hass))
        return self._install_id

    async def async_payload(self, lines: Iterable[LogLine]) -> dict[str, Any]:
        """One report: the download, as diagnostics.py gives it, and the washed log."""
        return build_payload(
            install_id=await self._async_install_id(),
            version=self.version,
            ha_version=_ha_version(),
            diagnostics=await _async_diagnostics(self.hass, self.entry),
            log=log_lines(lines, self._washer()),
        )

    async def async_report(self) -> None:
        """Send one report. Goes quiet at the slightest problem and never raises."""
        ring = self._ring
        if self._closed or ring is None:
            return
        if self._entry_gone():
            _LOGGER.debug("The %s entry is gone or disabled; the debug report stopped", DOMAIN)
            self._retire()
            return
        if not debug_report_on(self.entry.options):
            return
        try:
            lines = ring.lines()
            found = fitted(await self.async_payload(lines), _json_dumps())
            if found is None:
                _LOGGER.debug(
                    "The debug report weighs more than %d bytes without its log, so it is "
                    "not sent",
                    MAX_BYTES,
                )
                return
            report, size, kept = found
            # Home Assistant's shared session writes the body itself, with the
            # json_dumps that weighed it.
            async with asyncio.timeout(TIMEOUT):
                async with _session(self.hass).post(ENDPOINT, json=report) as resp:
                    status = resp.status
                    said = "" if status in (STORED, TOO_SOON) else (await resp.text())[:200]
            if status == STORED:
                if lines:
                    ring.drop_through(lines[-1].seq)
                _LOGGER.debug("The debug report was stored: %d bytes, %d log lines", size, kept)
            elif status == TOO_SOON:
                _LOGGER.debug(
                    "The debug report came sooner than ten minutes after the last one, "
                    "so it waits for its next turn"
                )
            else:
                _LOGGER.debug("The debug report was refused: %s %s", status, said)
        except Exception:  # the report must never break anything
            _LOGGER.debug("Could not send the debug report", exc_info=True)


def _current_task() -> asyncio.Task[Any] | None:
    try:
        return asyncio.current_task()
    except RuntimeError:  # no loop running
        return None


def async_setup_debug_report(hass: Any, entry: Any, version: str) -> DebugReporter:
    """Arm the entry's reporter, or point the one already armed at this set-up attempt.

    Called from async_setup_entry before the first Modbus call, beside the
    anonymous statistics, and for the same reason: Home Assistant runs an
    entry's on-unload callbacks when a set-up attempt fails and retries for
    as long as the controller stays away, and the report has to go on
    meanwhile. Stop it with async_stop_debug_report from async_unload_entry,
    never from an on-unload callback. Lines an earlier set-up of the entry
    had not sent are taken over.
    """
    reporters: dict[str, DebugReporter] = hass.data.setdefault(DATA_KEY, {})
    reporter = reporters.get(entry.entry_id)
    if reporter is None:
        carried = hass.data.setdefault(LINES_KEY, {}).pop(entry.entry_id, [])
        reporter = reporters[entry.entry_id] = DebugReporter(hass, entry, version, carried)
    else:
        reporter.version = version
    reporter.async_apply()
    return reporter


def async_stop_debug_report(hass: Any, entry: Any, *, keep_lines: bool = True) -> None:
    """Stop the entry's reporter, from async_unload_entry or async_remove_entry.

    The lines it had not sent are kept for the next set-up of the entry, a
    reload's, unless ``keep_lines`` is False: the entry is being removed.
    """
    reporter = (hass.data.get(DATA_KEY) or {}).pop(entry.entry_id, None)
    carried: dict[str, list[LogLine]] = hass.data.setdefault(LINES_KEY, {})
    lines = reporter.close() if reporter is not None else []
    if keep_lines and lines:
        carried[entry.entry_id] = lines
    elif not keep_lines:
        carried.pop(entry.entry_id, None)
