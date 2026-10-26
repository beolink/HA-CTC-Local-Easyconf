"""The CTC page in the sidebar, built afresh every time it is opened.

The layout is dashboard_views.py; this file finds the entities and talks to Lovelace.

The page is a Lovelace dashboard that is never stored. The frontend asks a dashboard
for its config each time the page is opened (the websocket command lovelace/config),
and this one answers with a layout built there and then from the integration's current
entities. So it cannot go stale: an entity that is enabled, disabled, renamed or
harvested from a newly ticked display page is on the page the next time it loads, and
a newer version of the integration brings its newer layout with it. While a page is
open, a change is announced with lovelace_updated, the event the frontend already
listens for, and the page fetches the new layout on its own.

It is registered the way Lovelace registers a dashboard from configuration.yaml: an
object among the lovelace data's dashboards and a lovelace panel at the same address.
Home Assistant offers no public call for this, so both are checked before use and a
failure only means there is no page; it never touches the integration itself. Read
only (mode yaml): there is nothing to edit, nothing is written to disk, and an address
that already holds a panel or a dashboard is left alone.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
import logging
from typing import Any

from homeassistant.components import frontend
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import MAJOR_VERSION, MINOR_VERSION
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.helpers import device_registry as dr, entity_registry as er
from homeassistant.helpers.debounce import Debouncer
from homeassistant.helpers.json import json_bytes, json_fragment

from . import dashboard_views as views
from .card import async_register_card
from .const import (
    CONF_LIVE_INTERVAL,
    CONF_SLOW_INTERVAL,
    DEFAULT_LIVE_INTERVAL,
    DEFAULT_SLOW_INTERVAL,
    DOMAIN,
)
from .keys import prefix_of

_LOGGER = logging.getLogger(__name__)

_DATA = f"{DOMAIN}_page"
_LOVELACE = "lovelace"
#: Long enough to fold a reload's many registry updates into one refresh.
_REFRESH_COOLDOWN = 2.0


def _lovelace_dashboards(hass: HomeAssistant) -> dict | None:
    """Lovelace's dashboards by url path: a dict key before 2025, an attribute since."""
    data = hass.data.get(_LOVELACE)
    found = data.get("dashboards") if isinstance(data, dict) else getattr(data, "dashboards", None)
    return found if isinstance(found, dict) else None


