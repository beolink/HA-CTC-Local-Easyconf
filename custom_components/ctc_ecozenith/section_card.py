"""One section of the CTC page, for a dashboard of one's own (roadmap L11).

The page's cards take lists of values, each with its explanation and its source,
and only the page's builder (dashboard_views._Builder) can write them: a person
who wanted the energy section on their own overview had to copy the texts by
hand, and the copies never followed the integration. The section card,
custom:ctc-ecozenith-section in www/ctc-ecozenith-card.js, takes a heat pump and
a section instead, asks for the section over the websocket command
ctc_ecozenith/items (websocket.py) and draws what comes back with the page's own
card.

What comes back is cut out of the page itself rather than built beside it: the
tab the section is on is built by the very function that builds it for the
page, from the very description of the pump the page is built from
(dashboard._collect), in the language the page is built in, and the section is
picked out of the result by its heading. The page has one language for all its
pumps, Home Assistant's (roadmap R23), which the websocket command passes on,
whatever language a pump's panel is read in. So the section card shows
exactly what the page shows in that section, the rule that
leaves out a reading only ever zero included, and follows the page when the
page changes. Nothing is added: the cards come back as the page has them, and
only the integration's own cards, which are all the section card can draw.

A section is named by an id that stays put when its heading is translated or
reworded (SECTIONS). Most are the ids the page itself uses; the hot water is on
two tabs, as readings and as controls, so the controls' sections for the house
all carry "_controls", and the overview's own carry what they are, as does the
section last on the controls tab, troubleshooting, with the debug report's
switch. What Home Assistant holds in the pump (roadmap R49) is a section only
while it holds anything, and the card keeps the page's rule: it is drawn while
the count is above zero.

Free of Home Assistant, like dashboard_views.py, so the sections can be checked
against the page without an installation (tests/test_section_card.py).
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any, Final

from . import dashboard_views as views

#: The page's tabs, by the TEXT key of their title, and what builds each.
_TABS: Final[dict[str, Callable[..., list[dict[str, Any]]]]] = {
    "overview": views.overview_sections,
    "controls": views.controls_sections,
    "performance": views.performance_sections,
    "values": views.values_sections,
}

#: Every section the section card offers, in the page's own order: the id, the
#: tab it is on, and the TEXT key of its heading. The status has none: its
#: heading on the page is the pump's own name, and "status" is what TEXT
#: calls it otherwise.
SECTIONS: Final[dict[str, tuple[str, str | None]]] = {
    "status": ("overview", None),
    "overrides": ("overview", "overrides"),
    "quick_controls": ("overview", "quick"),
    "key_readings": ("overview", "readings"),
    "heating_controls": ("controls", "heating"),
    "hot_water_controls": ("controls", "hot_water"),
    "power_controls": ("controls", "power"),
    "troubleshooting": ("controls", "troubleshooting"),
    "temperatures": ("performance", "temperatures"),
    "hot_water": ("performance", "hot_water"),
    "energy": ("performance", "energy"),
    "compressor": ("performance", "compressor"),
    "cycles": ("performance", "cycles"),
    "settings": ("performance", "settings"),
    "unit": ("performance", "unit"),
    "other": ("performance", "other"),
    "values": ("values", "values"),
}

#: The cards the section card can draw: the page's own four.
CARD_TYPES: Final[frozenset[str]] = frozenset({
    views.CHIPS_CARD, views.READINGS_CARD, views.CONTROLS_CARD, views.ROWS_CARD,
})


def label(section: str, lang: str) -> str:
    """What the section is called in the card's editor: the tab, then the heading.

    "Prestanda: Energi och värmefaktor", so that the hot water on the controls
    tab and the hot water among the readings read apart. A heading that is the
    tab's own title, All values, is said once.
    """
    tab, heading = SECTIONS[section]
    text = views.TEXT[views.language(lang)]
    name = text[heading or "status"]
    return name if name == text[tab] else f"{text[tab]}: {name}"


def section_cards(
    pump: Mapping[str, Any],
    section: str,
    ha_version: tuple[int, int],
    lang: str | None = None,
) -> list[dict[str, Any]]:
    """The cards of one section of the pump's page, as the page has them.

    ``pump`` is the description dashboard._collect hands the page. An empty
    list is a section the page leaves out for this pump: control switched off,
    or nothing in it this installation has. Raises KeyError for a section that
    is not in SECTIONS, which the websocket command's schema keeps out.

    ``lang`` is the language the page is built in, Home Assistant's
    (dashboard.page_language); the section's words, its explanations and the
    heading it is found by are all in it. Without it the section is built in
    the language the pump carries, which dashboard._collect sets to the same.

    The status is the first section whose heading is the pump's name, since it
    is built first; every other section is the last whose heading is its own,
    so that a pump somebody named "Nyckeltal" still finds its key figures
    after its status.
    """
    tab, heading_key = SECTIONS[section]
    text = views.TEXT[views.language(pump.get("language") if lang is None else lang)]
    heading = str(pump.get("name") or views.TITLE) if heading_key is None else text[heading_key]
    matches = [
        found for found in _TABS[tab](pump, text, ha_version)
        if _heading_of(found) == heading
    ]
    if not matches:
        return []
    chosen = matches[0] if heading_key is None else matches[-1]
    return [card for card in chosen["cards"][1:] if card.get("type") in CARD_TYPES]


def _heading_of(section: Mapping[str, Any]) -> str | None:
    """The heading a section of the page opens with, or None."""
    cards = section.get("cards") or []
    first = cards[0] if cards else None
    if isinstance(first, Mapping) and first.get("type") == "heading":
        return str(first.get("heading"))
    return None
