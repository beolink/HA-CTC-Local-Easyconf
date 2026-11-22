"""Serve the page's cards, its chart and the icon pack, and load them into the frontend.

The CTC page uses two cards of its own (www/ctc-ecozenith-card.js), which
add an explanation to every value. They are served by the integration and added as a
Lovelace resource the way the NIBE and Miele integrations add theirs: best effort on
storage mode resources, with the version in the address so a browser picks up a new
card after an update, and exactly one entry of ours kept. Where resources are managed
in configuration.yaml nothing can be added from here, and the script is injected into
the frontend instead.

A browser that already has Home Assistant open loads resources once per page load, so
after an update the page has to be reloaded once before the new card is used.
"""

from __future__ import annotations

import logging
from pathlib import Path

from homeassistant.components import frontend
from homeassistant.components.http import StaticPathConfig
from homeassistant.core import HomeAssistant

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

CARD_URL = f"/{DOMAIN}/ctc-ecozenith-card.js"
CARD_FILE = Path(__file__).parent / "www" / "ctc-ecozenith-card.js"

#: The icon pack that gives the sidebar CTC's own mark. It is loaded on every
#: page rather than with the dashboard, since the sidebar is drawn long before
#: anybody opens one.
ICON_URL = f"/{DOMAIN}/ctc-ecozenith-icon.js"
ICON_FILE = Path(__file__).parent / "www" / "ctc-ecozenith-icon.js"

#: The chart of the coefficient of performance beside the outdoor temperature,
#: on two axes (www/ctc-ecozenith-chart.js), loaded into the frontend as the
#: icon pack is.
CHART_URL = f"/{DOMAIN}/ctc-ecozenith-chart.js"
CHART_FILE = Path(__file__).parent / "www" / "ctc-ecozenith-chart.js"

_REGISTERED = f"{DOMAIN}_card_registered"


async def async_register_card(hass: HomeAssistant, version: str) -> None:
    """Serve the cards and make the frontend load them. Once per run."""
    if hass.data.get(_REGISTERED):
        return
    hass.data[_REGISTERED] = True
    versioned = f"{CARD_URL}?v={version}"
    try:
        await hass.http.async_register_static_paths([
            StaticPathConfig(CARD_URL, str(CARD_FILE), False),
            StaticPathConfig(ICON_URL, str(ICON_FILE), False),
            StaticPathConfig(CHART_URL, str(CHART_FILE), False),
        ])
    except (RuntimeError, ValueError) as err:
        # Already served, from an earlier load in this process.
        _LOGGER.debug("Static path for the card not registered: %s", err)
    except Exception as err:  # the page still loads, and says what is missing
        _LOGGER.warning("Could not serve the CTC card: %s", err)
    if not await _async_register_resource(hass, versioned):
        frontend.add_extra_js_url(hass, versioned)
    # The icon pack is always an extra module: an icon is wanted on every page.
    frontend.add_extra_js_url(hass, f"{ICON_URL}?v={version}")
    frontend.add_extra_js_url(hass, f"{CHART_URL}?v={version}")


async def _async_register_resource(hass: HomeAssistant, versioned: str) -> bool:
    """Keep exactly one resource of ours, at this version. False where that is not possible."""
    try:
        lovelace = hass.data.get("lovelace")
        if isinstance(lovelace, dict):
            resources = lovelace.get("resources")
        else:
            resources = getattr(lovelace, "resources", None)
        if resources is None or not hasattr(resources, "async_create_item"):
            return False  # resources from configuration.yaml
        if not getattr(resources, "loaded", True):
            await resources.async_load()
            resources.loaded = True
        ours = [
            item for item in resources.async_items()
            if str(item.get("url", "")).split("?")[0] == CARD_URL
        ]
        if not ours:
            await resources.async_create_item({"res_type": "module", "url": versioned})
            _LOGGER.info("Registered Lovelace resource %s", versioned)
            return True
        first, *stale = ours
        if first.get("url") != versioned:
            await resources.async_update_item(first["id"], {"res_type": "module", "url": versioned})
            _LOGGER.info("Updated Lovelace resource to %s", versioned)
        for item in stale:
            await resources.async_delete_item(item["id"])
        return True
    except Exception as err:  # a missing card must not stop the integration
        _LOGGER.warning("Could not add the CTC card as a Lovelace resource: %s", err)
        return False
