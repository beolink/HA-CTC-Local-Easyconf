"""The CTC Local Easyconf integration.

Reads a CTC heat pump locally over Modbus TCP, and optionally harvests the extra
values that only the display knows from its own web interface. Nothing goes near
myUplink or any other cloud.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry, ConfigEntryState
from homeassistant.const import CONF_HOST
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers import (
    config_validation as cv,
    device_registry as dr,
    issue_registry as ir,
)
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.helpers.storage import Store
from homeassistant.helpers.typing import ConfigType
from homeassistant.loader import async_get_integration
from homeassistant.util import dt as dt_util

from . import dashboard
from .alarms import STORAGE_VERSION as ALARM_STORAGE_VERSION, AlarmLog, confirm_window
from .catalogue import (
    MenuReading,
    async_discover_pages,
    async_rescan_pages,
    menu_is_due,
    menu_root,
    menu_wait,
    merge_menu,
    pages_from_storage,
    pages_to_storage,
    warned_of_no_menu,
)
from .const import (
    CONF_CHECK_UPDATES,
    CONF_ENABLE_CONTROL,
    CONF_FAST_INTERVAL,
    CONF_IDENTITY,
    CONF_IDENTITY_SCREENS,
    CONF_LANGUAGE,
    CONF_MENU,
    CONF_MENU_ROOT,
    CONF_MENU_VERSION,
    CONF_MODBUS_PORT,
    CONF_RESTORE_PAGE,
    CONF_SLAVE,
    CONF_SLOW_INTERVAL,
    CONF_SLOW_PAGES,
    CONF_TRY_QUICK_MENU,
    CONF_VISIT_SYSTEM_INFO,
    CONF_WEB_PORT,
    DEFAULT_FAST_INTERVAL,
    DEFAULT_MODBUS_PORT,
    DEFAULT_SLAVE,
    DEFAULT_SLOW_INTERVAL,
    DEFAULT_WEB_PORT,
    DOMAIN,
    LANG_SWEDISH,
    PLATFORMS,
    RELEASES_API,
    RELEASES_PAGE,
    SlowPage,
    has_display,
    identity_signal,
    web_interface_url,
)
from .coordinator import CtcControlManager, CtcModbusCoordinator, CtcWebCoordinator
from .cop import (
    MIN_CONSUMPTION_KWH,
    STORAGE_MINOR_VERSION as COP_MINOR_VERSION,
    ConsumptionSnapshot,
    CopTracker,
    ImmersionSnapshot,
    compressor_hours,
    cop_for_report,
    counter_fault,
    counters_read_at,
    current_totals,
    find_energy_totals,
    find_operating_hours,
    find_power_pair,
    immersion_at,
    modbus_consumption_answered,
    power_now,
    powered_on_hours,
)
from .debug_report import (
    async_setup_debug_report,
    async_stop_debug_report,
    without_the_debug_report,
)
from .harvest import STORAGE_VERSION as HARVEST_STORAGE_VERSION, HarvestMemory
from .identity import (
    Identity,
    IdentityScreens,
    async_read_identity,
    async_read_identity_via_panel,
    only_identity_differs,
)
from .integrate import STORAGE_VERSION as INTEGRATE_STORAGE_VERSION, PowerIntegrator
from .keys import (
    device_key,
    keep_hidden_rows,
    mac_address,
    previous_keys,
    union_by_page,
    unique_prefix,
    with_previous_keys,
)
from .modbus_api import CtcModbusClient, hold_library_quiet
from .modbus_probe import BUSY, CLOSED, async_classify_cached
from .seen import (
    STORAGE_MINOR_VERSION as SEEN_MINOR_VERSION,
    STORAGE_VERSION as SEEN_STORAGE_VERSION,
    SeenValues,
    migrate as migrate_seen,
)
from .seen_history import async_seed_from_statistics
from .services import async_register_services
from .stats import async_setup_stats, async_stop_stats
from .stats_extra import ErrorCounter, build_extra, entry_stem
from .transitions import TransitionWatch, find_starts_per_day, sample_of
from .updates import async_latest_release, check_is_due, newer
from .web_api import CtcWebClient, CtcWebError
from .websocket import async_register_websocket

_LOGGER = logging.getLogger(__name__)

#: How often the two lifetime counters are written down. Only one sample a day
#: is kept, so this is about not missing a day rather than about resolution.
COP_SAMPLE_INTERVAL = timedelta(hours=6)

#: Set up from config entries alone. async_setup exists for the actions and
#: the section card's websocket command, and hassfest asks an integration that
#: has one to say that it takes nothing from configuration.yaml.
CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Register what the integration offers once per run, whatever the number of pumps.

    The actions (roadmap R76) are registered here rather than with each entry,
    so they are there whether or not an entry has loaded, and an automation
    that calls one at a moment no entry is loaded is told so in words instead
    of finding no such action. The websocket command the section card asks
    for its cards (websocket.py, roadmap L11) is registered alongside.
    """
    async_register_services(hass)
    async_register_websocket(hass)
    return True


class SeenStore(Store):
    """The record of what the installation has seen, brought up to the current shape.

    Minor 1 held only the keys that had been something other than zero. Minor
    2 adds the keys that have ever been a number, which decides which display
    rows get an entity (roadmap L3). The rule itself is seen.migrate, free of
    Home Assistant and tested on its own; this is where Home Assistant calls
    it, once, when it finds a file of an older shape.

    The major version stays at 1, on purpose. Home Assistant refuses to load
    a file whose major version is above the one the code opens it with, and
    the release before this one opens the file as a plain Store at version 1,
    with nothing to catch the refusal: had this release written the file as
    version 2, a return to that release would have left the entry in
    SETUP_ERROR with every entity gone until somebody deleted the file by
    hand. At the same major, that release reads the file straight through,
    keeps the numeric set it does not know, writes it back as 1.1, and the
    next start of this release brings it up to 1.2 again. The same rule holds
    for the display, alarm and energy counter stores: a new shape is a new
    minor version, never a new major.
    """

    async def _async_migrate_func(
        self, old_major_version: int, old_minor_version: int, old_data: Any
    ) -> Any:
        return migrate_seen(old_data)


def seen_store(hass: HomeAssistant, entry_id: str) -> SeenStore:
    """The entry's seen store, opened as this release writes it: major 1, minor 2."""
    return SeenStore(
        hass,
        SEEN_STORAGE_VERSION,
        f"{DOMAIN}_{entry_id}_seen",
        minor_version=SEEN_MINOR_VERSION,
    )


_FAILURES: dict[str, ErrorCounter] = {}

#: Entries whose panel has been walked to the system information page in this
#: run. The walk moves the display, so it is attempted once, not at every
#: reload, and only while the identity is still missing.
_WALKED: set[str] = set()

#: Entries whose display has been swept for the identity's screens in this
#: run. The sweep asks the display for the values of every screen in its map,
#: 154 on an i255, so it runs once per run and not at every reload, which is
#: what follows the very write of what it found. The two screens it finds are
#: kept in the options and read on their own from then on.
_SWEPT: set[str] = set()

#: Attempts spent on each entry's menu in this run. Counted rather than flagged,
#: and kept across reloads, because writing the options reloads the entry: a flag
#: would leave a display that was busy for one moment with last version's menu
#: until somebody restarted Home Assistant, and no limit at all would let a panel
#: that never answers be walked over and over.
_MENU_TRIES: dict[str, int] = {}

#: When each entry's menu was last attempted, on the event loop's clock.
_MENU_LAST: dict[str, float] = {}

#: Each entry's one panel lock, kept across reloads like the tries and lent to
#: every web client the entry's set-ups build (CtcWebClient.panel). A lock per
#: client was a new lock at every reload, while a walk the old runtime had
#: begun went on: the reread_menu action walks on the client it started with,
#: and the new runtime's harvest then took a lock nobody held and walked the
#: same panel at once (roadmap R76). Two walkers on one panel record routes
#: that are wrong. With the lock kept here the new runtime's harvest and
#: catch-up queue behind the walk the old one is still making. The options
#: form borrows a loaded entry's client, and with it this lock; a client of
#: its own while the entry is not loaded is lent this lock as well.
_PANEL_LOCKS: dict[str, asyncio.Lock] = {}


def panel_lock(entry_id: str) -> asyncio.Lock:
    """The entry's one panel lock, the same one across every reload of the run."""
    lock = _PANEL_LOCKS.get(entry_id)
    if lock is None:
        lock = _PANEL_LOCKS[entry_id] = asyncio.Lock()
    return lock


#: How many times a run reads the menu again, and how long it waits in between.
MENU_READ_TRIES = 3
MENU_READ_RETRY = timedelta(minutes=5)

