"""Config and options flow for CTC Local Easyconf.

Setup has two questions. First where the unit is: the user chooses between
searching the local network and typing an address, and the search runs only once
it has been chosen. Then which of the display's own pages should be harvested
for the values Modbus does not carry, offered as a list of tick boxes built from
the unit's own menu.
"""

from __future__ import annotations

import logging
from typing import Any

from homeassistant import config_entries
from homeassistant.config_entries import ConfigFlowResult
from homeassistant.const import CONF_HOST
from homeassistant.core import callback
from homeassistant.helpers import selector
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.device_registry import format_mac
from homeassistant.helpers.service_info.dhcp import DhcpServiceInfo
import voluptuous as vol

from . import page_search
from .catalogue import (
    MenuReading,
    PanelBusy,
    async_discover_pages,
    async_rescan_pages,
    menu_after_rescan,
    menu_root,
    pages_from_storage,
    pages_to_storage,
)
from .const import (
    CONF_CHECK_UPDATES,
    CONF_DEBUG_REPORT,
    CONF_DEVICE_KEY,
    CONF_DISPLAY,
    CONF_ENABLE_CONTROL,
    CONF_FAST_INTERVAL,
    CONF_IDENTITY,
    CONF_LANGUAGE,
    CONF_MENU,
    CONF_MENU_ROOT,
    CONF_MENU_VERSION,
    CONF_MODBUS_PORT,
    CONF_RESTORE_PAGE,
    CONF_SEND_STATISTICS,
    CONF_SETTINGS_STEM,
    CONF_SLAVE,
    CONF_SLOW_INTERVAL,
    CONF_SLOW_PAGES,
    CONF_VISIT_SYSTEM_INFO,
    CONF_WEB_PORT,
    DEFAULT_FAST_INTERVAL,
    DEFAULT_MODBUS_PORT,
    DEFAULT_SLAVE,
    DEFAULT_SLOW_INTERVAL,
    DEFAULT_WEB_PORT,
    DOMAIN,
    LANG_SWEDISH,
    MIN_SLOW_INTERVAL,
    has_display,
)
from .debug_report import debug_report_on, only_the_debug_report_changes
from .discovery import (
    FAMILY,
    DiscoveredDisplay,
    async_discover,
    async_home_assistant_networks,
    async_probe_host,
    async_probe_web,
    settings_stem,
)
from .keys import (
    KNOWN,
    MOVED,
    NEW,
    Known,
    device_key,
    free_key,
    held_back,
    keep_hidden_rows,
    match_discovery,
    moved_data,
    moved_title,
    union_by_page,
    with_previous_keys,
)
from .modbus_api import CtcModbusClient, CtcModbusError, note_moved
from .modbus_probe import ANSWERED, BUSY, REJECTED, SILENT, async_classify
from .page_search import MAX_DEPTH, MAX_TAPS, PageSearch, async_search_page_locked
from .web_api import CtcWebClient, CtcWebError

_LOGGER = logging.getLogger(__name__)

# hassfest rejects URLs in strings.json, so the addresses travel as
# placeholders instead.
STATS_PLACEHOLDERS = {
    "endpoint": "stats.rnet.se",
    "endpoint_url": "https://stats.rnet.se",
    "privacy_url": "https://stats.rnet.se/integritet",
}

#: Where the README says what port 80 and 502 open on the network and how to
#: keep them for Home Assistant (roadmap R71). The first step and the
#: confirmation of a discovered unit say it in one sentence and link here.
EXPOSURE_PLACEHOLDERS = {
    "exposure_url": "https://github.com/beolink/HA-CTC-Local-Easyconf#network-and-exposure",
}

#: What the first form of Configure offers for a setting the options do not
#: hold yet, which is also what the integration does without it. An entry
#: that was never saved there lacks most of them, and the form writes every
#: field it has: a setting written with this value is no change, so a save
#: that changes nothing else in effect writes the debug report alone and
#: reloads nothing (_async_save).
FIRST_FORM_DEFAULTS: dict[str, Any] = {
    CONF_FAST_INTERVAL: DEFAULT_FAST_INTERVAL,
    CONF_SLOW_INTERVAL: DEFAULT_SLOW_INTERVAL,
    CONF_RESTORE_PAGE: True,
    CONF_ENABLE_CONTROL: True,
    CONF_VISIT_SYSTEM_INFO: True,
    CONF_CHECK_UPDATES: True,
    CONF_SEND_STATISTICS: True,
    CONF_DEBUG_REPORT: False,
}

CONF_PICKED = "picked"
MANUAL = "manual"

#: The two ways in, offered as a menu before anything goes on the network
#: (roadmap R70). Each is the id of the step it leads to.
STEP_SCAN = "scan"
STEP_MANUAL = "manual"
#: The display answered and Modbus did not (roadmap R16).
STEP_MODBUS_FAILED = "modbus_failed"
#: Modbus accepted the connection and dropped it at the first request: another
#: client holds the controller's one place (roadmap L12).
STEP_MODBUS_BUSY = "modbus_busy"
#: The options' search for the way to one page (roadmap L7): the tick box on
#: the first form, the step that asks for the page and searches, the field
#: the page is given in, and the step that shows what was found.
CONF_FIND_PAGE = "find_page"
STEP_FIND_PAGE = "find_page"
CONF_PAGE = "page"
STEP_FIND_PAGE_FOUND = "find_page_found"
#: What the form says for a search that ended without the page, by why.
SEARCH_ERRORS = {
    page_search.UNKNOWN: "page_unknown",
    page_search.OUTSIDE: "page_outside",
    page_search.NO_MENU: "no_menu",
    page_search.NOT_FOUND: "page_not_found",
    page_search.LOST: "page_lost",
    page_search.EMPTY: "page_empty",
    page_search.GAP: "page_gap",
    page_search.SILENT: "display_silent",
}


class CtcConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Guide the user from an empty form to a working entry."""

    VERSION = 1

    def __init__(self) -> None:
        self._host: str | None = None
        #: The key the new entry's device and entities are known by, its
        #: address unless a moved entry still has that (keys.free_key).
        self._key: str | None = None
        self._modbus_port = DEFAULT_MODBUS_PORT
        self._web_port = DEFAULT_WEB_PORT
        self._slave = DEFAULT_SLAVE
        self._model: str = "CTC"
        self._settings_name: str = ""
        self._found: list[DiscoveredDisplay] = []
        self._pages: list[Any] = []
        #: Whether ``_pages`` is the whole menu, read from a verified root with
        #: nothing given up on the way. Only then is the version stamped.
        self._complete = False
        #: The operation data root the reading found, kept with the menu so
        #: the harvester can step back to it between pages.
        self._root: int | None = None
        #: The form the address was last typed into, None when it was picked
        #: from the list or discovered. A Modbus failure after a try from the
        #: Modbus form says so, rather than showing the same form unchanged.
        self._origin: str | None = None
        #: False once a typed address turned out to have no web interface
        #: answering, and the entry is made on Modbus alone (roadmap R11).
        self._display = True
        #: Set when "try again" is pressed on the step that says the Modbus
        #: place is taken, so a second refusal says it is still taken.
        self._busy_retry = False

    # ------------------------------------------------------------ entry point

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask how the unit is to be found, before anything goes on the network.

        Searching asks every address of Home Assistant's own private networks
        for the display's settings file on port 80. On a shared network that is a port
        scan, so it runs only once somebody has chosen it; typing the address
        in never sweeps at all. The menu's text also says what is switched on
        from the start, since a new installation never sees the options form
        that explains it, and that the two ports it needs have no login.
        """
        return self.async_show_menu(
            step_id="user",
            menu_options=[STEP_SCAN, STEP_MANUAL],
            description_placeholders={**STATS_PLACEHOLDERS, **EXPOSURE_PLACEHOLDERS},
        )

    async def async_step_scan(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Sweep the network, then let the user pick or type an address.

        An address that already has an entry is left out of the list: picking
        it could only end in "already set up", and two units side by side are
        told apart more easily without it.
        """
        if user_input is not None:
            picked = user_input[CONF_PICKED]
            if picked == MANUAL:
                return await self.async_step_manual()
            self._host = picked
            self._origin = None
            self._display = True
            for display in self._found:
                if display.host == picked:
                    self._model = display.model
                    self._settings_name = display.settings_name
            return await self.async_step_connect()

        session = async_get_clientsession(self.hass)
        try:
            # Home Assistant's own adapters and nothing else: without one,
            # there is nothing to sweep and the address form follows at once.
            networks = await async_home_assistant_networks(self.hass)
            found = await async_discover(session, networks) if networks else []
        except Exception as err:  # a failed sweep must not block setup
            _LOGGER.debug("Network sweep failed: %s", err)
            found = []
        configured = self._configured_hosts()
        self._found = [display for display in found if display.host not in configured]

        if not self._found:
            # A sweep that found only units already set up did find something,
            # and saying it found no display sends the owner of a second pump
            # looking for a fault in the search.
            error = "only_configured" if found else "nothing_found"
            return await self.async_step_manual(errors={"base": error})

        options = [
            selector.SelectOptionDict(value=display.host, label=display.label)
            for display in self._found
        ]
        # Labelled from strings.json through the selector's translation key;
        # the label here is only what shows where the translations are missing.
        options.append(selector.SelectOptionDict(value=MANUAL, label="Enter an address"))
        schema = vol.Schema(
            {
                vol.Required(CONF_PICKED, default=self._found[0].host): selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=options,
                        mode=selector.SelectSelectorMode.LIST,
                        translation_key=CONF_PICKED,
                    )
                )
            }
        )
        return self.async_show_form(
            step_id="scan",
            data_schema=schema,
            description_placeholders={"count": str(len(self._found))},
        )

    def _free_key(self, host: str) -> str:
        """The key a new entry at ``host`` is to be known by (keys.free_key).

        Taken is every key an entry is known by, which for an entry that has
        moved is the address it was created with, and the key in every
        entry's unique_id. An ignored discovery takes nothing: adding its
        unit by hand replaces it, as Home Assistant has it.
        """
        taken: set[str] = set()
        prefix = f"{DOMAIN}_"
        for entry in self._async_current_entries(include_ignore=False):
            if entry.data.get(CONF_DEVICE_KEY) or entry.data.get(CONF_HOST):
                taken.add(device_key(entry.data))
            if entry.unique_id and entry.unique_id.startswith(prefix):
                taken.add(entry.unique_id[len(prefix):])
        return free_key(host, taken)

    def _configured_hosts(self) -> set[str]:
        """The addresses that already have an entry, ignored discoveries aside."""
        return {
            str(entry.data.get(CONF_HOST))
            for entry in self._async_current_entries(include_ignore=False)
            if entry.data.get(CONF_HOST)
        }

    async def async_step_manual(
        self,
        user_input: dict[str, Any] | None = None,
        errors: dict[str, str] | None = None,
    ) -> ConfigFlowResult:
        """Ask for the address by hand: chosen, picked from the list, or after an empty sweep."""
        if user_input is not None:
            return await self._async_address_given(user_input, STEP_MANUAL)
        return self._address_form(STEP_MANUAL, errors)

    async def async_step_modbus_failed(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """The display answered and Modbus did not: what to do at the panel, and the form again.

        The most common first-time failure. It used to land on the address form,
        whose text began with "no CTC was found automatically" right after one
        had been; this step names the menu on the panel instead, and takes the
        same fields, so a corrected address or port goes through as typed.
        """
        if user_input is not None:
            return await self._async_address_given(user_input, STEP_MODBUS_FAILED)
        return self._address_form(STEP_MODBUS_FAILED, None)

    def _address_form(self, step_id: str, errors: dict[str, str] | None) -> ConfigFlowResult:
        """The address and its ports, filled in with what is known so far."""
        schema = vol.Schema(
            {
                vol.Required(CONF_HOST, default=self._host or ""): str,
                vol.Required(CONF_MODBUS_PORT, default=self._modbus_port): int,
                vol.Required(CONF_WEB_PORT, default=self._web_port): int,
                vol.Required(CONF_SLAVE, default=self._slave): int,
            }
        )
        return self.async_show_form(
            step_id=step_id,
            data_schema=schema,
            errors=dict(errors or {}),
            description_placeholders={"host": self._host or ""},
        )

    async def _async_address_given(
        self, user_input: dict[str, Any], step_id: str
    ) -> ConfigFlowResult:
        """Check a typed address: the display first, then Modbus in async_step_connect.

        A display that does not answer at all no longer stops the set-up. Web
        switched off at the panel, an older display, or a firewall that lets
        502 through and nothing else used to end here as "not a CTC", though
        the runtime does well without the web. Only here, where somebody typed
        the address: Modbus is tried on its own, and an entry is made on Modbus
        alone if it answers (roadmap R11). Something answering on the web port
        that is not a CTC display still stops it, since that is some other
        device.
        """
        self._origin = step_id
        self._host = user_input[CONF_HOST].strip()
        self._modbus_port = user_input[CONF_MODBUS_PORT]
        self._web_port = user_input[CONF_WEB_PORT]
        self._slave = user_input[CONF_SLAVE]
        session = async_get_clientsession(self.hass)
        probe = await async_probe_web(session, self._host, self._web_port)
        if probe.display is not None:
            self._display = True
            self._model = probe.display.model
            self._settings_name = probe.display.settings_name
        elif probe.answered:
            # On the address form whichever form it was typed into: the step
            # modbus_failed says the display at the address answers.
            return self._address_form(STEP_MANUAL, {"base": "not_a_ctc"})
        else:
            self._display = False
            self._model = FAMILY
            self._settings_name = ""
        return await self.async_step_connect()

    # ------------------------------------------------------------- validation

    async def async_step_connect(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Check that Modbus answers before going any further."""
        assert self._host is not None
        # An entry that has moved here keeps the unique_id of the address it
        # was created with, so the address itself is asked about as well. One
        # that has moved away keeps this address's key, and the unit that
        # took the address up is given a key of its own rather than being
        # told it is set up already.
        self._async_abort_entries_match({CONF_HOST: self._host})
        self._key = self._free_key(self._host)
        await self.async_set_unique_id(f"{DOMAIN}_{self._key}")
        self._abort_if_unique_id_configured()

        client = CtcModbusClient(self._host, self._modbus_port, self._slave)
        failed = False
        try:
            await client.async_probe()
        except CtcModbusError as err:
            _LOGGER.debug("Modbus probe failed: %s", err)
            failed = True
        finally:
            await client.async_close()
        if failed:
            # Only now, with the flow's own client closed: the probe is a
            # second client and must never meet a session that works.
            return await self._async_modbus_failed()

        if not self._display:
            return self._create_without_display()
        return await self.async_step_slow()

    async def _async_modbus_failed(self) -> ConfigFlowResult:
        """Name what kept Modbus away, and show the step that fits (roadmap L12).

        pymodbus says the same thing whether the port refused, another client
        holds the controller's one place, or the line hiccupped, so a raw probe
        asks once more and tells them apart; see modbus_probe. It waits out the
        controller's settle time after the flow's own client first, so this
        costs some ten seconds, on the failing path only.
        """
        assert self._host is not None
        retried_busy, self._busy_retry = self._busy_retry, False
        try:
            verdict = await async_classify(self._host, self._modbus_port, self._slave)
        except Exception:  # naming the failure must never break the flow
            _LOGGER.debug("Could not probe the Modbus port", exc_info=True)
            verdict = SILENT
        if verdict == BUSY:
            return self._busy_form({"base": "modbus_busy"} if retried_busy else None)
        if verdict == ANSWERED:
            # It answers now: a passing hiccup, and the form to try again.
            return self._address_form(self._retry_step(), {"base": "modbus_transient"})
        if verdict == REJECTED:
            # Something speaks on the port, and not as the heat pump: a wrong
            # port or Modbus address, which another try would only repeat.
            return self._address_form(STEP_MANUAL, {"base": "modbus_rejected"})
        if not self._display:
            # Neither the web port nor Modbus answered: no CTC at that
            # address, as far as can be told, which the text explains. On the
            # address form, never on the step that says the display answers.
            return self._address_form(STEP_MANUAL, {"base": "not_a_ctc"})
        # Tried from this very form before: say that it failed again, or the
        # same form coming back looks as if nothing had happened.
        again = self._origin == STEP_MODBUS_FAILED
        return self._address_form(STEP_MODBUS_FAILED, {"base": "modbus_failed"} if again else None)

    def _retry_step(self) -> str:
        """The form a try goes back to: the one it was typed into, while its text holds.

        The step modbus_failed says that the display at the address answers, so
        a try from it goes back there only when the display at the address it
        took did answer; anything else goes back to the address form.
        """
        if self._origin == STEP_MODBUS_FAILED and self._display:
            return STEP_MODBUS_FAILED
        return STEP_MANUAL

    async def async_step_modbus_busy(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Another client holds the controller's one Modbus place: say so, and try again.

        The address is right, since something accepted the connection there,
        so there is nothing to type: the step explains what usually holds the
        place, and submitting it tries Modbus again.
        """
        if user_input is not None:
            self._busy_retry = True
            return await self.async_step_connect()
        return self._busy_form(None)

    def _busy_form(self, errors: dict[str, str] | None) -> ConfigFlowResult:
        return self.async_show_form(
            step_id=STEP_MODBUS_BUSY,
            errors=dict(errors or {}),
            description_placeholders={"host": self._host or ""},
        )

    def _entry_data(self) -> dict[str, Any]:
        """What the entry keeps about the unit: where it is and what it said it was.

        The settings file's stem is kept on its own (roadmap R19), so the daily
        report can say which family turned up without parsing the file name;
        an entry on Modbus alone has no settings file and no stem.

        The key its device and entities are known by is the address the entry
        is created with, the same value every entry before it was known by, and
        it stays when the DHCP flow later moves the address (keys.py, R20).
        Only where a moved entry still has that address's key is it another
        one, chosen in async_step_connect.
        """
        data: dict[str, Any] = {
            CONF_HOST: self._host,
            CONF_DEVICE_KEY: self._key or self._host,
            CONF_MODBUS_PORT: self._modbus_port,
            CONF_WEB_PORT: self._web_port,
            CONF_SLAVE: self._slave,
            "model": self._model,
            "settings_name": self._settings_name,
            CONF_DISPLAY: self._display,
        }
        if self._settings_name:
            data[CONF_SETTINGS_STEM] = settings_stem(self._settings_name)
        return data

    def _create_without_display(self) -> ConfigFlowResult:
        """The entry for a heat pump on Modbus alone (roadmap R11).

        No pages and no menu, and the model "EcoZenith", since the display that
        would name it did not answer. Nothing that needs the display runs for
        it: no reading of the identity, no reading of the menu, no walk, and
        the repairs view says nothing about pages or the serial number. A
        "read the menu again" under Configure that finds pages makes it a
        display entry; see CtcOptionsFlow.async_step_rescan.
        """
        return self.async_create_entry(
            title=f"{self._model} ({self._host})",
            data=self._entry_data(),
            options={
                CONF_SLOW_PAGES: [],
                CONF_SLOW_INTERVAL: DEFAULT_SLOW_INTERVAL,
                CONF_FAST_INTERVAL: DEFAULT_FAST_INTERVAL,
                CONF_RESTORE_PAGE: True,
                CONF_ENABLE_CONTROL: True,
                CONF_LANGUAGE: LANG_SWEDISH,
            },
        )

    # ------------------------------------------------------------ slow values

    async def async_step_slow(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Offer the display's own pages as tick boxes."""
        assert self._host is not None

        if user_input is not None:
            chosen = {int(page) for page in user_input.get(CONF_SLOW_PAGES, [])}
            keep = [page for page in self._pages if page.page in chosen]
            options = {
                CONF_SLOW_PAGES: pages_to_storage(keep),
                CONF_MENU: pages_to_storage(self._pages),
                CONF_SLOW_INTERVAL: int(
                    user_input.get(CONF_SLOW_INTERVAL, DEFAULT_SLOW_INTERVAL)
                ),
                CONF_FAST_INTERVAL: DEFAULT_FAST_INTERVAL,
                CONF_RESTORE_PAGE: user_input.get(CONF_RESTORE_PAGE, True),
                CONF_ENABLE_CONTROL: True,
                CONF_LANGUAGE: LANG_SWEDISH,
            }
            if self._complete:
                # Only the whole menu is stamped. A reading without the
                # operation data root is the one page the panel stood on, and
                # an interrupted sweep is missing what it did not reach; left
                # unstamped, the entry is owed a reading, and the background
                # reads the menu again a few minutes in, up to three times.
                options[CONF_MENU_VERSION] = await _async_version(self.hass)
            if self._root is not None:
                options[CONF_MENU_ROOT] = self._root
            return self.async_create_entry(
                title=f"{self._model} ({self._host})",
                data=self._entry_data(),
                options=options,
            )

        session = async_get_clientsession(self.hass)
        client = CtcWebClient(session, self._host, self._web_port, LANG_SWEDISH)
        try:
            reading = await async_discover_pages(client)
        except CtcWebError as err:
            _LOGGER.warning("Could not read the display's menu: %s", err)
            reading = MenuReading()
        self._pages = reading.pages
        self._complete = reading.complete
        self._root = reading.root

        if not self._pages:
            # Modbus alone is a perfectly good entry; the display is a bonus.
            return self.async_create_entry(
                title=f"{self._model} ({self._host})",
                data=self._entry_data(),
                options={
                    CONF_SLOW_PAGES: [],
                    CONF_SLOW_INTERVAL: DEFAULT_SLOW_INTERVAL,
                    CONF_FAST_INTERVAL: DEFAULT_FAST_INTERVAL,
                    CONF_RESTORE_PAGE: True,
                    CONF_ENABLE_CONTROL: True,
                    CONF_LANGUAGE: LANG_SWEDISH,
                },
            )

        return self.async_show_form(
            step_id="slow",
            # Every page is ticked to begin with: a page nobody harvests is a
            # page whose values are missing, and switching one off afterwards
            # is easier than discovering that something was never read.
            data_schema=_slow_schema(
                self._pages,
                [page.page for page in self._pages],
                DEFAULT_SLOW_INTERVAL,
                True,
            ),
            # The privacy page travels as a placeholder for the sentence on
            # what is switched on from the start; see async_step_user.
            description_placeholders={
                "model": self._model,
                "count": str(len(self._pages)),
                **STATS_PLACEHOLDERS,
            },
        )

    # -------------------------------------------------------------- discovery

    async def async_step_dhcp(self, discovery_info: DhcpServiceInfo) -> ConfigFlowResult:
        """Offer setup when a device with CTC's MAC prefix appears.

        A unit that is already set up is recognised by the MAC its display
        gave on the system information page, and when it turns up at another
        address its entry moves there: the address in the data changes, the
        device key stays, so the device and every entity keep their history
        (roadmap R20). A unit whose MAC has never been read is recognised by
        its address alone. Either way, an entry waiting to be set up again is
        tried at once. Only what is neither is offered as new.

        An entry is never moved to an address another entry already has,
        which would put two clients on the controller's one Modbus place. An
        entry with the unit's MAC left behind that way is said in the log as a
        warning, since it is most often the same heat pump set up twice and
        only the owner can tell which entry is to go.
        """
        host = discovery_info.ip
        mac = format_mac(discovery_info.macaddress)
        entries = self._async_current_entries(include_ignore=False)
        known = [
            Known(
                entry.entry_id,
                entry.data.get(CONF_HOST),
                (entry.options.get(CONF_IDENTITY) or {}).get("mac"),
            )
            for entry in entries
        ]
        verdict, entry_id = match_discovery(known, host, mac)
        if verdict == MOVED and entry_id is not None:
            self._async_move_entry(entry_id, host)
        elif verdict == KNOWN and entry_id is not None:
            self._async_wake_entry(entry_id)
        left = held_back(known, host, mac)
        if left is not None:
            titles = {entry.entry_id: entry.title for entry in entries}
            _LOGGER.warning(
                "The heat pump of %s answered DHCP from %s, the address of %s, and %s is left "
                "where it is: two entries on one address would be two clients on the "
                "controller's one Modbus place. If both are the same heat pump, delete the "
                "one that does not answer",
                titles.get(left, left),
                host,
                titles.get(entry_id, entry_id),
                titles.get(left, left),
            )
        if verdict != NEW:
            return self.async_abort(reason="already_configured")
        # No updates here: an entry known by this address that has moved away
        # is not the unit at it now, and its address must not be taken back.
        # The unit at it now is given a key of its own instead, so it is
        # offered like any other; an ignored discovery is still asked about.
        await self.async_set_unique_id(f"{DOMAIN}_{self._free_key(host)}")
        self._abort_if_unique_id_configured()

        session = async_get_clientsession(self.hass)
        display = await async_probe_host(session, host, DEFAULT_WEB_PORT)
        if display is None:
            return self.async_abort(reason="not_a_ctc")

        self._host = host
        self._origin = None
        self._display = True
        self._model = display.model
        self._settings_name = display.settings_name
        # The card of a discovered unit reads strings.json's flow_title, "{name}".
        self.context["title_placeholders"] = {"name": display.label}
        return await self.async_step_confirm()

    @callback
    def _async_move_entry(self, entry_id: str, host: str) -> None:
        """Point a configured unit's entry at the address it turned up at.

        The device key is pinned in the same write (keys.moved_data), so an
        entry from before it existed goes on being known by its old address.
        A loaded entry is reloaded by its own update listener, which takes
        any change of the data for one; an entry waiting to be set up again
        has no listener, so it is asked to try at once rather than at the end
        of its back-off, against an address that no longer answers. Either
        way the new client is a second knock on the same pump, so the Modbus
        side is told first, and waits out the old address's close at the new
        one (modbus_api.note_moved).
        """
        entry = self.hass.config_entries.async_get_entry(entry_id)
        if entry is None:
            return
        old = str(entry.data.get(CONF_HOST, ""))
        _LOGGER.info(
            "The heat pump of %s answered DHCP from %s instead of %s; the entry follows it there",
            entry.title,
            host,
            old,
        )
        note_moved(old, host, int(entry.data.get(CONF_MODBUS_PORT, DEFAULT_MODBUS_PORT)))
        self.hass.config_entries.async_update_entry(
            entry,
            data=moved_data(entry.data, host),
            title=moved_title(entry.title, old, host),
        )
        self._async_wake_entry(entry.entry_id)

    @callback
    def _async_wake_entry(self, entry_id: str) -> None:
        """Try an entry waiting out its back-off at once: its unit has just asked for an address.

        Home Assistant does the same for a discovery of a unique_id it knows,
        but this flow recognises a configured unit by its MAC or its address
        and aborts before that rule is reached, so it says it here, for the
        unit where it is as for one that has moved. After a power cut that
        brings Home Assistant up before the heat pump, the display's first
        DHCP request is what ends the wait, rather than the back-off of up to
        ten minutes. A loaded entry is left alone.
        """
        entry = self.hass.config_entries.async_get_entry(entry_id)
        if entry is not None and entry.state is config_entries.ConfigEntryState.SETUP_RETRY:
            self.hass.config_entries.async_schedule_reload(entry_id)

    async def async_step_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask before adopting a unit that turned up by itself.

        Setting the entry up walks the panel through its menus, so a discovered
        unit is never adopted silently. A discovered unit never passes the menu
        of async_step_user, so this text says what is switched on from the
        start as well, and that port 80 and 502 have no login (roadmap R71).
        """
        if user_input is not None:
            return await self.async_step_connect()
        self._set_confirm_only()
        return self.async_show_form(
            step_id="confirm",
            description_placeholders={
                "model": self._model,
                "host": self._host or "",
                **STATS_PLACEHOLDERS,
                **EXPOSURE_PLACEHOLDERS,
            },
        )

    @staticmethod
    @callback
    def async_get_options_flow(
        entry: config_entries.ConfigEntry,
    ) -> CtcOptionsFlow:
        return CtcOptionsFlow(entry)


async def _async_version(hass) -> str:
    """The integration's own version, which stamps the stored menu."""
    from homeassistant.loader import async_get_integration

    return str((await async_get_integration(hass, DOMAIN)).version)


def page_label(page: Any) -> str:
    """A page in the tick boxes: its title, and how many values it holds in brackets.

    The title is the display's own, in the panel's language. The count stands
    alone, explained in the step's text, because a selector's option label
    cannot be translated with a number in it; it used to name the values in
    Swedish whatever language Home Assistant spoke.
    """
    return f"{page.title} ({len(page.values)})"


def _slow_schema(
    pages: list[Any],
    selected: list[int],
    interval: int,
    restore: bool,
) -> vol.Schema:
    options = [
        selector.SelectOptionDict(value=str(page.page), label=page_label(page))
        for page in pages
    ]
    return vol.Schema(
        {
            vol.Optional(
                CONF_SLOW_PAGES, default=[str(page) for page in selected]
            ): selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=options,
                    multiple=True,
                    mode=selector.SelectSelectorMode.LIST,
                )
            ),
            vol.Optional(CONF_SLOW_INTERVAL, default=interval): selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=MIN_SLOW_INTERVAL,
                    max=21600,
                    step=60,
                    unit_of_measurement="s",
                    mode=selector.NumberSelectorMode.BOX,
                )
            ),
            vol.Optional(CONF_RESTORE_PAGE, default=restore): bool,
        }
    )