@callback
def _collect(hass: HomeAssistant, entry_id: str | None = None) -> list[dict[str, Any]]:
    """One plain dict per running heat pump, as dashboard_views expects.

    With ``entry_id``, only that entry's pump, described exactly as the page
    describes it: the section card's command (websocket.py) asks this way.
    """
    ent_reg = er.async_get(hass)
    dev_reg = dr.async_get(hass)
    pumps: list[dict[str, Any]] = []
    for entry in hass.config_entries.async_entries(DOMAIN):
        if entry_id is not None and entry.entry_id != entry_id:
            continue
        runtime = getattr(entry, "runtime_data", None)
        if entry.state is not ConfigEntryState.LOADED or runtime is None:
            continue
        prefix = prefix_of(runtime.device)
        entities: dict[str, str] = {}
        names: dict[str, str] = {}
        for item in er.async_entries_for_config_entry(ent_reg, entry.entry_id):
            # Leave out what a person switched off or hid, as the built-in
            # dashboards do.
            if item.disabled_by or item.hidden_by or not item.unique_id.startswith(prefix):
                continue
            key = item.unique_id[len(prefix):]
            entities[key] = item.entity_id
            names[key] = item.name or item.original_name or item.entity_id
        # Of what is on the page, the readings this installation has only ever
        # reported as zero: hardware or registers it does not have or use.
        seen = getattr(runtime, "seen", None)
        unused: set[str] = set()
        if seen is not None:
            current = {
                key: state.state
                for key, entity_id in entities.items()
                if (state := hass.states.get(entity_id)) is not None
            }
            unused = seen.unused(current)
        device = next(iter(dr.async_entries_for_config_entry(dev_reg, entry.entry_id)), None)
        name = (device and (device.name_by_user or device.name)) or runtime.device.get("name")
        pumps.append({
            "name": name or entry.title,
            "entities": entities,
            "names": names,
            # Each display value with its screen and its place in the page's
            # list, counted from 1, for the source line (R39): a page can
            # have four rows that read "Status 2", and the line tells them
            # apart where the name cannot.
            "pages": [
                {"title": page.title,
                 "page": page.page,
                 "values": [
                     {"key": v.key, "label": v.label, "unit": v.unit,
                      "screen": v.screen, "row": place}
                     for place, v in enumerate(page.values, start=1)
                 ]}
                for page in runtime.pages
            ],
            "energy_out": getattr(runtime.energy_out, "key", None),
            "energy_in": getattr(runtime.energy_in, "key", None),
            "unused": sorted(unused),
            "control_enabled": bool(runtime.control_enabled),
            # The configured interval, not the coordinator's own, which is
            # five minutes while the display is being retried and shorter
            # still before the first harvest after a start: the source line
            # says how often the page is read, not how the last read went.
            "display_interval": (
                int(entry.options.get(CONF_SLOW_INTERVAL, DEFAULT_SLOW_INTERVAL))
                if runtime.web is not None else None
            ),
            # The page the panel rests on and how often it is read without a
            # press, for the source line of its rows; None without one.
            "live_page": getattr(runtime.web, "live_page", None),
            "live_interval": (
                int(entry.options.get(CONF_LIVE_INTERVAL, DEFAULT_LIVE_INTERVAL))
                if getattr(runtime.web, "live_page", None) is not None else None
            ),
            # Home Assistant's language, as the integration's own entity names
            # have it through their translations (roadmap R23). The panel's
            # language, CONF_LANGUAGE, is how its menu and rows are read, and
            # names the display rows; it no longer picks the page's words.
            "language": page_language(hass),
        })
    return pumps


def page_language(hass: HomeAssistant) -> str:
    """The language the page is built in: Swedish for a Swedish Home Assistant, else English."""
    return views.language(hass.config.language)


@callback
def _waiting(hass: HomeAssistant) -> list[dict[str, Any]]:
    """The pumps Home Assistant is retrying, with the reason it gives.

    A pump that did not answer at set-up sits in setup_retry until it does,
    and used to look exactly like no pump at all on the page. A reload passes
    through the same state for a moment, which is also worth a word rather
    than "nothing is running".
    """
    return [
        {"name": entry.title, "reason": entry.reason}
        for entry in hass.config_entries.async_entries(DOMAIN)
        if entry.state is ConfigEntryState.SETUP_RETRY
    ]


@callback
def build_config(hass: HomeAssistant) -> dict[str, Any]:
    """The page as it should look right now. Never raises: a broken page says so."""
    try:
        return views.build_dashboard(
            _collect(hass),
            page_language(hass),
            (MAJOR_VERSION, MINOR_VERSION),
            waiting=_waiting(hass),
        )
    except Exception:  # the page must never break the frontend
        _LOGGER.exception("Could not build the CTC page")
        lang = page_language(hass)
        return views.message_dashboard(views.TEXT[lang]["failed"], lang)