#: How long the catch-up task holds the panel after a set-up that switched on
#: what an earlier version had switched off (entity.py, roadmap R21). Home
#: Assistant reloads the entry RELOAD_AFTER_UPDATE_DELAY, thirty seconds,
#: after the last such change, and cancels the entry's tasks as it does: a
#: menu reading cut short there costs one of its tries, and the walk to the
#: system information page its only one. Fifteen seconds over, for a busy
#: core; a reload that comes in time finds nothing begun, and one that does
#: not come at all only puts the walks off by this much.
SWITCH_ON_HOLD = timedelta(seconds=45)

#: How each entry's last walk through the menu in this run went, as
#: MenuReading.outcome gives it, empty before the first (roadmap L6). Kept
#: outside the entry like the tries, since a reload follows every menu that is
#: written, and the very same dictionary sits on the runtime, where the daily
#: report and the diagnostics read it: it is updated in place, so a walk made
#: from the options form reaches a runtime that is still loaded. Never written
#: to the options: every write of them reloads the entry.
_MENU_OUTCOME: dict[str, dict[str, Any]] = {}


def _keep_menu_outcome(entry_id: str, reading: MenuReading) -> None:
    """Keep how a walk through the menu went, in place of how the last one went."""
    kept = _MENU_OUTCOME.setdefault(entry_id, {})
    kept.clear()
    kept.update(reading.outcome())


def menu_read_from_the_options(
    entry_id: str, now: float, reading: MenuReading | None = None
) -> None:
    """Book a reading of the menu that the options form made just now.

    The tries are for the reading owed after an update, and they stop after
    MENU_READ_TRIES misses so a panel that never answers is not walked over and
    over. Somebody ticking "read the menu again" is asking for a reading now,
    after those misses as much as before them: the count starts over, so the
    background reading that follows a form whose own reading missed is owed
    its tries again rather than left a version behind until a restart. The
    form's reading counts as the last attempt, so that background reading
    waits its pause before walking the panel again. How the form's walk went
    replaces how the last one went, so the report does not speak of a walk
    that has since been made again.
    """
    _MENU_TRIES.pop(entry_id, None)
    _MENU_LAST[entry_id] = now
    if reading is not None:
        _keep_menu_outcome(entry_id, reading)

ISSUE_PAGES = "pages_missing"
#: The two things the pages_missing issue can say, under its one id (R12): a
#: menu that was read with nothing ticked, and a menu that was never read.
ISSUE_PAGES_UNTICKED = "pages_unticked"
ISSUE_MENU_UNREAD = "menu_unread"
ISSUE_HISTORY_PAGE = "history_page_missing"
ISSUE_IDENTITY = "identity_incomplete"
ISSUE_UPDATE_AVAILABLE = "update_available"
ISSUE_MODBUS_BUSY = "modbus_busy"
#: Every issue id an entry can have, after its entry_id; the two texts of
#: ISSUE_PAGES share its id. All of them go when the entry is removed.
_ENTRY_ISSUES = (
    ISSUE_PAGES,
    ISSUE_HISTORY_PAGE,
    ISSUE_IDENTITY,
    ISSUE_UPDATE_AVAILABLE,
    ISSUE_MODBUS_BUSY,
)

#: How often GitHub is asked. Rarely: a release is not news that cannot wait.
UPDATE_CHECK_INTERVAL = timedelta(hours=24)

#: When each entry last had an answer from GitHub, on the event loop's clock.
#: Kept outside the entry like the menu tries, because a reload used to ask
#: again at once, and saving the options a few times in a row was a few
#: questions in a row for an answer that cannot have changed.
_RELEASE_CHECKED: dict[str, float] = {}


async def _async_check_release(
    hass: HomeAssistant, entry: CtcConfigEntry, version: str
) -> None:
    """Say in the repairs view when a newer release is out.

    Home Assistant only knows about updates for what HACS installed, so a copy
    put in place by hand is never offered one. Switched off in the options for
    anyone who would rather not have the integration ask GitHub anything.

    The issue is touched only on an answer. No answer, a network hiccup or a
    rate limit, used to fall through to deleting it, so a notice that had stood
    for days vanished at the first reload that happened to meet a quiet GitHub;
    now it stands as it was until GitHub says otherwise. An answer within the
    day, by this run, is not asked for again, so a reload costs no question.
    """
    issue_id = f"{entry.entry_id}_{ISSUE_UPDATE_AVAILABLE}"
    if not entry.options.get(CONF_CHECK_UPDATES, True):
        ir.async_delete_issue(hass, DOMAIN, issue_id)
        # Switched back on, it is asked at once rather than within the day.
        _RELEASE_CHECKED.pop(entry.entry_id, None)
        return
    now = hass.loop.time()
    if not check_is_due(
        _RELEASE_CHECKED.get(entry.entry_id), now, UPDATE_CHECK_INTERVAL.total_seconds()
    ):
        return
    latest = await async_latest_release(async_get_clientsession(hass), RELEASES_API)
    if latest is None:
        _LOGGER.debug("The release check got no answer, so the notice stands as it was")
        return
    _RELEASE_CHECKED[entry.entry_id] = now
    if newer(version, latest):
        ir.async_create_issue(
            hass,
            DOMAIN,
            issue_id,
            is_fixable=False,
            severity=ir.IssueSeverity.WARNING,
            translation_key=ISSUE_UPDATE_AVAILABLE,
            translation_placeholders={"installed": version, "latest": latest},
            learn_more_url=RELEASES_PAGE,
        )
    else:
        ir.async_delete_issue(hass, DOMAIN, issue_id)


def _async_review_issues(
    hass: HomeAssistant, entry: CtcConfigEntry, runtime: CtcRuntime
) -> None:
    """Say in the repairs view what only the owner can settle.

    Three things the integration cannot do for itself: whether the panel may be
    walked to any page at all, which pages those are, and showing the system
    information page once so the display writes its serial number into it.
    Nothing ticked means the display is never read, so there is no delivered
    heat and no coefficient of performance, and that is worth saying plainly:
    an empty list is as often a menu that could not be read at set-up as it is
    a deliberate choice.

    An entry on Modbus alone has none of the three to settle, and is told
    apart first; see _display_notices_apply.
    """
    if not _display_notices_apply(hass, entry):
        return

    def review(key: str, needed: bool, text: str | None = None) -> None:
        issue_id = f"{entry.entry_id}_{key}"
        if needed:
            ir.async_create_issue(
                hass,
                DOMAIN,
                issue_id,
                is_fixable=False,
                severity=ir.IssueSeverity.WARNING,
                translation_key=text or key,
            )
        else:
            ir.async_delete_issue(hass, DOMAIN, issue_id)

    # One issue, two texts (R12). A stored menu with nothing ticked is the
    # owner's choice and the text says where to tick; no stored menu means
    # the reading never got through, which is tried again by itself and is
    # nothing the tick boxes can mend, since they are built from that menu.
    # The id stays the same, so an issue somebody has ignored stays ignored
    # when its text changes.
    review(
        ISSUE_PAGES,
        runtime.web is None,
        ISSUE_PAGES_UNTICKED
        if pages_from_storage(entry.options.get(CONF_MENU))
        else ISSUE_MENU_UNREAD,
    )
    review(ISSUE_HISTORY_PAGE, runtime.web is not None and runtime.energy_out is None)
    review(ISSUE_IDENTITY, not runtime.identity.serial)


def _display_notices_apply(hass: HomeAssistant, entry: CtcConfigEntry) -> bool:
    """Whether the repairs view's notices about the display concern this entry at all.

    An entry set up on Modbus alone, because the display's web interface did
    not answer (roadmap R11), has no pages to tick, no history page and no
    system information page to read, so the three notices would only ask for
    what cannot be done. Any of them left from before is taken away.
    """
    if has_display(entry.data):
        return True
    for key in (ISSUE_PAGES, ISSUE_HISTORY_PAGE, ISSUE_IDENTITY):
        ir.async_delete_issue(hass, DOMAIN, f"{entry.entry_id}_{key}")
    return False


