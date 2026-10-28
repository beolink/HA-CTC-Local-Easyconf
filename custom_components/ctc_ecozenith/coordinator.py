"""Update coordinators for the two transports.

The Modbus coordinator is the workhorse: it reads the documented registers in
contiguous blocks, it has no side effects and it runs on a short interval.

The web coordinator harvests the values Modbus does not expose. Reading a page
other than the one on the panel means navigating there, which moves the physical
display, so it runs rarely, it checks first whether somebody is using the panel,
and it puts the panel back when it is done.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
import logging
import time
from typing import Any

from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .alarms import AlarmWatch
from .catalogue import numeric_value
from .const import (
    CONTROL_EXPIRY_SECONDS,
    CONTROL_KEEPALIVE_SECONDS,
    DOMAIN,
    HARVEST_PATIENCE,
    HARVEST_SKIP_LIMIT,
    MODBUS_SENSORS,
    MODBUS_SETTINGS,
    RETRY_INTERVAL,
    ModbusSensor,
    SlowPage,
)
from .harvest import StoredHarvest, first_harvest_delay, is_fresh, stale_after, utcnow
from .keepalive import Keepalive
from .modbus_api import (
    PROBE_REGISTER,
    REQUEST_TIMEOUT,
    CtcModbusClient,
    CtcModbusError,
    CtcModbusTransportError,
    decode_reading,
    plan_blocks,
)
from .patience import Patience
from .poll import MissingBlocks, SlowRounds, lead_with, read_round
from .web_api import CtcWebClient, CtcWebError

_LOGGER = logging.getLogger(__name__)


def _entry_kwargs(config_entry: Any) -> dict[str, Any]:
    """The config entry for the base class, said out loud where there is one.

    Home Assistant otherwise takes it from a context variable that is set while
    the entry is being set up, and has said for a while that it wants it passed
    instead. Left out where there is none, as in the tests that build a
    coordinator on its own, so the base class keeps its own default there.
    """
    return {} if config_entry is None else {"config_entry": config_entry}


class CtcModbusCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Poll the documented Modbus registers."""

    def __init__(
        self,
        hass: HomeAssistant,
        client: CtcModbusClient,
        interval: int,
        config_entry: Any = None,
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN} modbus",
            update_interval=timedelta(seconds=interval),
            **_entry_kwargs(config_entry),
        )
        self.client = client
        self.descriptions: tuple[ModbusSensor, ...] = MODBUS_SENSORS + MODBUS_SETTINGS
        # The block with the register every model answers goes first, so a
        # controller that answers nothing is found out after one timeout.
        self._blocks = lead_with(plan_blocks(self.descriptions), PROBE_REGISTER)
        #: Blocks this model has turned out not to have. Learnt per run, so a
        #: restart gives every block a fresh chance.
        self._missing = MissingBlocks()
        self._slow = SlowRounds()
        #: The code behind every enum reading, by sensor key, kept beside the
        #: label in ``data``. The derived binary sensors judge these, since a
        #: label is a string that may be reworded or translated while the code
        #: is what the controller said. Only trusted for a key that is in data.
        self.codes: dict[str, int] = {}
        #: The code behind an enum reading the table has no label for, by sensor
        #: key, so the number is still visible in the sensor's attributes.
        self.unknown_codes: dict[str, int] = {}
        self._said_codes: set[tuple[str, int]] = set()
        # Cumulative count of register blocks that did not answer, or rounds
        # that lost the connection. Only used by the optional daily report,
        # which sends the delta since it last ran.
        self.read_failures = 0
        #: Every register address that has answered at least once since Home
        #: Assistant started, taken from the raw words of each round. This is
        #: where "the register answered" is judged. ``data`` is no good for
        #: it: a key is left out there both when the block was silent this
        #: round and when a 32 bit pair decoded to CTC's marker for a counter
        #: that is not fitted, and neither is the same as the model lacking
        #: the register. Never emptied, like the missing blocks: a block the
        #: model lacks never joins it, and a restart starts over.
        self.answered: set[int] = set()

    async def _async_update_data(self) -> dict[str, Any]:
        try:
            result = await read_round(
                self.client, self._blocks, self._missing.missing, probe=PROBE_REGISTER
            )
        except CtcModbusTransportError as err:
            self.read_failures += 1
            # Home Assistant logs the failure once, and the recovery, by itself.
            raise UpdateFailed(f"the heat pump was lost at register {err.address}: {err}") from err
        self.read_failures += len(result.unanswered) + len(result.dropped)

        for start in self._missing.note(result):
            count = next((n for s, n in self._blocks if s == start), 1)
            _LOGGER.info(
                "Registers %s to %s have not answered in %s rounds while the rest did, so "
                "this model is taken not to have them and they are left out until Home "
                "Assistant restarts",
                start,
                start + count - 1,
                self._missing.patience,
            )
        interval = self.update_interval.total_seconds() if self.update_interval else 0.0
        pace = self._slow.note(result.elapsed, interval)
        if pace == "slow":
            _LOGGER.warning(
                "A Modbus round took %.1f s against an interval of %.0f s, so readings "
                "are older than they look; a block that never answers costs %s s each "
                "round until it is learnt as missing",
                result.elapsed,
                interval,
                REQUEST_TIMEOUT,
            )
        elif pace == "recovered":
            _LOGGER.info("Modbus rounds are back within the interval (%.1f s)", result.elapsed)

        raw = result.raw
        self.answered.update(raw)
        if not raw:
            raise UpdateFailed("no Modbus register could be read")

        data: dict[str, Any] = {}
        for description in self.descriptions:
            value = self._decode(description, raw)
            if value is not None:
                data[description.key] = value
        return data

    def _decode(self, description: ModbusSensor, raw: dict[int, int]) -> Any:
        """The reading for one description, with the bookkeeping around it.

        The decoding itself is decode_reading in modbus_api, free of Home
        Assistant and tested on its own; this keeps the codes the table has no
        label for, so the sensor can carry the number as an attribute.
        """
        reading = decode_reading(description, raw)
        if reading.code is None:
            self.codes.pop(description.key, None)
        else:
            self.codes[description.key] = reading.code
        if description.enum is None or reading.value is None:
            return reading.value
        # A label of the integration's own making ("Okänd (12)") is not one of
        # the sensor's options, and Home Assistant answers a state that is not
        # with an exception on every write: the entity then stops updating at
        # all. VSH's i255 sat in system status 12 behind exactly that.
        if reading.unknown is None:
            self.unknown_codes.pop(description.key, None)
        else:
            self.unknown_codes[description.key] = reading.unknown
            if (description.key, reading.unknown) not in self._said_codes:
                self._said_codes.add((description.key, reading.unknown))
                _LOGGER.info(
                    "%s answered with code %s, which the table has no label for, so the "
                    "sensor reads %s and carries the code as an attribute",
                    description.key,
                    reading.unknown,
                    reading.value,
                )
        return reading.value


class CtcWebCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Harvest the display's own values for the pages the user selected.

    A harvest is a walk: to each selected page in turn, reading its screens,
    and back to where the panel stood. The data keeps a value across a page
    that could not be reached, so that a page out of reach for a moment does
    not empty its sensors, and ``read_at`` says for every key when it was last
    actually read off the panel. That age is what decides whether a sensor is
    available (:meth:`is_fresh`): a value carried for longer than a whole
    harvest may fail in a row (HARVEST_PATIENCE intervals) is called stale,
    page by page, while a harvest that reads no page at all is a failure of
    the whole display and is counted by Patience like any other.
    """

    def __init__(
        self,
        hass: HomeAssistant,
        client: CtcWebClient,
        pages: list[SlowPage],
        interval: int,
        restore_page: bool = True,
        stored: StoredHarvest | None = None,
        config_entry: Any = None,
        live_page: int | None = None,
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN} display",
            update_interval=timedelta(seconds=interval),
            **_entry_kwargs(config_entry),
        )
        self.client = client
        self.pages = pages
        #: The configured interval between harvests. The coordinator's own
        #: update_interval is shorter while the display is being retried, so
        #: the age a reading may reach is measured against this one.
        self.interval = interval
        self.restore_page = restore_page
        #: The live page: the harvested page the panel rests on between two
        #: walks. The walk reads it last and ends on it instead of putting the
        #: panel back on the home page, and async_read_live_page reads it off
        #: the panel without a press while the panel shows it. None, or a page
        #: that is not harvested, leaves the panel's rest where it was.
        self.live_page = live_page if any(p.page == live_page for p in pages) else None
        #: The live page's own account, beside the walk's: when it was last
        #: read without a press and how many times, when it was last tried,
        #: how many tries in a row read nothing and why the last one did not,
        #: the page the panel showed then, and what /vars/menu answered then,
        #: the panel's own state as far as it reports one. The walk's
        #: bookkeeping above is never touched by a live read.
        self.live_read_at: datetime | None = None
        self.live_reads = 0
        self.live_attempt: datetime | None = None
        self.live_skips = 0
        self.live_reason: str | None = None
        self.live_panel_page: int | None = None
        self.live_menu_vars: list[Any] = []
        #: Told when a live read read nothing. The coordinator's own listeners
        #: are not: the data has not changed, and the integrators behind them
        #: must not meet the same reading twice. The harvest sensor listens
        #: here, so its account of the live reads follows the tries.
        self._live_status_listeners: list[Callable[[], None]] = []
        #: The page the panel was showing before this integration first touched
        #: it in this run, learnt the first time a harvest finds the panel
        #: outside the harvested pages. Within the run it keeps one failed
        #: restore from making the wrong page the new normal; a restart learns
        #: it again.
        self.home_page: int | None = None
        self._expected_page: int | None = None
        #: The harvest's own bookkeeping, for the diagnostics to show: how many
        #: rounds in a row gave way because the panel was not where the last
        #: round left it, why the last round gave way (None when it did not),
        #: and when the last round started and the next one is due. The
        #: failures in a row are on ``patience``.
        self.skips_in_a_row = 0
        self.last_skip_reason: str | None = None
        #: Whether this round found the panel away from where the last one
        #: left it; the home page and the restore target both depend on it.
        self._moved = False
        #: Why the last attempt failed, in the words of its UpdateFailed, and
        #: None after an attempt that worked.
        self.last_failure: str | None = None
        #: When each value was last actually read off the panel. The data keeps
        #: a value through a skipped cycle, so this is the only way to tell a
        #: fresh reading from a carried one.
        self.read_at: dict[str, datetime] = {}
        #: When a harvest last read at least one page, and the pages it read
        #: and missed that time, by number.
        self.last_harvest: datetime | None = None
        self.pages_read: list[int] = []
        self.pages_missed: list[int] = []
        #: When the display was last attempted, and when it will be next: the
        #: coordinator's own schedule is private, so the moments are kept here
        #: for the diagnostic sensor to show.
        self.last_attempt: datetime | None = None
        self.next_attempt: datetime | None = None
        #: A display that is merely slow should not take every reading with it.
        self.patience = Patience(interval, RETRY_INTERVAL, HARVEST_PATIENCE)
        #: What each page said about an alarm, read off the same values as
        #: the rows; the alarm log on the runtime asks it after every round.
        self.alarms = AlarmWatch([page.page for page in pages])
        # What the last harvest before the restart left, as old as it is: the
        # sensors come up with it, judged by its age like any other reading,
        # and the first harvest is owed one interval after it rather than now.
        # The panel is not moved by a restart.
        if stored is not None:
            self.data = dict(stored.values)
            self.read_at = dict(stored.read_at)
            self.last_harvest = stored.harvested_at
        now = utcnow()
        wait = first_harvest_delay(stored, interval, now)
        self.update_interval = timedelta(seconds=wait)
        self.next_attempt = self._attempt_at(now + timedelta(seconds=wait))

    # ------------------------------------------------------------ the age

    @property
    def stale_after(self) -> timedelta:
        """How long a reading stays fresh after it was read."""
        return stale_after(self.interval, HARVEST_PATIENCE)

    def last_read(self, key: str) -> datetime | None:
        """When ``key`` was last read off the panel, or None if it never was.

        Public on purpose: the coefficient of performance sensors say in their
        reason since when the display has not answered, and the daily report
        pairs a Modbus reading with the moment the display was read.
        """
        return self.read_at.get(key)

    def is_fresh(self, key: str, now: datetime | None = None) -> bool:
        """Whether ``key`` holds a value read recently enough to be shown.

        Within as many intervals as a whole harvest may fail in a row, so a
        page that has stopped answering is treated like a display that has:
        its sensors go unavailable after the same patience, no sooner, and
        come back the moment the page is read again.
        """
        if key not in (self.data or {}):
            return False
        return is_fresh(self.read_at.get(key), now or utcnow(), self.stale_after)

    # -------------------------------------------------------- the harvest

    async def _async_update_data(self) -> dict[str, Any]:
        if not self.pages:
            return {}
        self.last_attempt = utcnow()
        async with self.client.panel:
            try:
                data = await self._async_harvest()
            except UpdateFailed as err:
                self.last_failure = str(err)
                shown = self.patience.failed(bool(self.data))
                self._reschedule()
                if shown:
                    raise
                _LOGGER.debug("Display harvest failed (%s), keeping what we have: %s",
                              self.patience.failures, err)
                return dict(self.data or {})
        self.last_failure = None
        self.patience.worked()
        self._reschedule()
        return data

    def _reschedule(self) -> None:
        """The pace from here: the interval, or the retry pace after a failure."""
        self.update_interval = timedelta(seconds=self.patience.seconds)
        self.next_attempt = self._attempt_at(utcnow() + self.update_interval)

    def _attempt_at(self, moment: datetime) -> datetime | None:
        """``moment``, or None where no attempt will be made then.

        Home Assistant runs no schedule for an entry whose polling is switched
        off, whatever the interval says, so the diagnostic sensor would
        otherwise name a next attempt that passes without one. A refresh asked
        for by hand still harvests; it is only the schedule that is off.
        """
        entry = getattr(self, "config_entry", None)
        if entry is not None and getattr(entry, "pref_disable_polling", False):
            return None
        return moment

    async def _async_harvest(self) -> dict[str, Any]:
        """One walk over the selected pages, in named steps.

        Where the panel stands decides whether to walk at all; then each page
        is read in turn, a page that cannot be reached or read costing only
        its own readings; then the panel is put back, or left on the live
        page, which is read last so that the walk ends there. A page that
        failed is no failure of the harvest while another page was read: its
        values age in the data and its sensors go unavailable by
        :meth:`is_fresh`. Only a walk that read no page at all is raised, so
        that Patience counts it and a display that has stopped answering is
        called unavailable after the usual three.
        """
        data: dict[str, Any] = dict(self.data or {})
        origin = await self._async_origin()
        if self._someone_at_the_panel(origin):
            return data
        self._note_home(origin)
        restore_to = self._restore_target(origin)

        read: list[int] = []
        missed: list[int] = []
        last_error: CtcWebError | None = None
        cost_before = self._cost()
        try:
            for page in self._walk_order():
                try:
                    done = await self._async_read_page(page, data)
                except CtcWebError as err:
                    # One page's trouble, not the display's: the next page
                    # starts from wherever the panel is, as every page does.
                    _LOGGER.debug("Page %s could not be read this time: %s", page.page, err)
                    last_error = err
                    done = False
                (read if done else missed).append(page.page)
        finally:
            await self._async_leave(restore_to)
        self.pages_read, self.pages_missed = read, missed
        self._say_cost(cost_before, read, missed)

        if not read:
            why = f": {last_error}" if last_error is not None else ""
            raise UpdateFailed(f"no selected page of the display could be read{why}")
        if missed:
            _LOGGER.debug(
                "Pages %s were not read this harvest; their readings keep their age until "
                "they are",
                missed,
            )
        if not data:
            raise UpdateFailed("no value could be read from the display")
        self.last_harvest = utcnow()
        return data

    def _cost(self) -> tuple[int, int]:
        """The client's request and tap counters, where it keeps them."""
        return int(getattr(self.client, "requests", 0)), int(getattr(self.client, "taps", 0))

    def _say_cost(self, before: tuple[int, int], read: list[int], missed: list[int]) -> None:
        """One debug line per walk: what it read and what it cost the display.

        The display's web server drops connections above a handful in flight
        and shares them with whoever stands at the panel, so a walk that costs
        hundreds of requests is a walk that fails for them; this is how that
        is seen in a log rather than guessed from timeouts.
        """
        requests, taps = self._cost()
        _LOGGER.debug(
            "Harvest read pages %s and missed %s at a cost of %s requests and %s taps",
            read,
            missed,
            requests - before[0],
            taps - before[1],
        )

    async def _async_origin(self) -> int:
        """Where the panel stands as the harvest begins."""
        try:
            return await self.client.async_current_page()
        except CtcWebError as err:
            raise UpdateFailed(f"could not read the panel state: {err}") from err

    def _someone_at_the_panel(self, origin: int) -> bool:
        """Whether somebody standing at the panel would be fighting us for it.

        If the page is not where we left it, leave it alone this round, whether
        one page is harvested or seven. But not for good: a panel parked on
        another page, or left somewhere by a restore that failed, would
        otherwise stop the harvest for ever and silently age every value.
        After HARVEST_SKIP_LIMIT rounds given way, the harvest goes ahead and
        puts the panel back on the page it found it on, which is where whoever
        moved it left it.
        """
        moved = self._expected_page is not None and origin != self._expected_page
        self._moved = moved
        if moved and self.skips_in_a_row < HARVEST_SKIP_LIMIT:
            self.skips_in_a_row += 1
            self.last_skip_reason = "panelen används av någon annan"
            if self.skips_in_a_row == 1:
                _LOGGER.info(
                    "The panel is on page %s, not on page %s where the harvest left it, "
                    "so somebody may be using it: this round is skipped and the display's "
                    "values keep their age. After %s skipped rounds the harvest goes ahead "
                    "anyway",
                    origin,
                    self._expected_page,
                    HARVEST_SKIP_LIMIT,
                )
            else:
                _LOGGER.debug(
                    "Panel is still on page %s, not %s; skipping this round (%s in a row)",
                    origin,
                    self._expected_page,
                    self.skips_in_a_row,
                )
            return True
        if self.skips_in_a_row:
            if moved:
                _LOGGER.info(
                    "The panel has been away from page %s for %s rounds, so the harvest "
                    "goes ahead and puts it back on page %s afterwards",
                    self._expected_page,
                    self.skips_in_a_row,
                    origin,
                )
            else:
                _LOGGER.info("The panel is back on page %s, so the harvest resumes", origin)
        self.skips_in_a_row = 0
        self.last_skip_reason = None
        return False

    def _note_home(self, origin: int) -> None:
        """First time in: whatever the panel was showing is where it belongs.

        Not after giving way, though: the page the panel stands on then is
        where somebody left it, not where it lives.
        """
        if (
            not self._moved
            and self.home_page is None
            and origin not in {p.page for p in self.pages}
        ):
            self.home_page = origin

    def _walk_order(self) -> list[SlowPage]:
        """The pages in the order the walk reads them: the live page last.

        The walk ends on the page it read last, so with the live page there
        the parking costs nothing: the taps that reach it are the walk's own,
        and the restore that follows finds the panel on it already.
        """
        if self.live_page is None:
            return list(self.pages)
        rest = [page for page in self.pages if page.page != self.live_page]
        return rest + [page for page in self.pages if page.page == self.live_page]

    def _restore_target(self, origin: int) -> int | None:
        """Where the panel is put back afterwards, if anywhere.

        Restoring to the page this cycle started on is right until a restore
        fails, after which that wrong page would become the new reference.
        The remembered home page breaks that loop. A round that goes ahead
        after giving way is the exception: the page the panel stands on is
        where somebody left it, and that is where it goes back, harvested page
        or not. With a live page the panel rests on that page instead,
        whatever "put the panel back" says, except after giving way: then it
        goes back where whoever moved it left it, as without one, and the
        next round, which finds it there, parks it again.
        """
        if self.live_page is not None and not self._moved:
            return self.live_page
        if not self.restore_page:
            return None
        if (
            not self._moved
            and origin in {p.page for p in self.pages}
            and self.home_page is not None
        ):
            return self.home_page
        return origin

    async def _async_read_page(self, page: SlowPage, data: dict[str, Any]) -> bool:
        """Walk to one page and read its screens into ``data``.

        True when at least one of its screens was read, which is when its
        values got a new moment in ``read_at``. The route was recorded during
        setup; replaying it is the only reliable way in, since the menu layout
        differs between models and cannot be derived at poll time.
        """
        if await self.client.async_current_page() != page.page and not (
            await self.client.async_goto_page(page.page, page.route)
        ):
            _LOGGER.debug("Could not reach page %s", page.page)
            return False
        values_by_screen: dict[int, list[Any]] = {}
        for screen in page.screens:
            try:
                values_by_screen[screen] = await self.client.async_vars(screen)
            except CtcWebError as err:
                _LOGGER.debug("Screen %s unreadable: %s", screen, err)
        if not values_by_screen:
            return False
        read_at = utcnow()
        for value in page.values:
            number = numeric_value(value, values_by_screen.get(value.screen, []))
            if number is not None:
                data[value.key] = number
                self.read_at[value.key] = read_at
        # The alarm the page prints, off the same values: no extra request.
        await self.alarms.async_note(self.client, page, values_by_screen, read_at)
        return True

    async def _async_leave(self, restore_to: int | None) -> None:
        """Put the panel back and note where it was left, for the next cycle."""
        restored = False
        if restore_to is not None:
            try:
                restored = await self._async_restore(restore_to)
            except CtcWebError:
                _LOGGER.debug("Could not restore the panel to page %s", restore_to)
        try:
            self._expected_page = await self.client.async_current_page()
        except CtcWebError:
            # The page could not be read just now. Forgetting where the panel
            # is would make the next round walk it whoever is standing there,
            # so the best knowledge stands instead: the page it was just put
            # back on, or failing that the page expected before this round.
            if restored:
                self._expected_page = restore_to
            _LOGGER.debug(
                "Could not read where the panel is after the round; expecting page %s",
                self._expected_page,
            )

    # ------------------------------------------------------- the live page

    async def async_read_live_page(self) -> bool:
        """Read the live page off the panel while the panel shows it, without a press.

        A GET of where the panel stands and one GET per screen of the page,
        the same two requests the walk makes once it stands on a page, and
        never a tap: nothing here can move the panel. Nothing is read while
        the panel shows another page, because somebody is using it or no walk
        has parked it yet; the rows keep their age until the next walk, which
        parks the panel again. Nothing is read while a walk holds the panel
        either: the harvest, a reading of the menu and the search for a page
        each move it under the lock, and a read in the middle of one would be
        of whatever page the walk stood on. The lock is looked at, not taken,
        so a form that asks for the panel is never told it is busy over two
        requests.

        What was read goes to the listeners as after a walk: the sensors,
        the integrators behind the estimated coefficient of performance and
        the alarm log, whose note from here is partial (alarms.AlarmWatch).
        The walk's own schedule and account stand: last_harvest, the pages
        read and missed and the next attempt are the walk's, and the pace of
        the walks is not reset by a read, which async_set_updated_data would
        do. True when the page was read.
        """
        page = next((p for p in self.pages if p.page == self.live_page), None)
        if page is None:
            return False
        self.live_attempt = utcnow()
        if self.client.panel.locked():
            self._live_read_nothing("en bläddring håller panelen")
            return False
        try:
            menu = list(await self.client.async_vars("menu"))
        except CtcWebError as err:
            self._live_read_nothing(f"panelens läge kunde inte läsas: {err}")
            return False
        self.live_menu_vars = menu
        shown = menu[0] if menu and isinstance(menu[0], int) else None
        self.live_panel_page = shown
        if shown != page.page:
            self._live_read_nothing(f"panelen visar sida {shown}, inte sida {page.page}")
            return False
        values_by_screen: dict[int, list[Any]] = {}
        for screen in page.screens:
            try:
                values_by_screen[screen] = await self.client.async_vars(screen)
            except CtcWebError as err:
                _LOGGER.debug("Screen %s of the live page unreadable: %s", screen, err)
        if not values_by_screen:
            self._live_read_nothing("ingen skärm på sidan svarade")
            return False
        read_at = utcnow()
        data = dict(self.data or {})
        for value in page.values:
            number = numeric_value(value, values_by_screen.get(value.screen, []))
            if number is not None:
                data[value.key] = number
                self.read_at[value.key] = read_at
        # The alarm the page prints, off the same values and with no extra
        # request; one page between two walks is never the round that closes
        # an episode.
        await self.alarms.async_note(self.client, page, values_by_screen, read_at, partial=True)
        self.data = data
        self.live_read_at = read_at
        self.live_reads += 1
        self.live_skips = 0
        self.live_reason = None
        _LOGGER.debug(
            "Live page %s read without a press at %s; the panel reports %s",
            page.page,
            read_at.isoformat(timespec="seconds"),
            menu,
        )
        self.async_update_listeners()
        return True

    @callback
    def async_add_live_status_listener(
        self, update_callback: Callable[[], None]
    ) -> Callable[[], None]:
        """Be told when a live read read nothing; returns the call that stops it."""
        self._live_status_listeners.append(update_callback)

        @callback
        def remove() -> None:
            if update_callback in self._live_status_listeners:
                self._live_status_listeners.remove(update_callback)

        return remove

    def _live_read_nothing(self, reason: str) -> None:
        """Note a live read that read nothing, and why, for the sensor and the diagnostics."""
        self.live_skips += 1
        self.live_reason = reason
        _LOGGER.debug(
            "Live page %s not read (%s in a row): %s", self.live_page, self.live_skips, reason
        )
        for update_callback in list(self._live_status_listeners):
            update_callback()

    async def async_restore_page(self, target: int) -> bool:
        """Put the panel back on ``target``, for callers outside the harvest.

        The caller holds ``client.panel``: this is handed to the walk that reads
        the system information page, which takes the lock for its whole trip.
        The lock is not reentrant, so taking it here would deadlock.
        """
        return await self._async_restore(target)

    async def _async_restore(self, target: int) -> bool:
        """Put the panel back, trying every way in that is known.

        A recorded route is the surest, stepping back works inside a submenu,
        and the home screen is the last resort so the panel is at least left
        somewhere sensible rather than deep in a menu. Stepping back is tried
        once: without a route async_goto_page has already stepped back
        towards the target, and asking again would press the same buttons on
        the same pages, the home screen's among them where it is not known.
        """
        route = next((p.route for p in self.pages if p.page == target and p.route), None)
        if await self.client.async_goto_page(target, route):
            return True
        if route and await self.client.async_step_back_to(target):
            return True
        if self.home_page is not None and target != self.home_page:
            home_route = next(
                (p.route for p in self.pages if p.page == self.home_page and p.route), None
            )
            if await self.client.async_goto_page(self.home_page, home_route):
                return True
        await self.client.async_goto_home()
        return await self.client.async_current_page() == target