def _page_class() -> type:
    """A Lovelace dashboard whose config is built on request.

    Made on first use so that a Home Assistant whose Lovelace has moved on only
    loses the page, not the integration.
    """
    from homeassistant.components.lovelace.dashboard import LovelaceConfig

    class CtcPage(LovelaceConfig):
        """What Lovelace asks for when the CTC page is opened."""

        def __init__(self, hass: HomeAssistant) -> None:
            super().__init__(hass, views.URL_PATH, {
                "mode": "yaml",
                "title": views.TITLE,
                "icon": views.ICON,
                "show_in_sidebar": True,
                "require_admin": False,
            })
            #: What the last page referred to, so a removed entity can be
            #: recognised as one of ours once the registry has forgotten it.
            self.entity_ids: set[str] = set()

        @property
        def mode(self) -> str:
            return "yaml"

        def _build(self) -> dict[str, Any]:
            config = build_config(self.hass)
            self.entity_ids = views.entity_ids(config)
            return config

        async def async_get_info(self) -> dict[str, Any]:
            # "auto-gen" rather than "yaml", so system health does not report
            # Lovelace itself as running in YAML mode because of this page.
            return {"mode": "auto-gen", "views": len(self._build().get("views", []))}

        async def async_load(self, force: bool) -> dict[str, Any]:
            return self._build()

        async def async_json(self, force: bool) -> json_fragment:
            return json_fragment(json_bytes(self._build()))

        @callback
        def announce(self) -> None:
            """Tell open pages to fetch the layout again."""
            self._config_updated()

    return CtcPage


@dataclass
class _Registered:
    page: Any
    refresh: Debouncer
    unsubscribe: list[Callable[[], None]] = field(default_factory=list)


async def async_register(hass: HomeAssistant, version: str) -> None:
    """Put the page in the sidebar. Tried once per run, whatever the number of pumps."""
    if _DATA in hass.data:
        return
    hass.data[_DATA] = None
    # The cards that explain every value, before the page that uses them.
    await async_register_card(hass, version)
    dashboards = _lovelace_dashboards(hass)
    if dashboards is None:
        _LOGGER.info("Lovelace is not loaded, so there is no CTC page")
        return
    if views.URL_PATH in dashboards or frontend.async_panel_exists(hass, views.URL_PATH):
        _LOGGER.info("/%s is already taken, so the CTC page is not added",
                     views.URL_PATH)
        return
    page = None
    try:
        page = _page_class()(hass)
        dashboards[views.URL_PATH] = page
        frontend.async_register_built_in_panel(
            hass,
            _LOVELACE,
            sidebar_title=views.TITLE,
            sidebar_icon=views.ICON,
            frontend_url_path=views.URL_PATH,
            config={"mode": "yaml"},
            require_admin=False,
        )
    except Exception:  # no page, but the integration runs on
        if page is not None and dashboards.get(views.URL_PATH) is page:
            dashboards.pop(views.URL_PATH)
        _LOGGER.warning("Could not add the CTC page to the sidebar", exc_info=True)
        return

    registered = _Registered(
        page=page,
        refresh=Debouncer(
            hass, _LOGGER, cooldown=_REFRESH_COOLDOWN, immediate=False, function=page.announce
        ),
    )

    @callback
    def _ours(event_data: Mapping[str, Any]) -> bool:
        # An event filter is handed the event's data, not the event.
        entity_id = event_data.get("entity_id")
        if entity_id in page.entity_ids:
            return True
        item = er.async_get(hass).async_get(entity_id) if entity_id else None
        return item is not None and item.platform == DOMAIN

    @callback
    def _registry_updated(_event: Event) -> None:
        registered.refresh.async_schedule_call()

    registered.unsubscribe.append(
        hass.bus.async_listen(
            er.EVENT_ENTITY_REGISTRY_UPDATED, _registry_updated, event_filter=_ours
        )
    )
    hass.data[_DATA] = registered


@callback
def async_announce_change(hass: HomeAssistant) -> None:
    """A pump started or stopped: open pages fetch the layout again, shortly."""
    registered = hass.data.get(_DATA)
    if registered is not None:
        registered.refresh.async_schedule_call()


@callback
def async_unregister(hass: HomeAssistant) -> None:
    """Take the page out of the sidebar, once no heat pump is left."""
    registered = hass.data.pop(_DATA, None)
    if registered is None:
        return
    for unsubscribe in registered.unsubscribe:
        unsubscribe()
    registered.refresh.async_cancel()
    frontend.async_remove_panel(hass, views.URL_PATH, warn_if_unknown=False)
    dashboards = _lovelace_dashboards(hass)
    if dashboards is not None and dashboards.get(views.URL_PATH) is registered.page:
        dashboards.pop(views.URL_PATH)