@callback
def _async_adopt_identity(
    hass: HomeAssistant, entry: CtcConfigEntry, runtime: CtcRuntime, found: Identity
) -> bool:
    """Take what a later reading of the identity found, without a reload.

    The identity used to be written to the options alone, and the reload that
    every write of the options brings was what carried it into the device, the
    sensors and the repairs view. A reload closes Modbus for the controller's
    settle time, puts the daily report's delay back to the start, asks GitHub
    again and shows the CTC page as "no heat pump is running", all for six
    strings that change nothing about what is polled. So everything that reads
    the identity is told directly instead: the runtime, which the report and
    the repairs view read live; the device in the registry, whose page shows
    the serial number and the versions; and the identity sensors, through a
    dispatcher signal. The options are still written, so the next start has
    the identity without asking the display, and the reload listener leaves a
    write that changes nothing but the identity alone. Returns whether
    anything new was found.
    """
    merged = runtime.identity.merged_with(found)
    if merged.as_dict() == runtime.identity.as_dict():
        return False
    runtime.identity = merged
    model = entry.data.get("model", "CTC")
    details = {
        "model": f"{model} + {merged.heatpump_model}" if merged.heatpump_model else model,
        "serial_number": merged.serial,
        "sw_version": merged.display_firmware,
        "hw_version": merged.bootloader,
    }
    known = {key: value for key, value in details.items() if value is not None}
    # The runtime's own DeviceInfo as well, so an entity added from now on,
    # an identity sensor among them, carries the same device details.
    runtime.device.update(known)
    mac = mac_address(merged.mac)
    connections = {(dr.CONNECTION_NETWORK_MAC, mac)} if mac is not None else set()
    if connections:
        runtime.device["connections"] = connections
    registry = dr.async_get(hass)
    identifier = next(iter(runtime.device["identifiers"]))
    for device in dr.async_entries_for_config_entry(registry, entry.entry_id):
        if identifier in device.identifiers:
            registry.async_update_device(device.id, **known)
            if connections and not connections <= device.connections:
                # The MAC on the device card, for the DHCP flow to know the
                # unit by. A Home Assistant before devices were kept per
                # config entry refuses a MAC another integration's device
                # already has; the card goes without it then, nothing else.
                try:
                    registry.async_update_device(device.id, merge_connections=connections)
                except Exception as err:  # the MAC is a convenience
                    _LOGGER.debug("Could not put the MAC on the device: %s", err)
    async_dispatcher_send(hass, identity_signal(entry.entry_id))
    _async_review_issues(hass, entry, runtime)
    hass.config_entries.async_update_entry(
        entry, options={**entry.options, CONF_IDENTITY: merged.as_dict()}
    )
    return True


async def _async_catch_up(
    hass: HomeAssistant,
    entry: CtcConfigEntry,
    runtime: CtcRuntime,
    client: CtcWebClient,
    version: str,
) -> None:
    """Read the menu again after an update, and read the identity again.

    Nothing here runs during set-up, so set-up never waits on the display and
    a restart never moves the panel. The menu re-read and the walk to the
    system information page both move the panel, and neither runs while the
    harvester is walking: the panel lock keeps them apart. A new version reads
    the whole menu again, because a newer parser can make sense of rows and
    pages the old one passed over, and pages nobody has switched off are
    harvested.

    The identity is read here too, at every start, so a firmware update of
    the display reaches the device page without anyone asking. The display
    only writes it into a screen once that screen has been shown on the
    panel, so a reading before that finds nothing and the gaps are filled
    the first time someone opens the page; the reading itself moves nothing,
    and a field it finds empty keeps the value it had. The two screens it
    lives on are kept in the options once found and read on their own; the
    sweep that finds them, over the values of every screen in the map, runs
    once per run and only for a screen that is not known yet.

    A menu that could not be read is tried again a few minutes later in the same
    run, and said out loud once the tries are spent. A display that was busy for
    one moment used to leave the stored menu a version behind until somebody
    restarted Home Assistant, with a single debug line as the only trace. The walk
    to the system information page keeps its one attempt: where it gives up, the
    menu itself has no way there, so repeating it would only move the panel.

    A set-up that switched on what an earlier version had switched off is
    followed by Home Assistant's reload half a minute later, which cancels
    this task and the harvest wherever they stand. So such a set-up holds the
    panel first, for SWITCH_ON_HOLD, before anything is counted or walked:
    the first harvest queues behind it, and the reload finds neither begun.
    The lock is taken before this task first yields, since it starts eagerly,
    so no harvest gets in ahead of it.
    """
    if getattr(runtime, "switched_on", 0) and has_display(entry.data):
        _LOGGER.debug(
            "Holding the panel for %s s, until the reload that switching entities on "
            "brings",
            int(SWITCH_ON_HOLD.total_seconds()),
        )
        async with client.panel:
            await asyncio.sleep(SWITCH_ON_HOLD.total_seconds())
    await _async_check_release(hass, entry, version)
    if not has_display(entry.data):
        # On Modbus alone (roadmap R11): no menu to read, no identity on a
        # screen, no panel to walk. Each would only wait out the display's
        # timeouts, up to half a minute a start, for nothing.
        return
    while True:
        changed: dict[str, Any] = {}
        try:
            if _menu_is_due(entry, version):
                wait = menu_wait(
                    _MENU_LAST.get(entry.entry_id),
                    hass.loop.time(),
                    MENU_READ_RETRY.total_seconds(),
                )
                if wait:
                    _LOGGER.debug(
                        "Reading the display's menu again in %s s", int(wait)
                    )
                    asked = _MENU_LAST.get(entry.entry_id)
                    await asyncio.sleep(wait)
                    if _MENU_LAST.get(entry.entry_id) != asked:
                        # The options form or the reread_menu action read the
                        # menu while this slept, and that reading counts as the
                        # last attempt (menu_read_from_the_options): the pause
                        # starts over from it rather than ending now.
                        continue
                _MENU_TRIES[entry.entry_id] = _MENU_TRIES.get(entry.entry_id, 0) + 1
                _MENU_LAST[entry.entry_id] = hass.loop.time()
                changed.update(await _async_reread_menu(client, entry, version))

            identity = runtime.identity
            screens = IdentityScreens.from_dict(entry.options.get(CONF_IDENTITY_SCREENS))
            known_screens = screens.as_dict()
            # At every start, a complete identity as much as one with gaps
            # (R21): the display's firmware gets updated, and a version read
            # once and never again would stand on the device page for good.
            # Static, so the panel stays where it is: the two known screens
            # are one request each, and a screen not known yet is looked for
            # by the sweep once per run and written down for the next start.
            sweep = entry.entry_id not in _SWEPT
            _SWEPT.add(entry.entry_id)
            found = await async_read_identity(client, screens, sweep=sweep)
            identity = identity.merged_with(found)

            if (
                not identity.serial
                and entry.options.get(CONF_VISIT_SYSTEM_INFO, True)
                and entry.entry_id not in _WALKED
            ):
                _WALKED.add(entry.entry_id)
                async with client.panel:
                    found = await async_read_identity_via_panel(
                        client,
                        restore=runtime.web.async_restore_page if runtime.web else None,
                        # Not in the options form: the press behind it has never
                        # been tried on a real panel, see const.CONF_TRY_QUICK_MENU.
                        quick_menu=bool(entry.options.get(CONF_TRY_QUICK_MENU, False)),
                        screens=screens,
                    )
                identity = identity.merged_with(found)
            # Into the device, the sensors and the options without a reload:
            # nothing about the identity needs one. The screens it was found
            # on are kept the same way, so a later start reads them alone.
            if identity.as_dict() != runtime.identity.as_dict():
                _async_adopt_identity(hass, entry, runtime, identity)
            if screens.as_dict() != known_screens:
                changed[CONF_IDENTITY_SCREENS] = screens.as_dict()
        except Exception as err:  # catching up must never break the entry
            _LOGGER.debug("Could not catch up with the display: %s", err)

        if changed:
            options = {**entry.options, **changed}
            if _takes_a_reload(entry, runtime, options):
                # The menu: writing it reloads the entry, which is where the
                # new pages are picked up. The reload cancels this task and
                # starts it over, and the attempts already spent are
                # remembered, so a menu that is still owed is tried again
                # there rather than endlessly. Written under the panel lock,
                # which the harvest holds while it walks: the reload cancels
                # the entry's tasks, the harvest among them, and a harvest
                # cut short mid-walk would leave the panel on whatever page
                # it had reached. The lock is fair, so a harvest that queued
                # on it while the menu was read, the first one after an
                # update is one, has walked by the time this gets it.
                async with client.panel:
                    # That harvest's store is written out now rather than
                    # after its delay: the set-up the reload brings reads it,
                    # and finding it owes the next harvest an interval later
                    # instead of walking the panel again at once.
                    memory = getattr(runtime, "harvest_memory", None)
                    if memory is not None:
                        await memory.async_flush()
                    hass.config_entries.async_update_entry(entry, options=options)
                return
            # The screens alone: written in place like the identity. The
            # listener leaves such a write be, so no reload follows and
            # nothing starts this task over; it goes on by itself to the menu
            # it may still owe. The next round reads the screens back out of
            # the options and writes nothing again.
            hass.config_entries.async_update_entry(entry, options=options)
        if not _menu_is_due(entry, version):
            return


def _menu_is_due(entry: CtcConfigEntry, version: str) -> bool:
    """Whether this run still owes the entry a fresh reading of the menu."""
    return menu_is_due(
        entry.options.get(CONF_MENU_VERSION),
        version,
        _MENU_TRIES.get(entry.entry_id, 0),
        MENU_READ_TRIES,
    )