class CtcOptionsFlow(config_entries.OptionsFlow):
    """Change which pages are harvested, how often, and whether control is on."""

    def __init__(self, entry: config_entries.ConfigEntry) -> None:
        self._entry = entry
        self._pages: list[Any] = []
        #: Whether ``_pages`` came off the panel just now, or out of storage
        #: because the panel would not give the menu up.
        self._fresh = False
        #: Whether the reading found any page at all, whole or not. An
        #: interrupted one is folded into the stored menu without a stamp;
        #: see async_step_rescan.
        self._found = False
        #: The operation data root the reading found, if it found one.
        self._root: int | None = None
        #: What the first form said, kept until the form after "read the
        #: menu again" is saved: a tick in that box used to throw the rest of
        #: the form away, intervals and tick boxes alike.
        self._pending: dict[str, Any] = {}
        #: What the search for the way to a page found, until it is saved.
        self._search: PageSearch | None = None

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is not None:
            options = self._entry.options
            menu = pages_from_storage(options.get(CONF_MENU))
            chosen = {int(page) for page in user_input.get(CONF_SLOW_PAGES, [])}
            pages = (
                {CONF_SLOW_PAGES: pages_to_storage([p for p in menu if p.page in chosen])}
                if menu
                else {}
            )
            self._pending = {
                **pages,
                CONF_VISIT_SYSTEM_INFO: bool(user_input.get(CONF_VISIT_SYSTEM_INFO, True)),
                CONF_CHECK_UPDATES: bool(user_input.get(CONF_CHECK_UPDATES, True)),
                CONF_FAST_INTERVAL: int(
                    user_input.get(
                        CONF_FAST_INTERVAL, options.get(CONF_FAST_INTERVAL, DEFAULT_FAST_INTERVAL)
                    )
                ),
                CONF_SLOW_INTERVAL: int(
                    user_input.get(
                        CONF_SLOW_INTERVAL, options.get(CONF_SLOW_INTERVAL, DEFAULT_SLOW_INTERVAL)
                    )
                ),
                CONF_RESTORE_PAGE: bool(
                    user_input.get(CONF_RESTORE_PAGE, options.get(CONF_RESTORE_PAGE, True))
                ),
                CONF_ENABLE_CONTROL: bool(
                    user_input.get(CONF_ENABLE_CONTROL, options.get(CONF_ENABLE_CONTROL, True))
                ),
                CONF_SEND_STATISTICS: bool(user_input.get(CONF_SEND_STATISTICS, True)),
                CONF_DEBUG_REPORT: bool(
                    user_input.get(CONF_DEBUG_REPORT, debug_report_on(options))
                ),
            }
            if user_input.get("rescan") and user_input.get(CONF_FIND_PAGE):
                # Each walks the panel, and one after the other in one dialog
                # is more of the panel than anybody asked for at once.
                return self.async_abort(reason="one_walk")
            if user_input.get("rescan"):
                # The rest of the form rides along to the next step and is
                # saved with it. Nothing is written yet: a write reloads the
                # entry, and the walk below must not meet the reload's own
                # first harvest on the panel.
                return await self.async_step_rescan()
            if user_input.get(CONF_FIND_PAGE):
                # The same: the rest of the form is saved with what it finds.
                return await self.async_step_find_page()
            return await self._async_save(self._pending)

        options = self._entry.options
        menu = pages_from_storage(options.get(CONF_MENU))
        selected = [page.page for page in pages_from_storage(options.get(CONF_SLOW_PAGES))]
        pages_field: dict[Any, Any] = {}
        if menu:
            # The whole menu is kept, so a page can be switched on or off here
            # without walking the panel through its menus again.
            pages_field[
                vol.Optional(CONF_SLOW_PAGES, default=[str(page) for page in selected])
            ] = selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=[
                        selector.SelectOptionDict(value=str(page.page), label=page_label(page))
                        for page in menu
                    ],
                    multiple=True,
                    mode=selector.SelectSelectorMode.LIST,
                )
            )
        schema = vol.Schema(
            {
                **pages_field,
                vol.Optional(
                    CONF_FAST_INTERVAL,
                    default=options.get(CONF_FAST_INTERVAL, DEFAULT_FAST_INTERVAL),
                ): selector.NumberSelector(
                    selector.NumberSelectorConfig(
                        min=10, max=600, step=5, unit_of_measurement="s",
                        mode=selector.NumberSelectorMode.BOX,
                    )
                ),
                vol.Optional(
                    CONF_SLOW_INTERVAL,
                    default=options.get(CONF_SLOW_INTERVAL, DEFAULT_SLOW_INTERVAL),
                ): selector.NumberSelector(
                    selector.NumberSelectorConfig(
                        min=MIN_SLOW_INTERVAL, max=21600, step=60,
                        unit_of_measurement="s",
                        mode=selector.NumberSelectorMode.BOX,
                    )
                ),
                vol.Optional(
                    CONF_RESTORE_PAGE, default=options.get(CONF_RESTORE_PAGE, True)
                ): bool,
                vol.Optional(
                    CONF_ENABLE_CONTROL,
                    default=options.get(CONF_ENABLE_CONTROL, True),
                ): bool,
                vol.Optional(
                    CONF_VISIT_SYSTEM_INFO,
                    default=options.get(CONF_VISIT_SYSTEM_INFO, True),
                ): bool,
                vol.Optional(
                    CONF_CHECK_UPDATES,
                    default=options.get(CONF_CHECK_UPDATES, True),
                ): bool,
                vol.Optional(
                    CONF_SEND_STATISTICS,
                    default=options.get(CONF_SEND_STATISTICS, True),
                ): bool,
                # The switch on the CTC page is the same setting (switch.py).
                vol.Optional(CONF_DEBUG_REPORT, default=debug_report_on(options)): bool,
                vol.Optional("rescan", default=False): bool,
                # Only where there is a display to search (roadmap L7).
                **(
                    {vol.Optional(CONF_FIND_PAGE, default=False): bool}
                    if has_display(self._entry.data)
                    else {}
                ),
            }
        )
        return self.async_show_form(
            step_id="init", data_schema=schema,
            description_placeholders=STATS_PLACEHOLDERS,
        )

    async def _async_save(
        self, changes: dict[str, Any], data: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Write the options, which reloads the entry unless the debug report is all that changed.

        Switching the statistics off erases what has already been sent, rather
        than merely going quiet, and that happens here, at the save, so that a
        form abandoned on the way, at a busy panel for one, changes nothing.
        Imported here rather than at the top: stats.py pulls in Home
        Assistant, and this module is read by tests that run without it.

        A save that changes nothing in effect but the debug report, a setting
        the options lacked written with the value it already had counting as
        no change (FIRST_FORM_DEFAULTS), writes the debug report alone over
        the stored options, and the reload listener leaves a write of it
        alone: a reload can cost the pump's one Modbus place for minutes.

        New entry data, where there is any, goes in the same write as the
        options, so the entry reloads once: the options the flow then hands
        Home Assistant are the ones already written, and change nothing.
        """
        was_on = self._entry.options.get(CONF_SEND_STATISTICS, True)
        if was_on and not changes.get(CONF_SEND_STATISTICS, True):
            from .stats import async_forget_install

            await async_forget_install(self.hass, self._entry, DOMAIN)
        stored = self._entry.options
        options = {**stored, **changes}
        if data is None and only_the_debug_report_changes(stored, options, FIRST_FORM_DEFAULTS):
            wanted = debug_report_on(options)
            options = dict(stored)
            if wanted != debug_report_on(stored):
                options[CONF_DEBUG_REPORT] = wanted
        if data is not None:
            self.hass.config_entries.async_update_entry(self._entry, data=data, options=options)
        return self.async_create_entry(title="", data=options)

    async def _async_display_found(self) -> dict[str, Any]:
        """The entry's data once the display of a Modbus-only entry has turned up.

        An entry made on Modbus alone (roadmap R11) is told so by its data, and
        nothing that needs the display runs for it. When "read the menu again"
        finds pages, the display is there after all, so the entry becomes one
        with a display, named by the settings file the display gives now.
        """
        data = {**self._entry.data, CONF_DISPLAY: True}
        display = await async_probe_host(
            async_get_clientsession(self.hass),
            data[CONF_HOST],
            data.get(CONF_WEB_PORT, DEFAULT_WEB_PORT),
        )
        if display is not None:
            data["model"] = display.model
            data["settings_name"] = display.settings_name
            data[CONF_SETTINGS_STEM] = display.stem
        return data

    def _web_client(self) -> CtcWebClient:
        """The display's client for a walk started from the options.

        A loaded entry's own client, so that this walk, the harvest, the menu
        re-read and the walk to the system information page all take the same
        panel lock: two walkers on one panel record routes that are wrong, and
        those are then saved for good. A client of its own only while the entry
        is not loaded, and that one takes the entry's panel lock too, the one
        lock every set-up of the entry hands its client, so a set-up that
        comes while this walk goes on holds its walks back. It is told the
        root from the stored menu as a set-up tells its client: one step up
        from the root is the home screen whether its tile can be read just
        then or not, and the search's walk home presses nothing there
        (page_search._async_walk_home).
        """
        runtime = getattr(self._entry, "runtime_data", None)
        client = getattr(runtime, "web_client", None)
        if client is not None:
            return client
        options = self._entry.options
        own = CtcWebClient(
            async_get_clientsession(self.hass),
            self._entry.data[CONF_HOST],
            self._entry.data.get(CONF_WEB_PORT, DEFAULT_WEB_PORT),
            int(options.get(CONF_LANGUAGE, LANG_SWEDISH)),
        )
        # Imported here, as menu_read_from_the_options is below: the package
        # pulls in the rest of the integration.
        from . import panel_lock

        own.panel = panel_lock(self._entry.entry_id)
        stored_root = options.get(CONF_MENU_ROOT)
        own.root = (
            int(stored_root)
            if isinstance(stored_root, int) and not isinstance(stored_root, bool)
            else menu_root(
                pages_from_storage(options.get(CONF_MENU))
                or pages_from_storage(options.get(CONF_SLOW_PAGES))
            )
        )
        return own

    async def async_step_rescan(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Walk the display's menu again and re-offer the tick boxes.

        A reading that gave nothing leaves the stored menu as it is: the tick
        boxes are offered from storage, and neither the menu nor the version
        stamp is written, so the reading is still owed. See
        catalogue.menu_after_rescan. The reading starts the count of
        background tries over, so a menu the background had given up on is
        read again after the save; see menu_read_from_the_options. What the
        first form said is saved together with this one.
        """
        if user_input is not None:
            chosen = {int(page) for page in user_input.get(CONF_SLOW_PAGES, [])}
            keep = [page for page in self._pages if page.page in chosen]
            changes = {
                **self._pending,
                CONF_SLOW_PAGES: pages_to_storage(keep),
                CONF_SLOW_INTERVAL: int(
                    user_input.get(CONF_SLOW_INTERVAL, DEFAULT_SLOW_INTERVAL)
                ),
                CONF_RESTORE_PAGE: user_input.get(CONF_RESTORE_PAGE, True),
            }
            if self._fresh:
                # Only a menu that came whole off the panel replaces the
                # stored one and is stamped, with the root it was swept from.
                # A missed reading writes neither: the stored menu stands as
                # it was, and the reading stays owed.
                changes[CONF_MENU] = pages_to_storage(self._pages)
                changes[CONF_MENU_VERSION] = await _async_version(self.hass)
                if self._root is not None:
                    changes[CONF_MENU_ROOT] = self._root
            elif self._found:
                # An interrupted reading is folded in for what it found, the
                # stored pages it did not reach standing beside them in their
                # old place (catalogue.menu_after_rescan), so a page only it
                # knows can be ticked here and is still on the list, with its
                # tick, the next time the form is opened. Left out of the menu
                # it could not be offered there, and the next save of the form
                # dropped it from the harvest without a word. Not stamped: the
                # reading stays owed, and the whole one that follows replaces
                # the menu with the tick kept.
                changes[CONF_MENU] = pages_to_storage(self._pages)
            data = None
            if self._found and not has_display(self._entry.data):
                data = await self._async_display_found()
            return await self._async_save(changes, data)

        try:
            reading = await async_rescan_pages(self._web_client())
        except PanelBusy:
            # The harvest, or a walk through the menu, holds the panel. A form
            # that waited for it would hang for minutes, so it says so instead
            # and is asked again in a moment. Nothing has been saved.
            return self.async_abort(reason="panel_busy")
        except CtcWebError as err:
            _LOGGER.warning("Could not read the display's menu: %s", err)
            reading = MenuReading()
        # Imported here: the package pulls in Home Assistant, and this module
        # is read by tests that run without it.
        from . import menu_read_from_the_options

        menu_read_from_the_options(self._entry.entry_id, self.hass.loop.time(), reading)
        # An interrupted sweep is offered for what it found, beside the stored
        # pages it did not reach, and is not fresh: nothing disappears and the
        # version is not stamped. A whole one keeps a stored page it did not
        # reach while the page still belongs: ticked, as the first form left
        # the tick boxes, reached by a route, and in the display's screen map.
        # See catalogue.menu_after_rescan.
        settings = {**self._entry.options, **self._pending}
        stored_menu = pages_from_storage(self._entry.options.get(CONF_MENU))
        stored_selection = pages_from_storage(settings.get(CONF_SLOW_PAGES))
        pages, self._fresh = menu_after_rescan(
            stored_menu,
            stored_selection,
            reading.pages,
            reading.complete,
            root=reading.root if reading.root is not None else settings.get(CONF_MENU_ROOT),
            screen_pages=reading.screen_pages,
        )
        # Each row carries the key it had where that was another, so the
        # set-up the save brings moves its entity and stored values over
        # (roadmap L2). A page read whole is paired like the background's
        # reading; a stored page the reading did not reach pairs with itself.
        # A row the display hides just now stays on its page, as in the
        # background's reading (keys.keep_hidden_rows).
        before = union_by_page(stored_menu, stored_selection)
        self._pages = with_previous_keys(before, keep_hidden_rows(before, pages))
        self._found = bool(reading.pages)
        self._root = reading.root

        # Offered as the first form left them, so a tick box or an interval
        # changed there is what this form shows.
        already = [page.page for page in pages_from_storage(settings.get(CONF_SLOW_PAGES, []))]
        return self.async_show_form(
            step_id="rescan",
            data_schema=_slow_schema(
                self._pages,
                already,
                int(settings.get(CONF_SLOW_INTERVAL, DEFAULT_SLOW_INTERVAL)),
                bool(settings.get(CONF_RESTORE_PAGE, True)),
            ),
            # The pages the reading found, not the stored ones kept beside them.
            description_placeholders={"count": str(len(reading.pages) or len(self._pages))},
        )

    # ------------------------------------------------- the way to one page

    async def async_step_find_page(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask for a page the reading of the menu does not reach, and search for the way there.

        Roadmap L7, on the owner's decision to build it now and run it at the
        panel: the search moves the panel, so it runs only when the owner
        submits this form, never by itself, and the form says that somebody
        should stand at the panel. The field is filled in with the page the
        panel shows, read without a press, so an owner who has walked the
        panel to the page by hand has nothing to type. The search takes the
        panel lock every walk takes (page_search.async_search_page_locked),
        and a panel already taken is said on the form at once. A search that
        ends without the page says why on the form and saves nothing; one
        that finds it shows what it found on the next step, where submitting
        saves it with the rest of the first form.
        """
        client = self._web_client()
        if user_input is None:
            return self._find_page_form(await _async_page_shown(client))
        target = int(user_input[CONF_PAGE])
        try:
            search = await async_search_page_locked(client, target)
        except PanelBusy:
            return self._find_page_form(target, "panel_busy")
        if not search.found:
            return self._find_page_form(target, SEARCH_ERRORS.get(search.reason, "page_not_found"))
        self._search = search
        return await self.async_step_find_page_found()

    def _find_page_form(self, page: int | None, error: str | None = None) -> ConfigFlowResult:
        """The form that asks for the page, filled in with ``page`` where it is known."""
        field = (
            vol.Required(CONF_PAGE, default=page) if page is not None else vol.Required(CONF_PAGE)
        )
        host = self._entry.data[CONF_HOST]
        port = int(self._entry.data.get(CONF_WEB_PORT, DEFAULT_WEB_PORT))
        where = host if port == DEFAULT_WEB_PORT else f"{host}:{port}"
        return self.async_show_form(
            step_id=STEP_FIND_PAGE,
            data_schema=vol.Schema(
                {
                    field: selector.NumberSelector(
                        selector.NumberSelectorConfig(
                            min=0, max=65535, step=1, mode=selector.NumberSelectorMode.BOX
                        )
                    )
                }
            ),
            errors={"base": error} if error else {},
            # hassfest refuses a URL in strings.json, so the screen map's
            # address travels as a placeholder, like the statistics' addresses.
            description_placeholders={
                "taps": str(MAX_TAPS),
                "depth": str(MAX_DEPTH),
                "screen_map": f"http://{where}/sm/all",
            },
        )

    async def async_step_find_page_found(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Say what the search found; submitting adds the page to the menu, ticked."""
        search = self._search
        assert search is not None and search.page is not None
        if user_input is not None:
            return await self._async_save(self._found_page_changes(search))
        page = search.page
        return self.async_show_form(
            step_id=STEP_FIND_PAGE_FOUND,
            description_placeholders={
                "page": str(page.page),
                "title": page.title,
                "count": str(len(page.values)),
                "steps": str(len(page.route)),
                "tries": str(search.tries),
            },
        )

    def _found_page_changes(self, search: PageSearch) -> dict[str, Any]:
        """The options with the page the search found in the menu, and ticked.

        In the stored menu where it was, as the search read it, route and
        all, or last where the menu did not have it; ticked along with what
        the first form ticked. A page the menu had keeps the keys of its rows
        and the rows it showed before and hides now, as after a reading of
        the menu (keys.keep_hidden_rows, keys.with_previous_keys). The
        version stamp is left alone: one page is not a reading of the menu,
        and the reading owed after an update keeps the page while it is
        ticked and has its route (catalogue.still_offered). The root is
        stored where it was not.
        """
        assert search.page is not None
        options = self._entry.options
        settings = {**options, **self._pending}
        stored_menu = pages_from_storage(options.get(CONF_MENU))
        stored_selection = pages_from_storage(settings.get(CONF_SLOW_PAGES))
        before = union_by_page(stored_menu, stored_selection)
        (found,) = with_previous_keys(before, keep_hidden_rows(before, [search.page]))
        menu = [found if page.page == found.page else page for page in before]
        if found.page not in {page.page for page in before}:
            menu.append(found)
        ticked = {page.page for page in stored_selection} | {found.page}
        changes = {
            **self._pending,
            CONF_MENU: pages_to_storage(menu),
            CONF_SLOW_PAGES: pages_to_storage([page for page in menu if page.page in ticked]),
        }
        if options.get(CONF_MENU_ROOT) is None and search.root is not None:
            changes[CONF_MENU_ROOT] = search.root
        return changes


async def _async_page_shown(client: Any) -> int | None:
    """The page the panel shows, read without a press, or None where it cannot be said.

    Not while another walk holds the panel: the page it shows then is the
    walk's, not one the owner chose.
    """
    if client.panel.locked():
        return None
    try:
        return await client.async_current_page()
    except CtcWebError:
        return None