#: How far past valid_until the check at an override's expiry is armed (F2.2).
_PAST = timedelta(microseconds=1)


class CtcControlManager:
    """Keep CTC's volatile control registers alive, and say truthfully which are.

    The 1000 block is write only and the controller forgets it roughly five
    minutes after the last write. Rewriting every minute keeps a wide margin, and
    stopping simply hands control back to the heat pump.

    An override counts as in force only from a write that reached the unit, and
    only while one has reached it within CONTROL_EXPIRY_SECONDS. The rule itself
    is in keepalive.py; this is the Home Assistant side of it: the timer, the
    log and the listeners.

    Writes take time, and two things can happen while one is on its way. A
    newer value for the same address: sets and the refresh take turns behind
    one lock, an address at a time, so a refresh never writes an older value
    over a newer one. A release, from the button, a select, the action or the
    entry being unloaded: those cannot wait for the lock, so the state is read
    again when the write is done, and a write that was overtaken by a release
    records nothing. A set reads the address's release count and the epoch of
    Släpp all styrning before it waits for the lock, so a release that comes
    while it queues behind a refresh, or while its write is the first of an
    address not yet held, wins as well (F1.2). The controller may then hold
    the value for its five minutes, but Home Assistant no longer claims it,
    which is what the release asked for.

    The keepalive learns that the controller has forgotten an address only
    from a write that fails, once a minute. So the first miss of a run also
    arms a check at the moment the address expires, which gives it up there
    and then unless a write has reached the unit since (F2.2).
    """

    def __init__(
        self,
        hass: HomeAssistant,
        client: CtcModbusClient,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._hass = hass
        self._client = client
        self._state = Keepalive(CONTROL_EXPIRY_SECONDS)
        # Wall clock rather than monotonic, because the entities show these
        # moments as timestamps and one clock has to serve both.
        self._clock = clock
        self._unsub = None
        #: The check at the moment an address expires, per address, armed at
        #: the first miss of a run and disarmed by a write that reaches the
        #: unit, a release, Släpp all styrning and the entry going.
        self._expiries: dict[int, Callable[[], None]] = {}
        #: The address whose write is on its way under the lock, if any: its
        #: outcome, not the check at the expiry, says whether it is still held.
        self._in_flight: int | None = None
        self._listeners: list[Callable[[], None]] = []
        #: Taken by a set for its write and by the refresh for each address.
        self._writing = asyncio.Lock()
        #: Set by async_stop and never cleared: the entry is going, and a
        #: refresh that is still running records nothing and writes no more.
        self._stopped = False

    @property
    def active(self) -> dict[int, int]:
        return self._state.active

    def get(self, address: int) -> int | None:
        return self._state.get(address)

    def written_at(self, address: int) -> datetime | None:
        """When a write of this address last reached the controller."""
        return self._moment(self._state.written_at(address))

    def valid_until(self, address: int) -> datetime | None:
        """Until when the controller is known to hold this address."""
        return self._moment(self._state.valid_until(address))

    def set_at(self, address: int) -> datetime | None:
        """When the value in force was set: first written, or last changed.

        Not moved by the keepalive's rewrites, so the sensor Aktiva
        överstyrningar can list it without a new state every minute (F6.2).
        """
        return self._moment(self._state.set_at(address))

    @staticmethod
    def _moment(seconds: float | None) -> datetime | None:
        if seconds is None:
            return None
        return datetime.fromtimestamp(seconds, tz=UTC)

    def written_attributes(self, address: int) -> dict[str, str]:
        """The two moments as entity attributes, for the numbers and the selects.

        Empty while nothing is in force, so an idle entity carries no stale times.
        """
        written = self.written_at(address)
        until = self.valid_until(address)
        if written is None or until is None:
            return {}
        return {
            "senast skriven": written.isoformat(timespec="seconds"),
            "gäller till": until.isoformat(timespec="seconds"),
        }

    def async_add_listener(self, listener: Callable[[], None]) -> Callable[[], None]:
        """Call back whenever an override is set or released; returns the undo."""
        self._listeners.append(listener)

        def _remove() -> None:
            if listener in self._listeners:
                self._listeners.remove(listener)

        return _remove

    def _notify(self) -> None:
        for listener in list(self._listeners):
            listener()

    async def async_set(self, address: int, raw: int | None) -> None:
        """Set or release one control register.

        The value is recorded only once the write has reached the controller, so
        a failed write never shows as control in force. A release waits for
        nothing and is recorded at once.
        """
        if raw is None:
            self._state.release(address)
            self._cancel_expiry(address)
            if not self._state:
                self._stop_timer()
            self._notify()
            return
        # Read before waiting for the lock, not after: a release that comes
        # while this queues behind a refresh is as much a release as one that
        # comes during the write, and the address may not be held yet, so the
        # generation, which a refresh's own write moves, cannot tell either.
        released = self._released_mark(address)
        async with self._writing:
            if self._stopped:
                raise CtcModbusError(
                    f"control register {address} was not written: the entry is being unloaded"
                )
            try:
                self._in_flight = address
                await self._client.async_write(address, raw)
            except CtcModbusError as err:
                if self._stopped or self._released_mark(address) != released:
                    raise
                # A write that fails while an older value is in force counts
                # against that value's time, like a failed refresh would.
                now = self._clock()
                if self._state.failed(address, now):
                    self._cancel_expiry(address)
                    self._said_released(address)
                    if not self._state:
                        self._stop_timer()
                    self._notify()
                elif self._state.failures(address) == 1:
                    self._said_missed(address, err)
                    self._watch_expiry(address)
                raise
            finally:
                self._in_flight = None
            if self._stopped or self._released_mark(address) != released:
                _LOGGER.debug(
                    "Control register %s was released while %s was on its way, so the "
                    "write is not recorded",
                    address,
                    raw,
                )
                return
            recovered = self._state.written(address, raw, self._clock())
            self._cancel_expiry(address)
        _LOGGER.debug("Control register %s set to %s", address, raw)
        if recovered:
            _LOGGER.info("Control register %s reached the controller again", address)
        self._ensure_timer()
        self._notify()

    def _released_mark(self, address: int) -> tuple[int, int]:
        """What a set compares before and after its write: the address's releases and the epoch."""
        return self._state.releases(address), self._state.epoch

    def async_release_all(self) -> None:
        """Stop overriding anything and hand the unit back to its own settings.

        Nothing is written: the controller forgets an override about five
        minutes after the last write, so it is enough to stop writing. A number
        has no release position in a list; on its own it is let go of by the
        action ctc_ecozenith.release, or the page's button on its row.
        """
        self._state.clear()
        self._cancel_expiries()
        self._stop_timer()
        self._notify()

    def _ensure_timer(self) -> None:
        if self._unsub is not None or not self._state:
            return
        from homeassistant.helpers.event import async_track_time_interval

        self._unsub = async_track_time_interval(
            self._hass,
            self._async_refresh,
            timedelta(seconds=CONTROL_KEEPALIVE_SECONDS),
        )

    def _stop_timer(self) -> None:
        if self._unsub is not None:
            self._unsub()
            self._unsub = None

    async def _async_refresh(self, _now) -> None:
        """Write every override again, and give up the ones the unit has forgotten.

        The state is read again for each address, under the write lock, so a
        value set a moment ago is what goes out, and read once more after the
        write, so an address released while the write was on its way is left
        released. A miss is a debug line, except the first of a run, which is a
        warning that says what happens next. The release is one warning; a
        write that reaches the unit again after misses is one info line.
        """
        changed = False
        for address in list(self._state.active):
            async with self._writing:
                if self._stopped:
                    return
                raw = self._state.get(address)
                if raw is None:
                    continue
                generation = self._state.generation(address)
                try:
                    self._in_flight = address
                    await self._client.async_write(address, raw)
                except CtcModbusError as err:
                    if self._stopped:
                        return
                    if self._state.generation(address) != generation:
                        continue
                    now = self._clock()
                    if self._state.failed(address, now):
                        self._cancel_expiry(address)
                        self._said_released(address)
                        changed = True
                    elif self._state.failures(address) == 1:
                        self._said_missed(address, err)
                        self._watch_expiry(address)
                    else:
                        _LOGGER.debug("Could not refresh control register %s: %s", address, err)
                    continue
                finally:
                    self._in_flight = None
                if self._stopped:
                    return
                if self._state.generation(address) != generation:
                    _LOGGER.debug(
                        "Control register %s was released while its refresh was on its way",
                        address,
                    )
                    continue
                recovered = self._state.written(address, raw, self._clock())
                self._cancel_expiry(address)
                if recovered:
                    _LOGGER.info("Control register %s reached the controller again", address)
                    changed = True
                else:
                    _LOGGER.debug("Control register %s refreshed with %s", address, raw)
        if changed:
            if not self._state:
                self._stop_timer()
            self._notify()

    def _said_missed(self, address: int, err: Exception) -> None:
        """The first miss of a run, whichever path it came by: what happens next."""
        until = self._moment(self._state.valid_until(address))
        _LOGGER.warning(
            "A write of control register %s did not reach the controller (%s). It holds "
            "the last value until about %s and the override is released then unless a "
            "write reaches it",
            address,
            err,
            until.isoformat(timespec="seconds") if until else "?",
        )

    def _watch_expiry(self, address: int) -> None:
        """Give the address up at the moment the controller forgets it (F2.2).

        Armed at the first miss of a run. Otherwise only a write that fails
        gives an address up, and the keepalive's next one may come up to a
        minute after valid_until, while the controls go on saying control is
        in force and the sensor goes on counting it, with nothing told to
        draw again. At valid_until this gives the address up unless a write
        has reached the unit since, which disarms it anyway, says so once,
        stops the keepalive when nothing is left and tells the listeners. A
        write of the address that is on its way just then decides instead:
        if it fails, the miss gives the address up, since the time has passed.

        Armed a microsecond past valid_until: the moment goes to the timer
        rounded to whole microseconds, and rounded down it would fire a hair
        before the manager's own clock counts the expiry as reached.
        """
        self._cancel_expiry(address)
        ok_at = self._state.written_at(address)
        until = self._moment(self._state.valid_until(address))
        if ok_at is None or until is None:
            return
        until += _PAST
        from homeassistant.helpers.event import async_track_point_in_time

        @callback
        def _expire(_now: datetime) -> None:
            self._expiries.pop(address, None)
            if self._stopped or self._in_flight == address:
                return
            if self._state.written_at(address) != ok_at:
                return
            if not self._state.expire(address, self._clock()):
                return
            self._said_released(address)
            if not self._state:
                self._stop_timer()
            self._notify()

        self._expiries[address] = async_track_point_in_time(self._hass, _expire, until)

    def _cancel_expiry(self, address: int) -> None:
        unsub = self._expiries.pop(address, None)
        if unsub is not None:
            unsub()

    def _cancel_expiries(self) -> None:
        for address in list(self._expiries):
            self._cancel_expiry(address)

    def _said_released(self, address: int) -> None:
        _LOGGER.warning(
            "Control register %s released: no write has reached the controller for %s s, "
            "so it has forgotten the value and Home Assistant no longer claims it",
            address,
            CONTROL_EXPIRY_SECONDS,
        )

    async def async_stop(self) -> None:
        """The entry is going: stop the timer, forget everything, outlast no write.

        Nothing is written; the controller lets go by itself. A refresh that is
        in the middle of a write is let finish that one write and then stops,
        and this waits for it, so that when it returns no write of this
        manager's is on its way or queued behind the client's shutdown.
        """
        self._stopped = True
        self._stop_timer()
        self._cancel_expiries()
        self._state.clear()
        async with self._writing:
            pass