async def _async_reread_menu(
    client: CtcWebClient, entry: CtcConfigEntry, version: str
) -> dict[str, Any]:
    """The whole menu again, or nothing at all when the display would not give it.

    Pages nobody has switched off stay on and pages somebody switched off stay
    off; see merge_menu. Only a reading that worked stamps the version, so a
    failed one is owed rather than forgotten. A reading that was interrupted
    counts as failed here: the sweep lost the root or could not reach a page
    again, so a page it would otherwise have found is missing from it, and
    folding it in would make that page and its entities disappear until the
    next release. So does a reading with a gap: a page whose screen or caption
    did not answer is left out of it by the sweep (MenuReading.gaps), since
    written over the stored page it would rename a row and the registry
    tidy-up would then take that row's entity. The stored menu stands and the
    reading is tried again.

    How far the walk got is kept for the report and the diagnostics, and the
    line that ends the tries says it. Where the walk found no menu it has
    said so on warning itself, once this run, so that line is info then: the
    log says a menu that cannot be read once per start (F3.3). It says no
    menu has been read at all where none is stored, rather than that an
    earlier one is kept. A display that stopped answering partway is raised
    once that is done, as it was before the walk gave it back in the reading,
    so the rest of the round waits for the next one.

    A reading the options form or the reread_menu action made while this one
    waited for the panel is not made again (roadmap R76). Both book theirs as
    the last attempt (menu_read_from_the_options) the moment their walk is
    done, before anything that queued on the lock gets it, so a last attempt
    that moved while this waited is somebody else's walk of the same menu:
    this gives back nothing and walks nothing. Where the action read the whole
    menu, its write queues on the lock behind this and reloads the entry once
    it gets it. The try already counted is not given back, since that reading
    started the count over. The options are read once the walk is done, not
    before the wait.
    """
    attempted = _MENU_LAST.get(entry.entry_id)
    async with client.panel:
        if _MENU_LAST.get(entry.entry_id) != attempted:
            _LOGGER.debug(
                "The display's menu was read while this reading waited for the panel, "
                "so it is not walked again"
            )
            return {}
        # Without the operation data root there is no menu to read, only the
        # page the panel happens to show, and that must not replace the menu.
        reading = await async_discover_pages(client, require_root=True)
    options = entry.options
    _keep_menu_outcome(entry.entry_id, reading)
    if not reading.pages or not reading.complete:
        spent = _MENU_TRIES.get(entry.entry_id, 0)
        if reading.gaps:
            gaps = ", ".join(str(page) for page in reading.gaps)
            what = (
                f"Page(s) {gaps} of the display's menu were read with a gap, a screen or a "
                "caption that did not answer, so the reading is not the whole menu"
            )
        elif reading.pages:
            what = "Only part of the display's menu could be read"
        else:
            what = f"The display's menu could not be read ({reading.how_far()})"
        stored = bool(pages_from_storage(options.get(CONF_MENU)))
        if spent >= MENU_READ_TRIES:
            if stored:
                kept = (
                    "so the menu stored by an earlier version is kept and anything a "
                    "newer one would make sense of is not harvested"
                )
            else:
                kept = "and no menu has been read before, so no page of the display is harvested"
            said = (
                f"{what} in {MENU_READ_TRIES} attempts, {kept}. Choose Read the display's "
                "menu again under Configure to try again"
            )
            if not reading.pages and warned_of_no_menu(client):
                _LOGGER.info("%s", said)
            else:
                _LOGGER.warning("%s", said)
        else:
            _LOGGER.debug(
                "%s (attempt %s of %s); %s and trying again in %s minutes",
                what,
                spent,
                MENU_READ_TRIES,
                "keeping the stored one" if stored else "none is stored yet",
                int(MENU_READ_RETRY.total_seconds() // 60),
            )
        if reading.error is not None:
            raise CtcWebError(reading.error)
        return {}
    return _menu_changes(options, reading, version)


def _menu_changes(
    options: Any, reading: MenuReading, version: str
) -> dict[str, Any]:
    """What a whole reading of the menu writes into the options.

    The reading folded into the stored menu (merge_menu), every row with the
    key it had where that was another, the selection that follows, the
    version stamp and the root the sweep found. One rule for the reading
    owed after an update and for the reread_menu action (roadmap R76), so
    the two cannot fold a menu in two ways.
    """
    stored_menu = pages_from_storage(options.get(CONF_MENU))
    stored_selection = pages_from_storage(options.get(CONF_SLOW_PAGES))
    # Both are asked: an installation from before the whole menu was kept
    # has only its harvested pages, and a page that stands in one but not the
    # other must not be lost by a reading that misses it either.
    root = reading.root if reading.root is not None else options.get(CONF_MENU_ROOT)
    menu, selected = merge_menu(
        union_by_page(stored_menu, stored_selection),
        [page.page for page in stored_selection],
        reading.pages,
        root=root,
        screen_pages=reading.screen_pages,
    )
    reached = {page.page for page in reading.pages}
    if kept := [page.page for page in menu if page.page not in reached]:
        _LOGGER.info(
            "The reading of the display's menu did not reach page(s) %s; they are kept "
            "as they were, since a page can be hidden in some operating states, and "
            "a page is let go by unticking it",
            ", ".join(str(page) for page in kept),
        )
    # A row the display hides at this moment stays on its page (a slot that
    # shows the compressor's speed or a status by turns), and every row
    # carries the key it had where that was another, so the set-up the write
    # brings moves its entity and its stored values over (L2).
    before = union_by_page(stored_menu, stored_selection)
    menu = with_previous_keys(before, keep_hidden_rows(before, menu))
    chosen = set(selected)
    changed = {
        CONF_MENU: pages_to_storage(menu),
        CONF_SLOW_PAGES: pages_to_storage([page for page in menu if page.page in chosen]),
        CONF_MENU_VERSION: version,
    }
    if reading.root is not None:
        changed[CONF_MENU_ROOT] = reading.root
    return changed


def _still_loaded_with(entry: CtcConfigEntry, runtime: CtcRuntime) -> bool:
    """Whether the entry is loaded, and with the runtime a walk was started on."""
    return (
        entry.state is ConfigEntryState.LOADED
        and getattr(entry, "runtime_data", None) is runtime
    )


async def async_reread_menu_now(
    hass: HomeAssistant, entry: CtcConfigEntry, version: str
) -> bool:
    """Read the display's whole menu now, for the reread_menu action (roadmap R76).

    The same reading as "read the menu again" in the options (R13): under the
    entry's one panel lock (panel_lock), which a harvest, the reading owed
    after an update and the walk to the system information page take as
    well, and never waited for. A panel already taken raises PanelBusy at
    once and nothing is walked or written. The tries the background counts
    start over (menu_read_from_the_options), so a menu it had given up on is
    owed again.

    A whole reading is folded in by the background's own rule (_menu_changes):
    a page switched off stays off, a new page comes on, the version is
    stamped, and the write reloads the entry, which is where new pages are
    picked up. Anything less writes nothing and leaves the stored menu as it
    is, since nobody stands at a form to tick what an interrupted sweep found.
    The write waits for the panel lock, like the catch-up's: a harvest that
    queued while the menu was read walks to its end first, and its store is
    written out before the reload, so the set-up that follows owes the next
    harvest an interval later rather than walking the panel again at once.
    The lock is let go between the walk and the write, so a reading the
    catch-up queued meanwhile finds the menu read (_async_reread_menu) and
    gives the lock back at once rather than walking under a write that is
    about to reload the entry and cut it short.

    The options are worked out under the lock, from the entry's options at
    that moment, so a save in Configure made while the write waited is not
    written over. Nothing is written to an entry that has been unloaded or
    reloaded since the walk began: the walk went on with the old runtime's
    client, and something changed beneath it: a save in Configure, a new
    address or entities switched on. The action says so as a menu that could
    not be read. Returns whether the menu was written.
    """
    runtime = entry.runtime_data
    client = runtime.web_client
    try:
        reading = await async_rescan_pages(client)
    except CtcWebError as err:
        _LOGGER.warning("Could not read the display's menu: %s", err)
        reading = MenuReading(error=str(err))
    menu_read_from_the_options(entry.entry_id, hass.loop.time(), reading)
    if not reading.pages or not reading.complete:
        _LOGGER.info(
            "The reread_menu action could not read the display's whole menu (%s), so "
            "the stored menu stands",
            reading.how_far() if not reading.pages else "only part of it was read",
        )
        return False
    reloaded = (
        "%s was reloaded or unloaded while the reread_menu action read its menu, so "
        "nothing is written and the stored menu stands; ask again"
    )
    if not _still_loaded_with(entry, runtime):
        _LOGGER.info(reloaded, entry.title)
        return False
    async with client.panel:
        if not _still_loaded_with(entry, runtime):
            _LOGGER.info(reloaded, entry.title)
            return False
        memory = getattr(runtime, "harvest_memory", None)
        if memory is not None:
            await memory.async_flush()
        # Looked at again after the flush, the one wait here, and nothing
        # awaited from the look to the write.
        if not _still_loaded_with(entry, runtime):
            _LOGGER.info(reloaded, entry.title)
            return False
        options = {**entry.options, **_menu_changes(entry.options, reading, version)}
        hass.config_entries.async_update_entry(entry, options=options)
    return True


async def _async_name_the_failure(
    hass: HomeAssistant, entry: CtcConfigEntry, host: str, port: int, slave: int
) -> ConfigEntryNotReady | None:
    """Say what kept Modbus away from a set-up that could not read a register (roadmap L12).

    pymodbus answers a refused port, a controller whose one place another
    client holds, and a passing hiccup alike, so the entry sat in setup_retry
    with "no Modbus register could be read" whatever the cause, and with Home
    Assistant's backoff growing to ten minutes a stranger with a modbus: block
    left in configuration.yaml waited long on an error without a name. A raw
    probe tells the cases apart; see modbus_probe. It runs here only, after
    this attempt's client has been shut, so it never meets a session that
    works, and a verdict is reused for ten minutes so the retries do not knock
    twice as often.

    A taken place is said in the repairs view, where an owner sees it; Home
    Assistant logs a set-up that is not ready on info only. The notice goes at
    the first reading that works, or when a later probe finds another cause.
    Returns the reason to raise, or None to raise the failure as it was.
    """
    issue_id = f"{entry.entry_id}_{ISSUE_MODBUS_BUSY}"
    try:
        verdict = await async_classify_cached(host, port, slave)
    except Exception:  # naming the failure must never hide it
        _LOGGER.debug("Could not probe the Modbus port after a failed set-up", exc_info=True)
        return None
    if verdict != BUSY:
        ir.async_delete_issue(hass, DOMAIN, issue_id)
    if verdict == BUSY:
        if ir.async_get(hass).async_get_issue(DOMAIN, issue_id) is None:
            _LOGGER.warning(
                "The heat pump accepts a Modbus connection and drops it at the first "
                "request: another client holds its one Modbus place, a modbus: block in "
                "configuration.yaml, a test tool or a session that was never let go"
            )
        ir.async_create_issue(
            hass,
            DOMAIN,
            issue_id,
            is_fixable=False,
            severity=ir.IssueSeverity.WARNING,
            translation_key=ISSUE_MODBUS_BUSY,
        )
        return ConfigEntryNotReady(
            "Another client holds the heat pump's only Modbus connection",
            translation_domain=DOMAIN,
            translation_key="modbus_busy",
        )
    if verdict == CLOSED:
        return ConfigEntryNotReady(
            "The heat pump does not accept a connection on its Modbus port",
            translation_domain=DOMAIN,
            translation_key="modbus_closed",
        )
    return None


def _stats_extra_for(hass: HomeAssistant, entry: CtcConfigEntry) -> dict[str, Any]:
    """The integration's part of the anonymous daily report.

    Resolved when the report is built, not when it is armed: the controller
    allows a single Modbus client, so a busy or absent controller makes the
    set-up raise and Home Assistant retries it for as long as that lasts.
    The report has to say "installed and unreachable" rather than nothing at
    all. It never opens a connection of its own, it reads what the
    coordinators already have. See stats_extra.py for exactly what is sent.
    """
    runtime = getattr(entry, "runtime_data", None)
    failures = _FAILURES.setdefault(entry.entry_id, ErrorCounter())
    # The menu as stored, and how far this run's last walk through it got:
    # whether no page is read because nothing is ticked or because the menu
    # was never read, and if never, at which step (roadmap R12 and L6).
    menu = {
        "menu_pages": len(pages_from_storage(entry.options.get(CONF_MENU))),
        "menu_home": _MENU_OUTCOME.get(entry.entry_id, {}).get("home_found"),
        "menu_root": _MENU_OUTCOME.get(entry.entry_id, {}).get("root_entered"),
    }
    # The settings file stem, which the report sends as family_<stem> only for
    # a model it reports as "other" (roadmap R19).
    stem = entry_stem(entry.data)
    if runtime is None:
        # Set-up has not finished. The model is the one thing the config
        # knows; a read failure is recorded so a controller that never
        # answers is visible rather than silent.
        return build_extra(
            entry.data.get("model"),
            has_display=False,
            control_enabled=False,
            page_count=0,
            read_failures=1,
            settings_stem=stem,
            **menu,
        )
    # Recognising a counter and reading it are different things, so the report
    # says which of the two happened. See stats_extra.build_extra.
    heat, consumed = current_totals(runtime)
    # And whether the numbers themselves are wrong. Only a fault carries the two
    # totals with it: a machine that has simply not counted far enough yet is
    # waiting, not broken, and sends nothing but the flag that says so.
    # The compressor's hours, not the unit's: a machine can stand powered on for
    # years without the compressor running, and two zeroes are then an honest
    # account rather than a fault. See cop.counter_fault.
    fault = counter_fault(heat, consumed, compressor_hours(runtime))
    integrator = getattr(runtime, "integrator", None)
    return build_extra(
        entry.data.get("model"),
        has_display=runtime.web is not None,
        control_enabled=runtime.control_enabled,
        page_count=len(runtime.pages),
        read_failures=failures.delta(runtime.modbus.read_failures),
        heatpump_model=runtime.identity.heatpump_model,
        serial=runtime.identity.serial,
        display_firmware=runtime.identity.display_firmware,
        heatpump_firmware=runtime.identity.heatpump_firmware,
        control_firmware=(runtime.modbus.data or {}).get("control_sw"),
        history_page=bool(runtime.operating_hours),
        heat_counter=runtime.energy_out is not None,
        consumption_counter=runtime.energy_in is not None,
        consumption_modbus=modbus_consumption_answered(runtime.modbus.answered),
        heat_total=heat is not None,
        consumption_total=consumed is not None,
        cop_floor=consumed is not None and not fault and consumed < MIN_CONSUMPTION_KWH,
        cop_stuck=fault == "stuck",
        power_rows=getattr(runtime, "power_out", None) is not None
        and getattr(runtime, "power_in", None) is not None,
        # And whether they handed over numbers. A row that is there and gives
        # nothing is what a switched off heat pump looks like, and it used to be
        # indistinguishable from a page without the rows at all.
        power_values=power_now(runtime) is not None,
        cop_integrated=integrator is not None
        and current_totals(runtime) == integrator.totals
        and integrator.totals[0] is not None,
        cop_implausible=fault == "implausible",
        heat_total_kwh=heat if fault else None,
        consumption_total_kwh=consumed if fault else None,
        settings_stem=stem,
        **menu,
        **cop_for_report(runtime),
    )


async def _async_arm_statistics(hass: HomeAssistant, entry: CtcConfigEntry) -> None:
    """Arm the daily report before the first Modbus call.

    Home Assistant runs an entry's on-unload callbacks after every failed
    set-up attempt and retries for as long as the controller stays away, so a
    reporter armed at the end of a successful set-up goes quiet exactly then.
    Stopped only from async_unload_entry, which a failed attempt never
    reaches. On unless the user switches it off in the options.
    """
    try:
        integration = await async_get_integration(hass, DOMAIN)
        await async_setup_stats(
            hass, entry, DOMAIN, str(integration.version),
            extra=lambda: _stats_extra_for(hass, entry),
        )
    except Exception:  # statistics must never break a set-up
        _LOGGER.debug("Could not arm the statistics reporter", exc_info=True)


async def _async_arm_debug_report(hass: HomeAssistant, entry: CtcConfigEntry) -> None:
    """Arm the voluntary debug report, right after the daily one and for the same reason.

    Off unless the owner switches it on, and then it reports while the
    controller is away as much as while it answers: armed before the first
    Modbus call, stopped only from async_unload_entry and async_remove_entry.
    It follows its option by itself, as a listener of the entry's own, so a
    switch of it reloads nothing; see debug_report.py.
    """
    try:
        integration = await async_get_integration(hass, DOMAIN)
        async_setup_debug_report(hass, entry, str(integration.version))
    except Exception:  # the debug report must never break a set-up
        _LOGGER.debug("Could not arm the debug report", exc_info=True)


@callback
def _async_move_row_keys(
    hass: HomeAssistant, entry: CtcConfigEntry, prefix: str, moves: dict[str, str]
) -> None:
    """Carry each moved display row's entity over to the key of its place (roadmap L2).

    Only the unique_id changes. The entity id, and with it the history, the
    dashboards that point at it and whatever somebody set on the entity, stay
    as they are. An entry that already stands at the new key is left beside
    the old one rather than either being removed: that is a state no release
    leaves behind, and the owner is the one to say which goes. One line in
    the log either way, warning only for that.
    """
    if not moves:
        return
    from homeassistant.helpers import entity_registry as er

    registry = er.async_get(hass)
    moved: list[str] = []
    stuck: list[str] = []
    for item in er.async_entries_for_config_entry(registry, entry.entry_id):
        if item.domain != "sensor" or not item.unique_id.startswith(prefix):
            continue
        new = moves.get(item.unique_id[len(prefix):])
        if new is None:
            continue
        taken = registry.async_get_entity_id("sensor", DOMAIN, f"{prefix}{new}")
        if taken is not None:
            stuck.append(f"{item.entity_id} ({taken})")
            continue
        registry.async_update_entity(item.entity_id, new_unique_id=f"{prefix}{new}")
        moved.append(item.entity_id)
    if moved:
        _LOGGER.info(
            "%d display rows are known by their place on the page from now on rather than "
            "by their name; their entities keep their ids: %s",
            len(moved),
            ", ".join(sorted(moved)),
        )
    if stuck:
        _LOGGER.warning(
            "%d display rows could not be carried over to the key of their place on the page, "
            "because an entity already stands there; both are left as they are, so delete the "
            "one you do not want: %s",
            len(stuck),
            ", ".join(sorted(stuck)),
        )


def commissioning_date(runtime: CtcRuntime):
    """Work out when the lifetime counters started, from the powered-on hours.

    CTC counts the hours the unit has been switched on. Taken back from today
    they land on the day it was commissioned, which is also the day both energy
    counters stood at zero. The hours stop while the unit is off, so the answer
    can only come out late, never early, and the tracker keeps the earliest one.
    """
    from datetime import date, timedelta

    hours = powered_on_hours(runtime)
    if hours is None:
        return None
    return date.today() - timedelta(hours=hours)




@dataclass
class CtcRuntime:
    """Everything one config entry needs at runtime."""

    modbus: CtcModbusCoordinator
    control: CtcControlManager
    device: DeviceInfo
    #: The display's client, kept whether or not any page is harvested. Its
    #: panel lock is the one thing that keeps the harvest, the menu re-read,
    #: the walk to the system information page and a "read the menu again"
    #: from the options from walking the same physical panel at once.
    web_client: CtcWebClient
    web: CtcWebCoordinator | None = None
    #: Where the last harvest is written down for the next start. On the
    #: runtime so that a write of the options that reloads the entry can have
    #: it written out first; see _async_catch_up.
    harvest_memory: HarvestMemory | None = None
    pages: list[SlowPage] = field(default_factory=list)
    control_enabled: bool = False
    identity: Identity = field(default_factory=Identity)
    cop: CopTracker | None = None
    #: The two lifetime counters, once they turn up among the harvested pages.
    energy_out: Any | None = None
    energy_in: Any | None = None
    #: Consumed energy from Modbus, where the display has no counter for it.
    consumption_snapshot: ConsumptionSnapshot | None = None
    #: The immersion heater's counter, Modbus 62191, taken when the display's
    #: counters were read, for the samples and the share of auxiliary heat
    #: (roadmap R28). Wherever there is a tracker.
    immersion_snapshot: ImmersionSnapshot | None = None
    #: The display's two power rows, and the sums the integration makes of them
    #: where the machine's own counters stay at zero. See integrate.py.
    power_out: Any | None = None
    power_in: Any | None = None
    integrator: PowerIntegrator | None = None
    #: Candidate rows for the unit's powered-on hours; the largest is used.
    operating_hours: Any | None = None
    #: What this installation has ever given a value other than zero.
    seen: SeenValues | None = None
    #: The entry's data and options this set-up was built from. The reload
    #: listener compares against them: a write that changes nothing but the
    #: identity is applied in place, anything else takes a reload.
    applied_data: dict[str, Any] = field(default_factory=dict)
    applied_options: dict[str, Any] = field(default_factory=dict)
    #: The heat pump's starts, stops and other transitions, from the Modbus
    #: status codes, poll by poll. The sensors and the events read this.
    transitions: TransitionWatch | None = None
    #: The display's "Antal starter /24 h" row, where the history page is
    #: harvested, which the mean run over a day divides the minutes by.
    starts_per_day: Any | None = None
    #: The alarm the display shows and the last ten episodes of it, where the
    #: display is harvested at all.
    alarms: AlarmLog | None = None
    #: How the last walk through the menu in this run went, empty before the
    #: first: the entry's own dictionary in _MENU_OUTCOME, shared, not copied.
    menu_outcome: dict[str, Any] = field(default_factory=dict)
    #: How many entities this set-up switched on that an earlier version had
    #: created switched off (entity.py). Home Assistant reloads the entry
    #: half a minute after that, and _async_catch_up holds the panel until it
    #: has, rather than start a walk the reload would cut short.
    switched_on: int = 0

    async def async_record_cop(self, read_at: datetime | None = None) -> bool:
        """Write the two lifetime counters down as a sample, and say whether one was kept.

        The six hour timer calls this, and so does the cop_sample action
        (roadmap R76), so a sample after a change need not wait for the timer.
        A sample is the pair as it stood when the panel was read, and it is
        stamped with that moment, never with the clock: the timer fires
        whether or not the history page has been reached since, and a page
        that has stopped being reached (R5) leaves the pair standing in the
        data. Without a fresh reading of both counters there is no sample,
        and a moment the tracker has already kept is the same reading again,
        which it drops itself. ``read_at`` is handed in for the pair that
        came back out of the display store at start-up, with its own moment.
        """
        tracker = self.cop
        if tracker is None:
            return False
        out, consumed = current_totals(self)
        if read_at is None:
            read_at = counters_read_at(self)
        before = tracker.last_sample_at
        try:
            commissioned = commissioning_date(self)
            if commissioned is not None:
                await tracker.async_set_anchor(commissioned)
            if read_at is not None:
                await tracker.async_record(
                    out, consumed, now=read_at, immersion=immersion_at(self, read_at)
                )
        except Exception as err:  # a missed sample is not fatal
            _LOGGER.debug("Could not write down the energy counters: %s", err)
            return False
        return tracker.last_sample_at != before


type CtcConfigEntry = ConfigEntry[CtcRuntime]


async def async_setup_entry(hass: HomeAssistant, entry: CtcConfigEntry) -> bool:
    """Set up one heat pump."""
    host = entry.data[CONF_HOST]
    modbus_port = entry.data.get(CONF_MODBUS_PORT, DEFAULT_MODBUS_PORT)
    web_port = entry.data.get(CONF_WEB_PORT, DEFAULT_WEB_PORT)
    slave = entry.data.get(CONF_SLAVE, DEFAULT_SLAVE)
    options = entry.options

    await _async_arm_statistics(hass, entry)
    await _async_arm_debug_report(hass, entry)
    # The sidebar page, before the first Modbus call for the same reason as the
    # report: a controller that is away keeps the entry retrying, and the page
    # should say so rather than vanish from the sidebar.
    try:
        integration = await async_get_integration(hass, DOMAIN)
        await dashboard.async_register(hass, str(integration.version))
    except Exception:  # the page must never break a set-up
        _LOGGER.warning("Could not add the CTC page", exc_info=True)

    # pymodbus writes an error line of its own for every register nobody
    # answers, which on a model that lacks a block is twelve red lines per
    # start about something the round handles and says on debug. Kept out of
    # the log for as long as an entry is loaded, and only that one line.
    entry.async_on_unload(hold_library_quiet())

    modbus_client = CtcModbusClient(host, modbus_port, slave)
    modbus = CtcModbusCoordinator(
        hass,
        modbus_client,
        int(options.get(CONF_FAST_INTERVAL, DEFAULT_FAST_INTERVAL)),
        config_entry=entry,
    )
    try:
        await modbus.async_config_entry_first_refresh()
    except Exception as err:
        # The controller allows a single Modbus client. A failed attempt that
        # leaves its socket open holds that slot, so every retry then fails as
        # well and the entry can never recover on its own. Shut down rather
        # than closed: nothing of this attempt may connect again.
        await modbus_client.async_shutdown()
        # The page says the pump is being retried, and why, rather than that
        # nothing is running. Announced here, since the entry is in
        # setup_retry by the time an open page asks for its layout again.
        dashboard.async_announce_change(hass)
        # With this attempt's client shut, and only then, the cause is named.
        reason = await _async_name_the_failure(hass, entry, host, modbus_port, slave)
        if reason is not None:
            raise reason from err
        raise
    # The first reading that works: whatever held the Modbus place has let go.
    ir.async_delete_issue(hass, DOMAIN, f"{entry.entry_id}_{ISSUE_MODBUS_BUSY}")

    web_client = CtcWebClient(
        async_get_clientsession(hass),
        host,
        web_port,
        int(options.get(CONF_LANGUAGE, LANG_SWEDISH)),
    )
    # The entry's own panel lock in place of the client's, the same across
    # reloads, so a walk an earlier runtime still makes holds this runtime's
    # harvest and catch-up back (panel_lock).
    web_client.panel = panel_lock(entry.entry_id)

    # What the unit is, rather than what it is doing. Kept with the entry, so
    # the device has it from the first moment: the panel writes it into its own
    # screens and changes it only with a firmware update. It is read again in
    # the background, by the catch-up task below, so that set-up itself never
    # waits on the display.
    identity = Identity.from_dict(options.get(CONF_IDENTITY))

    model = entry.data.get("model", "CTC")
    # What the device and every entity are known by: the address the entry was
    # created with, kept when the address moves (keys.py, roadmap R20).
    key = device_key(entry.data)
    device = DeviceInfo(
        identifiers={(DOMAIN, key)},
        manufacturer="CTC / Enertech",
        model=f"{model} + {identity.heatpump_model}" if identity.heatpump_model else model,
        # The device name becomes the prefix of every entity id, so it stays
        # short. The entry title keeps the address for telling two units apart.
        name=f"CTC {model}",
        serial_number=identity.serial,
        sw_version=identity.display_firmware,
        hw_version=identity.bootloader,
        # No link to a web interface that did not answer at set-up (R11).
        configuration_url=web_interface_url(host, web_port) if has_display(entry.data) else None,
    )
    if (mac := mac_address(identity.mac)) is not None:
        # The display's MAC on the device card, and what the DHCP flow
        # recognises the unit by when it turns up at another address.
        device["connections"] = {(dr.CONNECTION_NETWORK_MAC, mac)}

    runtime = CtcRuntime(
        modbus=modbus,
        control=CtcControlManager(hass, modbus_client),
        device=device,
        web_client=web_client,
        control_enabled=bool(options.get(CONF_ENABLE_CONTROL, True)),
        identity=identity,
        menu_outcome=_MENU_OUTCOME.setdefault(entry.entry_id, {}),
        # From the entry, not from the local copy above: the identity read a
        # moment ago may already have been written to the options.
        applied_data=dict(entry.data),
        applied_options=dict(entry.options),
    )

    # On the entry before the panel is first touched, not after. Home Assistant
    # takes runtime_data away when an entry is unloaded, and an options dialog
    # that was open across the reload can send "read the menu again" while the
    # first harvest below is still walking: the options flow borrows this
    # runtime's web client, and with it the one panel lock the harvest holds,
    # only if the runtime is already here. Without it the flow would build a
    # client of its own, see a free lock and walk the same panel at once.
    # Everything that reads the runtime before set-up is done, the daily
    # report and the dashboard among them, copes with web being None.
    entry.runtime_data = runtime

    # The watch over the status codes, fed from every successful round. The
    # first round, the one set-up just did, is the baseline: whatever state
    # the pump is found in is not a change. Listening before the platforms are
    # set up means the sensors and the events that listen to the same
    # coordinator read a watch that has already seen the round. A failed round
    # is no sample: the codes it leaves behind are the last successful one's.
    watch = TransitionWatch()

    def _observe() -> None:
        if modbus.last_update_success:
            watch.observe(sample_of(modbus, dt_util.now()))

    _observe()
    entry.async_on_unload(modbus.async_add_listener(_observe))
    runtime.transitions = watch

    # The display rows that have moved to the key of their place on the page
    # (L2): their entities are carried over here, before the platforms make
    # them, and their stored values below as each store is loaded. A row says
    # where it came from for as long as it stays in the menu, so this is done
    # at every set-up and is a no-op once done.
    moves = previous_keys(
        pages_from_storage(options.get(CONF_MENU))
        + pages_from_storage(options.get(CONF_SLOW_PAGES))
    )
    _async_move_row_keys(hass, entry, unique_prefix(key), moves)

    pages = pages_from_storage(options.get(CONF_SLOW_PAGES, []))
    if pages:
        # The last harvest before the restart, values and moments alike. The
        # coordinator comes up with it, so the sensors show what was read
        # before, as old as it is, and the first harvest is owed one interval
        # after the last one: a restart moves the panel not at all. Nothing is
        # harvested in set-up, not even without a store; then the first harvest
        # follows a few seconds after the platforms are up.
        memory = HarvestMemory(
            Store(hass, HARVEST_STORAGE_VERSION, f"{DOMAIN}_{entry.entry_id}_display")
        )
        stored = await memory.async_load()
        if stored is not None and moves:
            renamed = stored.renamed(moves)
            if renamed.as_dict() != stored.as_dict():
                stored = renamed
                await memory.async_save(stored)
        runtime.harvest_memory = memory
        # The operation data root, which every route starts from: between two
        # pages the harvester steps back to it rather than going home for
        # each. From the menu where it was stored with it, else from the page
        # with the empty route, else learnt the first time the tile is pressed.
        stored_root = options.get(CONF_MENU_ROOT)
        web_client.root = (
            int(stored_root)
            if isinstance(stored_root, int) and not isinstance(stored_root, bool)
            else menu_root(pages_from_storage(options.get(CONF_MENU)) or pages)
        )
        web = CtcWebCoordinator(
            hass,
            web_client,
            pages,
            int(options.get(CONF_SLOW_INTERVAL, DEFAULT_SLOW_INTERVAL)),
            restore_page=bool(options.get(CONF_RESTORE_PAGE, True)),
            stored=stored,
            config_entry=entry,
        )
        runtime.web = web
        runtime.pages = pages
        runtime.energy_out, runtime.energy_in = find_energy_totals(pages)
        runtime.operating_hours = find_operating_hours(pages)
        runtime.power_out, runtime.power_in = find_power_pair(pages)
        if runtime.power_out is not None and runtime.power_in is not None:
            # Energy of our own, for the installations whose counters never move,
            # which follows the outdoor unit: a 400 or 600 series leaves both
            # lifetime counters at zero where the 700 series fills them. Both
            # rows sit on the same page, so one read time covers the pair, and a
            # pair read in the same instant is what makes the quotient worth
            # having even though each total is rough. See integrate.py.
            integrator = PowerIntegrator(
                Store(hass, INTEGRATE_STORAGE_VERSION, f"{DOMAIN}_{entry.entry_id}_integrated")
            )
            await integrator.async_load()
            power_out_key = runtime.power_out.key
            power_in_key = runtime.power_in.key

            def _integrate() -> None:
                data = web.data or {}
                if integrator.note(
                    web.last_read(power_out_key),
                    data.get(power_out_key),
                    data.get(power_in_key),
                ):
                    integrator.save_soon()

            _integrate()
            entry.async_on_unload(web.async_add_listener(_integrate))
            runtime.integrator = integrator
        runtime.starts_per_day = find_starts_per_day(pages)
        if runtime.energy_out is not None and runtime.energy_in is None:
            # The older display software, as on an i360, counts delivered heat
            # but not consumed energy. Modbus 62341 holds that number, and is
            # taken at the moment the display is read so the two stay a pair.
            # Nothing about the register is settled here: whether a counter at
            # zero is new or stuck is judged at every read by counter_fault,
            # and whether the register has answered at all is read live off
            # the coordinator, by the report and by the sensors' reason. The
            # first refresh is one poll, and a block that was silent in it, or
            # a pair that read CTC's marker, used to cost the sensors for good,
            # until somebody reloaded the entry. Registered before the
            # platforms, so the sensors that listen to the same coordinator
            # see the new pairing when they update. The pair written down
            # before the restart comes back as a pair, see the store above.
            snapshot = ConsumptionSnapshot()
            heat_key = runtime.energy_out.key
            if stored is not None:
                snapshot.seed(web.last_read(heat_key), stored.consumption)

            def _take_consumption() -> None:
                snapshot.update(web.last_read(heat_key), modbus.data)

            _take_consumption()
            entry.async_on_unload(web.async_add_listener(_take_consumption))
            runtime.consumption_snapshot = snapshot
        if runtime.energy_out is not None and (
            runtime.energy_in is not None or runtime.consumption_snapshot is not None
        ):
            # Minor 2 since the immersion heater's counter came into the
            # samples (R28); the major stays at 1, see SeenStore.
            runtime.cop = CopTracker(
                Store(hass, 1, f"{DOMAIN}_{entry.entry_id}_cop", minor_version=COP_MINOR_VERSION)
            )
            await runtime.cop.async_load()
            # The immersion heater's counter, taken when delivered heat is
            # read, as the consumption above, so a sample and the share of
            # auxiliary heat set it against the compressor's counter of the
            # same moment. Written down with the harvest and brought back
            # here as the pair of the moment the display store comes back
            # with, so the sample taken at start-up keeps it; a fresh Modbus
            # reading is never paired with an old display one. A store
            # written before it was kept has none, and that sample has no
            # immersion heater, which the share passes over.
            immersion = ImmersionSnapshot()
            immersion_key = runtime.energy_out.key
            immersion.seed(
                web.last_read(immersion_key), stored.immersion if stored is not None else None
            )

            def _take_immersion() -> None:
                immersion.update(
                    web.last_read(immersion_key),
                    modbus.data if modbus.last_update_success else None,
                )

            _take_immersion()
            entry.async_on_unload(web.async_add_listener(_take_immersion))
            runtime.immersion_snapshot = immersion
        # The alarm the panel shows, read off the same values as the rows. The
        # harvest notes it page by page; this concludes once per round, after
        # the listeners of the round have what they need, with the outdoor
        # temperature Modbus has at that moment. A round that read no page is
        # no information and leaves the log alone; a round that read fewer
        # pages than all that can print an alarm may show one but cannot close
        # an episode. A seen alarm is held for the harvest interval, see
        # alarms.confirm_window.
        alarms = AlarmLog(
            Store(hass, ALARM_STORAGE_VERSION, f"{DOMAIN}_{entry.entry_id}_alarms"),
            confirm_within=confirm_window(web.interval),
        )
        await alarms.async_load()
        runtime.alarms = alarms

        def _conclude_alarms() -> None:
            seen = web.alarms.fresh()
            if seen.read:
                alarms.note(
                    seen.shown,
                    datetime.now(UTC),
                    (modbus.data or {}).get("outdoor_temp"),
                    complete=seen.complete,
                )

        _conclude_alarms()
        entry.async_on_unload(web.async_add_listener(_conclude_alarms))

        def _remember() -> None:
            # After every refresh, written only when a harvest actually ran;
            # after the consumption and immersion heater listeners, so each
            # pair goes in together.
            paired = runtime.consumption_snapshot
            heater = runtime.immersion_snapshot
            memory.remember(
                web.data,
                web.read_at,
                web.last_harvest,
                paired.value if paired is not None else None,
                heater.value if heater is not None else None,
            )

        entry.async_on_unload(web.async_add_listener(_remember))

    # What this installation actually has, learnt from what it reports: CTC
    # answers with a clean zero for hardware and registers it does not use.
    seen = SeenValues(
        seen_store(hass, entry.entry_id),
        on_new=lambda: dashboard.async_announce_change(hass),
    )
    await seen.async_load()
    if moves and seen.rename(moves):
        # Written now, so the record and the registry agree from this start on.
        await seen.async_save()
    for coordinator in (modbus, runtime.web):
        if coordinator is None:
            continue
        seen.note(coordinator.data)
        entry.async_on_unload(
            coordinator.async_add_listener(
                lambda coordinator=coordinator: seen.note(coordinator.data)
            )
        )
    runtime.seen = seen
    if seen.fresh:
        # Once, with the recorder surely up: what the statistics already show.
        from homeassistant.helpers.start import async_at_started

        async def _seed(_hass: HomeAssistant) -> None:
            await async_seed_from_statistics(hass, entry.entry_id, unique_prefix(key), seen)

        entry.async_on_unload(async_at_started(hass, _seed))

    try:
        await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    except Exception:
        await modbus_client.async_shutdown()
        raise
    entry.async_on_unload(entry.add_update_listener(_async_reload))

    _async_review_issues(hass, entry, runtime)

    integration = await async_get_integration(hass, DOMAIN)
    async def _release_tick(_now=None) -> None:
        await _async_check_release(hass, entry, str(integration.version))

    entry.async_on_unload(
        async_track_time_interval(hass, _release_tick, UPDATE_CHECK_INTERVAL)
    )
    entry.async_create_background_task(
        hass,
        _async_catch_up(hass, entry, runtime, web_client, str(integration.version)),
        f"{DOMAIN} catch up",
    )

    if runtime.cop is not None:
        async def _record_cop(_now=None) -> None:
            # The pair as it stood when the panel was last read, and its
            # moment; see CtcRuntime.async_record_cop.
            await runtime.async_record_cop()

        # The counters as they came back from the store, stamped with the
        # moment they were read rather than with now: a restart is not a
        # reading, and a sample dated today with yesterday's counters would
        # pass for yesterday's in the daily figure. Handed in as they are,
        # however old: a pair read before the restart is a reading at its own
        # moment, and the tracker keeps a moment once.
        await runtime.async_record_cop(read_at=runtime.web.last_read(runtime.energy_out.key))  # type: ignore[union-attr]
        entry.async_on_unload(
            async_track_time_interval(hass, _record_cop, COP_SAMPLE_INTERVAL)
        )

    dashboard.async_announce_change(hass)
    return True


def _takes_a_reload(
    entry: CtcConfigEntry, runtime: CtcRuntime, options: dict[str, Any]
) -> bool:
    """Whether a write of these options is one the reload listener acts on.

    One rule, for the listener and for the task that writes from the inside
    and has to know whether its write ends it. The entry's data and every
    option but the identity, the screens the identity was found on and the
    debug report are somebody's choice and take a reload to come into force.
    The first two are written from the inside, by _async_adopt_identity and
    _async_catch_up, and change nothing about what is polled. The debug report
    is the owner's choice, and its reporter follows it by itself (debug_report.py):
    a reload for it would close Modbus, and a set-up that meets the controller's
    one place still taken waits for it. So a write that differs from what this
    set-up was built from in nothing else is left alone; the debug report is
    taken out of both sides before the identity's rule compares them.
    """
    if dict(entry.data) != runtime.applied_data:
        return True
    return not only_identity_differs(
        without_the_debug_report(runtime.applied_options), without_the_debug_report(options)
    )


async def _async_reload(hass: HomeAssistant, entry: CtcConfigEntry) -> None:
    """Reload on a change of the entry, unless only the identity or the debug report changed.

    Home Assistant calls this for every write of the entry. A write that
    changes nothing but CONF_IDENTITY comes from _async_adopt_identity, which
    has already told everything that reads the identity, and a reload would
    only cost what is listed there; one that changes nothing but the screens
    comes from _async_catch_up, which carries on by itself; one that changes
    nothing but the debug report comes from its switch or from Configure, and
    its reporter, a listener of the entry's as well, applies it in place. A
    change of the host or of any other option is somebody's choice and takes
    a reload to come into force. The rule is _takes_a_reload.
    """
    runtime = getattr(entry, "runtime_data", None)
    if runtime is not None and not _takes_a_reload(entry, runtime, entry.options):
        _LOGGER.debug(
            "Only the identity or the debug report was written to the entry; nothing to "
            "reload for"
        )
        return
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: CtcConfigEntry) -> bool:
    """Tear one heat pump down, releasing the single Modbus slot."""
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        runtime = entry.runtime_data
        # Only here, never from an on-unload callback: those also run when a
        # set-up attempt fails, and the report has to survive that. The debug
        # report the same way; the lines it has not sent wait for the next
        # set-up.
        await async_stop_stats(hass, entry, DOMAIN)
        async_stop_debug_report(hass, entry)
        # The keepalive first, so that no write of this entry's is queued
        # behind the shutdown; then the client, for good. A reload builds the
        # next client at once, and a connection the old entry opened after
        # this point would hold the controller's single slot against it.
        await runtime.control.async_stop()
        await runtime.modbus.client.async_shutdown()
        dashboard.async_announce_change(hass)
    return unloaded


async def async_remove_entry(hass: HomeAssistant, entry: CtcConfigEntry) -> None:
    """Take the sidebar page away with the last heat pump.

    Not on unload: a reload unloads the entry too, and removing the panel then
    would throw anyone looking at the page back to the start page.
    """
    others = [
        other
        for other in hass.config_entries.async_entries(DOMAIN)
        if other.entry_id != entry.entry_id
    ]
    if not others:
        dashboard.async_unregister(hass)
    # Every notice of the entry's, since Home Assistant takes none of an
    # integration's issues away with the entry: one left would stand in the
    # repairs view for an entry that no longer exists, beside its twin if the
    # unit is added again, until the next restart.
    for key in _ENTRY_ISSUES:
        ir.async_delete_issue(hass, DOMAIN, f"{entry.entry_id}_{key}")
    # No set-up of this entry will borrow its panel lock again.
    _PANEL_LOCKS.pop(entry.entry_id, None)
    # The debug report goes with the entry, and so do the lines it had not
    # sent. Its reporter is still armed where the entry waited for another
    # set-up attempt, which never reaches async_unload_entry.
    async_stop_debug_report(hass, entry, keep_lines=False)
    # What the unit was seen to have, what its display last gave, the energy
    # counters behind the coefficient of performance and what it alarmed
    # about belong to this entry alone, and go with it.
    await Store(hass, SEEN_STORAGE_VERSION, f"{DOMAIN}_{entry.entry_id}_seen").async_remove()
    await Store(
        hass, HARVEST_STORAGE_VERSION, f"{DOMAIN}_{entry.entry_id}_display"
    ).async_remove()
    await Store(hass, 1, f"{DOMAIN}_{entry.entry_id}_cop").async_remove()
    await Store(hass, ALARM_STORAGE_VERSION, f"{DOMAIN}_{entry.entry_id}_alarms").async_remove()
