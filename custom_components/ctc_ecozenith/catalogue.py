"""Discover which pages and values the connected display actually offers.

Screen and text numbering differs between models, an i255 and an i550 Pro do not
agree on either, so nothing here is hard coded. The catalogue is built by
reading the unit's own screen map, screen definitions and text catalogue.

Values are paired with labels geometrically: a value's name is the nearest label
to its left on the same row, falling back to the nearest label above it. That
mirrors how the panel itself is laid out.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Iterable, Mapping
from dataclasses import dataclass, field
import logging
import re
from typing import Any

from .const import (
    CHROME_BUTTON,
    LIVE_PAGE_NONE,
    MIN_LIVE_INTERVAL,
    PERIOD_MARKERS,
    ROW_TOLERANCE,
    SENTINELS,
    SlowPage,
    SlowValue,
)
from .keys import row_key, union_by_page
from .web_api import CtcWebClient, CtcWebError, Widget

_LOGGER = logging.getLogger(__name__)

# Units that appear inside the display's own format strings.
_UNIT_PATTERN = re.compile(r"(kWh|l/min|ppm|°C|kW|rps|bar|min|%|A|V|h)")
_CONVERSION = re.compile(r"%[.\-0-9lu]*[dfsu]")
#: A row the panel prints as hours and minutes, "03:46". One widget with two
#: variables on an i550 Pro; on an i255 two widgets with a ":" between them.
_CLOCK_FORMAT = re.compile(r"^\s*%0?\d*d:%0?\d*d\s*$")
_INTEGER_FORMAT = re.compile(r"^\s*%0?\d*[du]\s*$")
#: The "h:m" at the end of such a row's name says how it is printed, not what
#: the reading is, so it goes the way a unit does.
_CLOCK_SUFFIX = re.compile(r"\s+h:m\s*$")
#: "/24 h" is a period, starts per day; the h belongs to the 24, not to the
#: count, and the name keeps the period without it.
_PERIOD_UNIT = re.compile(r"(/\d+)\s+h\s*$")

_FLOW_X = -10000
MAX_READINGS_PER_ROW = 4
#: Anything this high up and this wide is the page heading, not a row name.
_HEADER_HEIGHT = 45


def _decimals(fmt: str) -> float:
    """Return the scale implied by the display's format string."""
    if ".1f" in fmt:
        return 0.1
    if ".-1f" in fmt:
        return 0.1
    if ".2f" in fmt:
        return 0.01
    return 1.0


# A unit at the end of the row name. Not when a "/24" stands right before it:
# "Antal starter /24 h" is a count per day, and the h is the day's.
_LABEL_UNIT = re.compile(
    r"(?<!/\d)(?<!/\d\d)(?<!/\d\d\d)[( ](kWh|l/min|ppm|°C|kW|rps|bar|min|%|A|V|h)\)?\s*$"
)


_POSITION_SUFFIX = re.compile(r"\s+\d+$")

#: Rows that count hours since the day the unit was commissioned: "Total
#: drifttid" is the time switched on, "Drifttid total" the compressor's, and in
#: English both are "Total operation time". Neither resets, and the panel does
#: not always print the h.
_HOUR_COUNTERS = ("total drifttid", "drifttid total", "total operation time")
#: Compressor starts since commissioning. The English name follows CTC's manual
#: and is not confirmed against a panel's own text catalogue.
_START_COUNTERS = ("antal starter", "number of starts")


def _base_label(label: str) -> str:
    """Drop the positional suffix added when a row carries several readings."""
    return _POSITION_SUFFIX.sub("", label.strip())


def _is_period(label: str) -> bool:
    """True for a row over a window of time, "/30 dagar" or "/24 h"."""
    text = _base_label(label).casefold()
    return any(marker in text for marker in PERIOD_MARKERS)


def is_lifetime_counter(label: str | None) -> bool:
    """True for a row that has counted since the unit was commissioned.

    Operating hours and compressor starts never go down, so their long term
    statistics are sums, like the energy counters'. The same names over a
    period, "Antal starter /24 h", rise and fall and are not counters.
    """
    text = _base_label(label or "").casefold()
    return text.startswith(_HOUR_COUNTERS + _START_COUNTERS) and not _is_period(text)


def _is_period_count(label: str | None) -> bool:
    """True for starts counted over a window, "Antal starter /24"."""
    text = _base_label(label or "").casefold()
    return text.startswith(_START_COUNTERS) and _is_period(text)


def is_period_count(label: str | None) -> bool:
    """The starts per day row, by its label, for whoever reads it off the harvest.

    "Antal starter /24" (the h went with the period in 0.17.0), the row that
    transitions.py divides the day's compressor minutes by.
    """
    return _is_period_count(label)


def is_clock_format(fmt: str | None) -> bool:
    """True when a row is printed as hours and minutes, "%02d:%02d"."""
    return bool(fmt) and _CLOCK_FORMAT.match(fmt) is not None


def _implied_unit(label: str) -> str | None:
    """The unit a row carries by what it is, where the panel prints none.

    "Drifttid total" is hours like the "Total drifttid h" above it; the panel
    just leaves the h off that row.
    """
    text = _base_label(label).casefold()
    if text.startswith(_HOUR_COUNTERS) and not _is_period(text):
        return "h"
    return None


def _unit(fmt: str, label: str = "") -> str | None:
    """Extract the unit from the format string, or failing that the label.

    CTC writes the unit inside the format string on some rows, for example
    ``%.-1frps``, and inside the row's name on others, for example
    ``Avgiven värme (kW)``. A row printed as hours and minutes is read as
    minutes, and a counter of hours that prints no unit is still hours.
    """
    if is_clock_format(fmt):
        return "min"
    stripped = _CONVERSION.sub(" ", fmt).replace("%%", " % ")
    match = _UNIT_PATTERN.search(stripped)
    if match:
        return match.group(1)
    match = _LABEL_UNIT.search(_base_label(label))
    if match:
        return match.group(1)
    return _implied_unit(label)


def _clean_label(label: str) -> str:
    """Drop a trailing unit from a row name so it reads well as an entity name.

    The positional suffix is kept, since it is what tells "in" from "out" on a
    row that carries two readings. The "h" of a "/24 h" and the "h:m" of a
    clock row go too: they say how the panel prints the row, and the reading
    carries its own unit.
    """
    suffix = _POSITION_SUFFIX.search(label.strip())
    base = _base_label(label)
    base = _CLOCK_SUFFIX.sub("", base)
    base = _PERIOD_UNIT.sub(r"\1", base)
    cleaned = _LABEL_UNIT.sub("", base).strip(" ()") or base
    return f"{cleaned}{suffix.group(0)}" if suffix else cleaned


def display_state_class(unit: str | None, label: str | None) -> str | None:
    """The state class a display reading should carry, going by its unit and row.

    Energy in kWh is either a lifetime counter, which only grows, or a period
    such as "Avgiven värme/30 dagar", which rises and falls as days leave the
    window. Home Assistant refuses "measurement" for energy: a counter is
    "total_increasing", and a period fits no state class at all, so it gets
    none. Operating hours and compressor starts since commissioning are
    counters as well, with or without a unit. Every other reading with a unit
    is a measurement, and one without a unit is left alone, a firmware version
    is a number too, except starts per day: a count that rises and falls with
    the weather, worth its daily statistics (R38) although it has no unit, and
    one that had them already, from the days the "/24 h" lent it an h.
    """
    if unit == "kWh":
        return None if _is_period(label or "") else "total_increasing"
    if is_lifetime_counter(label):
        return "total_increasing"
    if not unit:
        return "measurement" if _is_period_count(label) else None
    return "measurement"


def _raw_number(raw: list[Any], index: int) -> int | None:
    """One raw variable as an integer, or None when absent or a missing marker."""
    if index >= len(raw):
        return None
    item = raw[index]
    if isinstance(item, bool) or not isinstance(item, int) or item in SENTINELS:
        return None
    return item


def numeric_value(value: SlowValue, raw: list[Any]) -> float | None:
    """Turn a raw variable into a number, honouring CTC's missing markers.

    A clock row holds hours and minutes in two variables and is read as one
    figure in minutes, so "03:46" becomes 226 and can be graphed. The row says
    so by its unit. A menu stored by a version from before the minutes carries
    the same format with no unit, and that row goes on reading its hours, as it
    always has, until the menu has been read again and the unit arrives with
    it: the figure and its unit change together, never one before the other.
    """
    if not value.var_indices:
        return None
    if value.unit == "min" and is_clock_format(value.fmt) and len(value.var_indices) == 2:
        hours, minutes = (_raw_number(raw, index) for index in value.var_indices)
        if hours is None or minutes is None:
            return None
        return float(hours * 60 + minutes)
    item = _raw_number(raw, value.var_indices[0])
    if item is None:
        return None
    return round(item * value.scale, 3)


def _is_caption(widget: Widget) -> bool:
    """True when a widget is a text element, not an icon.

    Icons resolve to whatever entry their selector lands on, which on a
    schematic page is often an unrelated string from a long list such as the
    language names. Only text elements are trusted to name a reading.
    """
    return widget.kind in (2, 3)


def _usable_label(widget: Widget) -> bool:
    text = (widget.label or "").strip()
    if not text or len(text) < 2:
        return False
    # Alarm and info catalogue entries are rendered into status fields; they are
    # never the name of a neighbouring reading.
    return not (text.startswith("[E") or text.startswith("[I") or text == "* Demo *")


def has_conversion(fmt: str) -> bool:
    """True when a format string actually renders a number.

    Entries such as ``" / "`` and ``" , "`` are separators between two readings
    on the same row, not readings of their own.
    """
    return bool(_CONVERSION.search(fmt))


def _label_column(captions: list[Widget]) -> int | None:
    """Where the row names stand.

    Not simply the leftmost thing on the page: a divider or a rule drawn at x=0
    would take that place and then nothing would ever line up. The left edge
    most of the names share is what the layout is built on.
    """
    columns: dict[int, int] = {}
    for caption in captions:
        if caption.x > _FLOW_X:
            columns[caption.x] = columns.get(caption.x, 0) + 1
    if not columns:
        return None
    return min(columns, key=lambda x: (-columns[x], x))


def _is_heading(widget: Widget) -> bool:
    """The page's own title, which stands across the top and names nothing."""
    return 0 <= widget.y < _HEADER_HEIGHT and widget.width >= 80


def _pair_labels(widgets: list[Widget]) -> dict[int, str]:
    """Name each reading after the caption that belongs to it.

    Two layouts have to work. Most pages draw a row at a time, caption on the
    left and reading to its right. Some draw every caption first and then every
    reading, and the rows scrolled out of view all share one off screen y, so
    geometry has nothing left to separate them with. What both have in common is
    the order: the captions come in the order their readings do. So the captions
    in the left hand column queue up and each reading takes the one that has
    waited longest.

    A reading parked far to the left is laid out after the one before it and
    belongs to the same caption, which is how "Brine in/ut" carries two.

    A caption whose text the display would not give up still holds its place in
    the queue: it is a row of the page either way, and skipping it would hand
    its reading the next row's name. Where a caption and a reading are both on
    screen their rows have to agree: a caption left behind owns nothing and is
    passed over, and a caption still to come means this reading has no name.
    """
    # A reading is a text element too, so what makes a caption is that it
    # carries no format of its own.
    captions = [
        w for w in widgets
        if w.visible and _is_caption(w) and w.width > 0 and not w.value_fmt
    ]
    # A reading the panel has hidden still holds its row: the caption above it
    # is spoken for, and skipping it would hand that caption to the next row.
    # Only the visible ones are named, since only those become values.
    values = [w for w in widgets if w.value_fmt and has_conversion(w.value_fmt)]
    if not any(w.visible for w in values):
        return {}

    column = _label_column(captions)
    queue = [
        caption for caption in sorted(captions, key=lambda w: w.index)
        if column is not None and abs(caption.x - column) <= 2 and not _is_heading(caption)
    ]

    groups: list[tuple[Widget | None, list[Widget]]] = []
    for value in sorted(values, key=lambda w: w.index):
        if value.x <= _FLOW_X and groups:
            groups[-1][1].append(value)
            continue
        caption = None
        while queue:
            candidate = queue[0]
            if candidate.y >= 0 <= value.y:
                if candidate.y < value.y - ROW_TOLERANCE:
                    queue.pop(0)  # a row of its own, without a reading
                    continue
                if candidate.y > value.y + ROW_TOLERANCE:
                    break  # the caption belongs further down; this one has none
            elif candidate.y >= 0 > value.y:
                # The caption is on a row the panel is drawing and the reading
                # is not, so the caption is a heading over the rows below it.
                queue.pop(0)
                continue
            elif value.y >= 0 > candidate.y:
                # Drawn on the page while every caption left is scrolled away:
                # a figure on a schematic, which no caption names.
                break
            caption = queue.pop(0)
            break
        groups.append((caption, [value]))

    pairing: dict[int, str] = {}
    for caption, members in groups:
        members = [value for value in members if value.visible]
        if not members:
            continue
        # A genuine row holds a handful of readings at most: the widest seen is
        # "Överhettning S/H" with four. More than that is a block that ran past
        # its caption, and guessing would give unrelated numbers the same name.
        if caption is None or len(members) > MAX_READINGS_PER_ROW:
            continue
        if not _usable_label(caption):
            continue
        name = (caption.label or "").strip()
        for position, value in enumerate(members, start=1):
            pairing[value.index] = (
                name if len(members) == 1 else f"{name} {position}"
            ).strip()
    return pairing


async def async_page_title(client: CtcWebClient, screens: list[int]) -> str:
    """Return a human title for a page.

    Operation data pages carry their heading as a wide text across the top. The
    chrome screens have only buttons, and icons resolve to whatever entry their
    selector lands on, so a title candidate has to be both near the top and wide
    enough not to be an icon.
    """
    candidates: list[Widget] = []
    for screen in screens:
        try:
            widgets = await client.async_widgets(screen)
        except CtcWebError:
            continue
        candidates.extend(
            w
            for w in widgets
            if w.visible
            and _usable_label(w)
            and 0 <= w.y < 45
            and w.width >= 80
        )
    if candidates:
        best = max(candidates, key=lambda w: w.width)
        text = (best.label or "").strip()
        if len(text) >= 3:
            return text
    return f"Sida {screens[0] if screens else '?'}"


#: A reading as the page draws it, before it becomes a value: the row name the
#: pairing gave it, the format string, the variables and the widget's index.
_Reading = tuple[str, str, list[int], int]


def _join_clock_rows(readings: list[_Reading]) -> list[_Reading]:
    """Fold a row's hours and minutes into one reading where they are drawn apart.

    An i550 Pro prints "Drift /24 h:m" with one format, ``%02d:%02d``, and two
    variables. An i255 prints the same row as two integers with a ":" between
    them, which the pairing names "Drift /24 h:m 1" and "... 2". Two readings
    of a clock row make one figure, not two unitless sensors, so the second is
    folded into the first and the pair looks the way the i550 draws it: one
    row named "Drift /24 h:m", and so one key, the one an i550 Pro has always
    had. The i255's two keys with _1 and _2 end here, by design.
    """
    joined: list[_Reading] = []
    for raw_label, fmt, indices, index in readings:
        if joined and _CLOCK_SUFFIX.search(_base_label(raw_label)):
            prev_label, prev_fmt, prev_indices, prev_index = joined[-1]
            if (
                _base_label(prev_label) == _base_label(raw_label)
                and _INTEGER_FORMAT.match(prev_fmt)
                and _INTEGER_FORMAT.match(fmt)
                and len(prev_indices) + len(indices) == 2
            ):
                joined[-1] = (
                    _base_label(raw_label),
                    f"{prev_fmt.strip()}:{fmt.strip()}",
                    prev_indices + indices,
                    prev_index,
                )
                continue
        joined.append((raw_label, fmt, indices, index))
    return joined


@dataclass
class PageValues:
    """What one reading of a page's screens came back with, gaps included.

    A screen whose widgets could not be read leaves its rows out, and a
    caption the catalogue would not give up (web_api.async_text answers ""
    for it) leaves its row named "Värde N": the key, which follows the row's
    place (keys.row_key), stays, but a row found by its name, the delivered
    heat counter among them, is not found under that one. Both
    are silent in ``values``: the list is as true as it goes and says nothing
    about what is missing. So the reading says it beside the values, and a
    page that was not read whole is not taken for the page.
    """

    values: list[SlowValue] = field(default_factory=list)
    #: The page's screens whose widgets could not be read this time.
    skipped: list[int] = field(default_factory=list)
    #: How many captions the catalogue would not give up while the page was
    #: read, where the client counts them (web_api.CtcWebClient.text_misses).
    text_misses: int = 0

    @property
    def whole(self) -> bool:
        """Whether every screen was read and every caption answered."""
        return not self.skipped and not self.text_misses


async def async_page_values(
    client: CtcWebClient, page: int, screens: list[int]
) -> list[SlowValue]:
    """Describe every formatted value on a page, gaps and all; see async_read_page_values."""
    return (await async_read_page_values(client, page, screens)).values


async def async_read_page_values(
    client: CtcWebClient, page: int, screens: list[int]
) -> PageValues:
    """Describe every formatted value on a page, and say whether the page was read whole."""
    found: list[SlowValue] = []
    skipped: list[int] = []
    seen: set[str] = set()
    misses_before = int(getattr(client, "text_misses", 0) or 0)
    for screen in screens:
        try:
            widgets = await client.async_widgets(screen)
        except CtcWebError as err:
            _LOGGER.debug("Skipping screen %s: %s", screen, err)
            skipped.append(screen)
            continue
        pairing = _pair_labels(widgets)
        readings: list[_Reading] = []
        for widget in widgets:
            if widget.value_fmt is None or not widget.value_vars:
                continue
            if not widget.visible or not has_conversion(widget.value_fmt):
                continue
            raw_label = (pairing.get(widget.index) or f"Värde {widget.index}").strip().rstrip(":")
            readings.append((raw_label, widget.value_fmt, list(widget.value_vars), widget.index))
        for raw_label, fmt, indices, _index in _join_clock_rows(readings):
            unit = _unit(fmt, raw_label)
            label = _clean_label(raw_label)
            # The key from where the row stands, never from its name: the name
            # may get better, and a caption the display would not give up
            # leaves the name "Värde N" for a round, but the key is what the
            # entity is known by and stays (keys.row_key, roadmap L2).
            base = row_key(page, screen, indices[0])
            key = base
            suffix = 2
            while key in seen:
                key = f"{base}_{suffix}"
                suffix += 1
            seen.add(key)
            found.append(
                SlowValue(
                    key=key,
                    label=label,
                    page=page,
                    screen=screen,
                    fmt=fmt,
                    var_indices=indices,
                    unit=unit,
                    scale=_decimals(fmt),
                )
            )
    misses = int(getattr(client, "text_misses", 0) or 0) - misses_before
    return PageValues(found, skipped, max(misses, 0))


@dataclass
class MenuReading:
    """What one walk through the display's menu came back with.

    ``pages`` is what was found. ``complete`` says whether that is the whole
    operation data subtree: the root was found and verified, the root was
    regained after every tap, and every page the sweep knew of was reached
    again for its own strip. The tap budget does not count against it, since
    the same menu spends it the same way every time.

    A reading that is not complete is still worth offering, but it is not the
    menu: a page that only the interrupted part of the sweep would have found
    is simply absent from it, and writing it in place of a stored menu would
    make that page, and its entities, disappear until the next release. So
    the readers that fold a reading into a stored menu treat an incomplete one
    as a reading still owed, and nothing stamps the version for it.

    ``gaps`` names the pages the sweep reached but did not read whole: a
    screen whose widgets would not come, or a caption the catalogue would not
    give up, which renames the row it belonged to (see :class:`PageValues`).
    Such a page is left out of ``pages`` and makes the reading incomplete,
    for the same reason an unreached page does: written in place of the
    stored page it would give a row a name it does not have. Before the keys
    followed the row's place (roadmap L2) that also took the row's entity
    with it, VSH's delivered heat counter among the rows that have read that
    way; now the entity stays, but the counter is found by its name, so the
    coefficient of performance would still lose it until the next reading.

    The walk also says how far it got (roadmap L6): whether the home screen
    was recognised, whether the operation data tile was found on it, whether
    pressing the tile led into the menu, and, where the display stopped
    answering partway, what it said. A walk that ends in nothing used to look
    the same whichever step failed, which is how an i360 could stand without
    a page read through several releases with nobody able to say why. The
    steps are None on a reading the sweep did not make, such as the empty
    one a caller puts in place of a walk that could not start, and a step is
    None where the display stopped answering before the walk got to try it:
    a display that does not answer is not one whose home screen or tile is
    missing, and the report, which carries the steps but not the error, would
    otherwise say the same no for both (F3.1). A walk that went its whole way
    says no for a step it did not reach.
    """

    pages: list[SlowPage] = field(default_factory=list)
    complete: bool = False
    #: The operation data root's page number, where the sweep found it. Every
    #: recorded route starts there, and the harvester steps back to it between
    #: two pages; a SlowPage does not carry it, so the menu does.
    root: int | None = None
    #: Pages read with a gap this time, by number; see the class docstring.
    gaps: list[int] = field(default_factory=list)
    #: Every page number in the display's screen map when the walk began, or
    #: None where the map was never read. A stored page the sweep did not
    #: reach is kept only while the map still has it (see still_offered): a
    #: page the display's software no longer has is evidence, and costs no
    #: tap to find out.
    screen_pages: frozenset[int] | None = None
    #: How far the walk got; see the class docstring.
    home_found: bool | None = None
    tile_found: bool | None = None
    root_entered: bool | None = None
    #: What the display client said when the display stopped answering
    #: partway. The pages found before it are not offered: a walk the display
    #: cut short is not a menu, and the readers took it for none before.
    error: str | None = None

    @property
    def mapped(self) -> bool:
        """Whether the walk got into the operation data menu and found pages there."""
        return self.error is None and bool(self.pages) and self.root_entered is not False

    def how_far(self) -> str:
        """Where a walk that found no menu stopped, as a clause for the log."""
        if self.error is not None:
            if self.root_entered:
                where = "inside the operation data menu"
            elif self.home_found:
                where = "after the home screen was found"
            else:
                where = "before the home screen was found"
            return f"the display stopped answering {where} ({self.error})"
        if not self.home_found:
            said = (
                "the home screen was not recognised, so the operation data tile was "
                "never looked for and nothing but the panel's own back button was pressed"
            )
        elif not self.tile_found:
            said = (
                "the home screen was found but the operation data tile on it was not, "
                "so nothing on it was pressed"
            )
        elif not self.root_entered:
            said = "the operation data tile was found but pressing it did not lead into the menu"
        else:
            said = "the operation data menu was entered but no page with a reading was found in it"
        if self.pages and not self.root_entered:
            said += "; the page the panel was showing is offered on its own"
        return said

    def describe(self) -> str:
        """One line on how the walk went, for the log."""
        if not self.mapped:
            return f"The display's menu could not be read: {self.how_far()}"
        text = (
            f"The display's menu was read: home screen, operation data tile and menu "
            f"found, {len(self.pages)} page(s)"
        )
        if not self.complete:
            text += ", though not the whole menu, so it is read again"
        return text

    def outcome(self) -> dict[str, Any]:
        """The walk in brief, for the runtime, the report and the diagnostics."""
        return {
            "home_found": self.home_found,
            "tile_found": self.tile_found,
            "root_entered": self.root_entered,
            "pages": len(self.pages),
            "complete": self.complete,
            "gaps": list(self.gaps),
            "error": self.error,
        }


#: The outcomes said out loud in this run, by display and by whether the walk
#: failed: a failure is a warning once, a success an info line once, and every
#: walk after that the same way is a debug line. The menu is read up to three
#: times a run, and a panel that never gives it up should not say so three
#: times; a panel that gives it up after a failure should still say that.
_SAID: set[tuple[str, bool]] = set()


def _say(client: Any, reading: MenuReading) -> None:
    """Log how the walk went, once per run per display and outcome."""
    failed = not reading.mapped
    level = logging.WARNING if failed else logging.INFO
    where = getattr(client, "base_url", None)
    if isinstance(where, str):
        if (where, failed) in _SAID:
            level = logging.DEBUG
        else:
            _SAID.add((where, failed))
    _LOGGER.log(level, "%s", reading.describe())


def warned_of_no_menu(client: Any) -> bool:
    """Whether a walk on this display has said on warning in this run that it found no menu.

    The background reading after an update ends its tries with a line of its
    own, and where the walk has said it already that line is not a second
    warning: the log says a menu that cannot be read once per start.
    """
    where = getattr(client, "base_url", None)
    return isinstance(where, str) and (where, True) in _SAID


def menu_root(pages: list[SlowPage]) -> int | None:
    """The operation data root among stored pages: the one with an empty route.

    Every other page's route starts from it. A root without values of its own
    is not among the pages at all, and then the number comes from where the
    menu was stored (CONF_MENU_ROOT) or is learnt when the tile is pressed.
    """
    return next((page.page for page in pages if not page.route), None)


async def async_discover_pages(
    client: CtcWebClient, require_root: bool = False
) -> MenuReading:
    """Walk the operation data subtree and describe every page it contains.

    The panel moves while this runs and is put back where it started: on a
    page of the menu by the route the sweep recorded to it, and on the root or
    the home screen the way the sweep came in (see _async_restore). Only the
    operation data subtree is entered, and only once its root has been found
    and verified. That subtree is read only on every CTC model checked, so a
    tap landing slightly off cannot change a setting; the menus around it are
    not, and a panel left in Avancerat or on a settings page must not be swept.
    So where the root cannot be found, no page control is pressed: the page
    the panel is showing is read as it stands and offered on its own, or, with
    ``require_root``, nothing is offered at all. The search for the root does
    press the panel's own back button on its way towards the home screen; that
    is the chrome, which navigates and never acts. The two readers that fold a
    reading into a stored menu ask for the root, since one page in place of
    the whole menu would be a loss, not a reading.

    Layout differs between models: an i255 puts a tab strip along the bottom, an
    i550 Pro does not. Rather than guess, every plausible control on the root
    page is tried once and the tap that reached each page is recorded, so poll
    time can replay a known route instead of deriving one again. The sweep
    stops the moment it cannot get back to the root, because every tap it
    makes is meant for a page it has verified it is on, and the reading then
    says it is not complete (see :class:`MenuReading`).

    The reading also says how far the walk got, and the walk says it in the
    log, once per run per display on warning where it found no menu and on
    info where it did. A display that stops answering partway is one of the
    ways it finds none: the panel is put back as usual and the reading
    carries the error, with no pages, rather than the error being raised.
    """
    reading = MenuReading(home_found=False, tile_found=False, root_entered=False)
    try:
        page_map = await client.async_screen_map(refresh=True)
        reading.screen_pages = frozenset(page_map)
        origin = await client.async_current_page()
    except CtcWebError as err:
        # Nothing has been pressed, so there is nothing to put back, and no
        # step was tried, so none of them says no.
        reading.home_found = reading.tile_found = reading.root_entered = None
        reading.error = str(err)
        _say(client, reading)
        return reading
    discovered: list[SlowPage] = []
    visited: set[int] = set()
    gaps: set[int] = set()
    complete = False
    found: tuple[int, int] | None = None
    searched = False

    try:
        found = await _async_operation_root(client, page_map, origin, reading)
        searched = True
        if found is None:
            if not require_root:
                _LOGGER.debug(
                    "Could not find the operation data menu; reading the page the panel "
                    "is showing without pressing anything on it"
                )
                here = await client.async_current_page()
                await _async_collect(client, page_map, here, discovered, visited, [], gaps)
        else:
            root, home = found
            # The home screen is where the sweep came in from, not a page of
            # the menu. A tap in the subtree that happens to land there must
            # not collect it, or its tiles would be pressed one by one at the
            # second level: Varmvatten, Värme, Avancerat.
            visited.add(home)
            await _async_collect(client, page_map, root, discovered, visited, [], gaps)
            complete = await _async_explore(
                client, page_map, root, home, discovered, visited, gaps=gaps
            )
    except CtcWebError as err:
        reading.error = str(err)
        if not searched:
            # The display went quiet during the search for the root: the
            # steps it had reached stand, and the ones it had not are
            # unknown rather than failed.
            for step in ("home_found", "tile_found", "root_entered"):
                if getattr(reading, step) is not True:
                    setattr(reading, step, None)
    finally:
        # Every page the sweep reached from the root has the route it was
        # reached by, a page without a reading of its own included, so a
        # panel that started on one of them is walked back there by it.
        routes = (
            {page.page: page.route for page in discovered} if found is not None else None
        )
        await _async_restore(client, page_map, origin, found, routes)

    if gaps and reading.error is None:
        # A page read with a gap is a page not read: left out, and the
        # reading is owed, as it is for a page the sweep did not reach.
        _LOGGER.info(
            "Page(s) %s of the display's menu were read with a gap, a screen or a "
            "caption that did not answer, so they are left out of this reading and "
            "the menu is read again",
            ", ".join(str(page) for page in sorted(gaps)),
        )
    if reading.error is None:
        reading.pages = [page for page in discovered if page.values and page.page not in gaps]
        reading.complete = complete and not gaps
        reading.gaps = sorted(gaps)
    reading.root = found[0] if found is not None else None
    _say(client, reading)
    return reading


class PanelBusy(Exception):
    """Somebody else holds the panel: the harvest, the menu re-read or the walk."""


async def async_rescan_pages(client: CtcWebClient) -> MenuReading:
    """Read the menu again for a form, under the harvester's own panel lock.

    The harvest, the menu re-read after an update and the walk to the system
    information page all take ``client.panel`` before they move the display,
    and a "read the menu again" from the options has to take the same lock:
    two walkers on one panel record routes that are wrong, and those routes
    are then saved in the options for good. A form cannot wait for the lock,
    though. A harvest holds the panel for as long as its pages take, and a
    dialog that hangs for minutes is a dialog somebody closes and opens again.
    So a panel that is already taken is answered with :class:`PanelBusy` at
    once and only a free one is walked. The look and the take happen with
    nothing awaited in between, so the harvester cannot slip in between them.
    """
    if client.panel.locked():
        raise PanelBusy
    async with client.panel:
        # A second reading, like the one after an update, only counts from a
        # verified operation data root: a single page in place of the whole
        # menu would be a loss (see async_discover_pages).
        return await async_discover_pages(client, require_root=True)


async def _async_explore(
    client: CtcWebClient,
    page_map: dict[int, list[int]],
    root: int,
    home: int,
    into: list[SlowPage],
    visited: set[int],
    max_taps: int = 26,
    gaps: set[int] | None = None,
) -> bool:
    """Tap what looks like a control, note where it leads, and go one deeper.

    One level is not always enough. An i255 puts every operation data page one
    tap from the root, an i550 Pro hides the heat pump's own page behind a
    second tab strip, and that page is the one carrying the model, the control
    board's firmware and the delivered heat.

    The sweep ends early the moment the root cannot be regained: from then on
    the panel is on a page nobody chose. Returns whether the sweep was whole,
    which it is not when the root was lost or when a page it had found could
    not be reached again for its own strip; what was found is kept in ``into``
    either way, and the caller says what an incomplete reading is worth.
    """
    taps, intact, whole = await _async_tap_pages(
        client, page_map, root, root, home, into, visited, max_taps, gaps=gaps
    )

    # Second level: pages found above that carry a strip of their own.
    for page in [p.page for p in list(into) if p.page != root]:
        if not intact or taps >= max_taps:
            break
        if not await _async_return_to_root(client, page_map, root, home):
            intact = False
            break
        route = next((p.route for p in into if p.page == page), [])
        if not await _async_replay(client, page_map, route, page):
            _LOGGER.debug(
                "The recorded route to page %s did not arrive, so its own strip was not "
                "swept; the reading is not the whole menu",
                page,
            )
            whole = False
            continue
        more, intact, page_whole = await _async_tap_pages(
            client, page_map, page, root, home, into, visited, max_taps - taps, route, gaps
        )
        taps += more
        whole = whole and page_whole
    if not intact:
        _LOGGER.warning(
            "Lost the way back to the operation data menu, so the sweep stopped early; "
            "what it found is offered but not taken for the whole menu"
        )
        return False
    await _async_return_to_root(client, page_map, root, home)
    return whole


async def _async_tap_pages(
    client: CtcWebClient,
    page_map: dict[int, list[int]],
    page: int,
    root: int,
    home: int,
    into: list[SlowPage],
    visited: set[int],
    budget: int,
    prefix: list[tuple[int, int]] | None = None,
    gaps: set[int] | None = None,
) -> tuple[int, bool, bool]:
    """Tap every target on one page, recording where each tap led.

    Returns the taps spent, whether the root was still within reach at the
    end, and whether every target of the page got its turn. Every tap is made
    on a page the sweep has just verified it is on: a tap that led somewhere
    is followed by the way back, and when the root itself cannot be regained
    the page's remaining targets are given up rather than pressed from
    wherever the panel ended up. When the root is reached but the route to
    this page is not, this page is given up and the sweep goes on from the
    root. A budget that runs out is not a page given up: the budget is a
    fixed cap, spent the same way on the same menu every time.
    """
    targets = await _async_tap_targets(client, page_map, page)
    taps = 0
    for x, y in targets:
        if taps >= budget:
            break
        if await client.async_current_page() != page:
            if not await _async_return_to_root(client, page_map, root, home):
                return taps, False, False
            if page != root and not await _async_replay(client, page_map, prefix, page):
                _LOGGER.debug(
                    "The recorded route to page %s did not arrive, so the rest of its "
                    "controls were given up; the reading is not the whole menu",
                    page,
                )
                return taps, True, False
        taps += 1
        await client.async_click(page_map.get(page, []), x, y)
        landed = await client.async_current_page()
        if landed == page or landed in visited:
            continue
        await _async_collect(
            client, page_map, landed, into, visited, [*(prefix or []), (x, y)], gaps
        )
    return taps, True, True


async def _async_replay(
    client: CtcWebClient,
    page_map: dict[int, list[int]],
    route: list[tuple[int, int]] | None,
    page: int,
) -> bool:
    """Replay a recorded route from the root and say whether it arrived."""
    for x, y in route or []:
        await client.async_click(page_map.get(await client.async_current_page(), []), x, y)
    return await client.async_current_page() == page


async def _async_return_to_root(
    client: CtcWebClient,
    page_map: dict[int, list[int]],
    root: int,
    home: int | None = None,
) -> bool:
    """Get back to the operation data root from wherever a tap led.

    Stepping back is enough within the subtree, but a tap on the header can
    drop the panel all the way to the home screen. There the top right corner
    is not a back button but a button of its own (the quick menu on an i550
    Pro), so on the home screen nothing is stepped: the way in is the
    operation data tile, which the client knows how to press. A page whose
    back button did nothing at all is a dialog or something else outside the
    menu; the walk home starts with that same button, so it is not asked for.
    """
    before = await client.async_current_page()
    if before == root:
        return True
    if before != home:
        if await _async_back_to(client, page_map, root, stop_at=home):
            return True
        if await client.async_current_page() == before:
            return False
    try:
        if await client.async_goto_operation_root():
            return await client.async_current_page() == root
    except CtcWebError:
        return False
    return False


async def _async_tap_targets(
    client: CtcWebClient,
    page_map: dict[int, list[int]],
    page: int,
    exclude: Callable[[list[Widget]], Awaitable[set[tuple[int, int]]]] | None = None,
) -> list[tuple[int, int]]:
    """Return distinct points worth tapping on a page, in reading order.

    ``exclude`` is given each screen's widgets, from the same reading the
    points are taken from, and names points to leave out: the search for one
    page leaves out a control whose caption names a menu it stays out of.
    """
    seen: set[tuple[int, int, int, int]] = set()
    targets: list[tuple[int, int, int, int]] = []
    excluded: set[tuple[int, int]] = set()
    for screen in page_map.get(page, []):
        try:
            widgets = await client.async_widgets(screen)
        except CtcWebError:
            continue
        if exclude is not None:
            excluded |= await exclude(widgets)
        for widget in widgets:
            if not widget.visible or widget.width < 20 or widget.height < 14:
                continue
            if widget.x < 0 or widget.y < 0:
                continue
            if widget.y < 45:
                continue  # the header carries the clock and the back button
            if widget.width >= 460 and widget.height >= 250:
                continue  # the page background, not a control
            box = (widget.x, widget.y, widget.width, widget.height)
            if box in seen:
                continue
            seen.add(box)
            targets.append(box)
    points = [(x + w // 2, y + h // 2) for x, y, w, h in _order_targets(targets)]
    return [point for point in points if point not in excluded]


def _order_targets(
    targets: list[tuple[int, int, int, int]]
) -> list[tuple[int, int, int, int]]:
    """Try the tab strip first, then everything else.

    A row of equally sized boxes along the bottom is a tab strip, and tabs are
    what actually lead somewhere. The rest of an operation data page is usually
    the schematic, worth trying but not worth trying first: spending the tap
    budget on it is how the history page got missed.
    """
    bottom = [b for b in targets if b[1] > 200]
    widths = {b[2] for b in bottom}
    tabs = sorted(bottom, key=lambda b: b[0]) if len(bottom) >= 3 and len(widths) <= 2 else []
    rest = sorted((b for b in targets if b not in tabs), key=lambda b: (b[1], b[0]))
    return tabs + rest


async def _async_back_to(
    client: CtcWebClient,
    page_map: dict[int, list[int]],
    target: int,
    hops: int = 4,
    stop_at: int | None = None,
) -> bool:
    """Step back with the chrome button until the target page is showing.

    ``stop_at`` is the home screen, where that button is not a back button:
    arriving there without having reached the target is a failure, reported
    without pressing anything on it.
    """
    for _ in range(hops):
        here = await client.async_current_page()
        if here == target:
            return True
        if here == stop_at:
            return False
        await client.async_click(page_map.get(here, []), *CHROME_BUTTON)
        if await client.async_current_page() == here:
            return False
    return await client.async_current_page() == target


async def _async_collect(
    client: CtcWebClient,
    page_map: dict[int, list[int]],
    page: int,
    into: list[SlowPage],
    visited: set[int],
    route: list[tuple[int, int]],
    gaps: set[int] | None = None,
) -> None:
    """Describe one page and remember how it was reached.

    A page is noted in ``gaps`` when it was not read whole: a screen whose
    widgets would not come, or a caption the catalogue would not give up
    while the title or the rows were read. The page is still described and
    kept in ``into``, so the sweep goes on from it as before; the reading as
    a whole decides what a page with a gap is worth (see MenuReading).
    """
    if page in visited:
        return
    visited.add(page)
    screens = page_map.get(page, [])
    if not screens:
        return
    misses_before = int(getattr(client, "text_misses", 0) or 0)
    title = await async_page_title(client, screens)
    title_misses = int(getattr(client, "text_misses", 0) or 0) - misses_before
    reading = await async_read_page_values(client, page, screens)
    if gaps is not None and (not reading.whole or title_misses > 0):
        _LOGGER.debug(
            "Page %s was read with a gap: screens %s skipped, %s caption(s) not answered",
            page,
            reading.skipped,
            reading.text_misses + max(title_misses, 0),
        )
        gaps.add(page)
    into.append(
        SlowPage(
            page=page,
            title=title,
            screens=list(screens),
            values=reading.values,
            route=list(route),
        )
    )


async def _async_operation_root(
    client: CtcWebClient,
    page_map: dict[int, list[int]],
    origin: int,
    steps: MenuReading | None = None,
) -> tuple[int, int] | None:
    """Navigate to the operation data menu; return its page and the home screen.

    The home screen is passed on the way in, recognised by its operation data
    tile, and the sweep needs to know it: a tap in the subtree that lands there
    must not make it a page of the menu. The client remembers a home screen it
    has recognised, so asking for it first and then for the root costs no
    second press. Each step reached is noted on ``steps``.
    """
    home = await client.async_goto_home()
    if home is None:
        return None
    if steps is not None:
        steps.home_found = True
    entered = await client.async_goto_operation_root()
    if steps is not None:
        # The client says whether it found the tile; one that keeps no such
        # record is taken at its word that pressing the tile got in.
        steps.tile_found = bool(getattr(client, "tile_found", entered))
    if not entered:
        return None
    root = await client.async_current_page()
    if root == home:
        return None
    if steps is not None:
        steps.root_entered = True
    return root, home


#: How a walk back to a page by its recorded route ended (_async_put_back).
ARRIVED = "arrived"
#: The panel is on some other page, from which the chrome still steps back.
ASTRAY = "astray"
#: The panel is on a page whose back button did nothing: a dialog, or
#: something else the walk cannot leave by stepping back.
STUCK = "stuck"
#: How many times the chrome is pressed, at most, on the way back to a page.
_BACK_HOPS = 6


def _pages_by_route(
    routes: Mapping[int, list[tuple[int, int]]] | None,
) -> dict[tuple[tuple[int, int], ...], int]:
    """The page each recorded route led to, by the route: where a step of a replay should land."""
    return {tuple(route): page for page, route in (routes or {}).items()}


async def _async_replay_steps(
    client: CtcWebClient,
    page_map: dict[int, list[int]],
    here: int,
    route: list[tuple[int, int]],
    done: int,
    passed: Mapping[tuple[tuple[int, int], ...], int],
    home: int,
) -> int | None:
    """Press the rest of a route, from step ``done`` on, every landing checked.

    ``passed`` is the page each recorded route led to, by the route, so
    every prefix of this one names the page its last step reached when the
    route was recorded. A step is pressed only from a page the panel shows,
    never on the home screen, whose places are tiles and not the route's.
    The replay ends at the first step that leaves the panel where it was,
    or lands anywhere but the page that prefix led to, the home screen and
    a page outside the menu among them: the next step's place on another
    page is some other control. Where a prefix names no page, a landing on
    the home screen or on a page the replay has already passed ends it all
    the same. Returns the page the last step landed on, or None where the
    replay ended early.
    """
    seen = {here}
    for step in range(done, len(route)):
        if here == home:
            return None
        x, y = route[step]
        await client.async_click(page_map.get(here, []), x, y)
        landed = await client.async_current_page()
        expected = passed.get(tuple(route[: step + 1]))
        if (
            landed in seen
            or landed == home
            or (expected is not None and landed != expected)
        ):
            return None
        seen.add(landed)
        here = landed
    return here


async def _async_put_back(
    client: CtcWebClient,
    page_map: dict[int, list[int]],
    page: int,
    route: list[tuple[int, int]],
    root: int,
    home: int,
    routes: Mapping[int, list[tuple[int, int]]] | None = None,
) -> str:
    """Walk the panel to a page of the operation data menu by its recorded route.

    The route starts at the root, so the panel is first stepped back with
    the chrome until it shows the page itself, the root or the home screen,
    whichever comes first: a page the panel passes on its way up is not left
    for the root and walked down to again. From the home screen the way in
    is the operation data tile, and the chrome is never pressed there, since
    on the home screen that spot opens the quick menu. From the root the
    route is replayed one step at a time, every step worked out from the
    page the panel shows, and a step that leaves the panel where it was ends
    the walk: every step was recorded because it led somewhere, so the way
    is not there just now (the i550 Pro's root draws some of its tiles only
    in some operating states), and nothing is pressed on into whatever the
    next step would hit. So does a step that lands anywhere but the page it
    led to when it was recorded, which ``routes``, every page the walk
    reached by its route, says by the route's prefix (_async_replay_steps):
    another tile in the same place, in another operating state, leads
    elsewhere, and the next step there is another control. No step is
    pressed on the home screen, and no step of a recorded route is under the
    heading, since the sweep never presses there.

    Returns ARRIVED, ASTRAY or STUCK.
    """
    here = await client.async_current_page()
    seen: set[int] = set()
    while here not in (page, root, home):
        if here in seen or len(seen) >= _BACK_HOPS:
            return ASTRAY
        seen.add(here)
        await client.async_click(page_map.get(here, []), *CHROME_BUTTON)
        after = await client.async_current_page()
        if after == here:
            return STUCK
        here = after
    if here == page:
        return ARRIVED
    if here == home:
        if not await client.async_goto_operation_root():
            return ASTRAY
        here = await client.async_current_page()
        if here != root:
            return ARRIVED if here == page else ASTRAY
    passed = _pages_by_route(routes)
    passed[tuple(route)] = page
    landed = await _async_replay_steps(client, page_map, here, route, 0, passed, home)
    return ARRIVED if landed == page else ASTRAY


async def _async_restore(
    client: CtcWebClient,
    page_map: dict[int, list[int]],
    origin: int,
    found: tuple[int, int] | None,
    routes: Mapping[int, list[tuple[int, int]]] | None = None,
) -> None:
    """Put the panel back on the page it was showing before we started.

    Only with a root and a home screen to go by. Where the root was never
    found, the panel stands on a page the search could not recognise, and
    pressing its back button is exactly what the search just did without
    getting anywhere; the panel is left where it is rather than pressed blind,
    which is the home screen as a rule.

    A page the walk reached by a recorded route, ``routes`` by page with the
    root's own empty route among them, is put back the way the walk reached
    it (_async_put_back): stepped back to from below, or walked down to from
    the root, and from the home screen by the tile and the route, every step
    held to the page it led to when it was recorded. A walk back that loses
    the way steps back to the home screen and leaves the panel there, so a
    panel that started inside the menu ends where it started or on the home
    screen, never on a page in between.

    Any other page is outside the menu, or a page the walk did not reach.
    Stepping back is tried, but never on the home screen, whose top right
    button opens the quick menu, and never again on a page where the button
    did nothing. From the home screen the only known way in leads to the
    root, so that is taken only when the root is where the panel started;
    otherwise the home screen is where it stays.
    """
    try:
        here = await client.async_current_page()
        if here == origin:
            return
        if found is None:
            _LOGGER.debug(
                "The panel is left on page %s rather than put back on page %s: without "
                "the operation data menu there is no known way back",
                here,
                origin,
            )
            return
        root, home = found
        route = (routes or {}).get(origin)
        if route is not None:
            ended = await _async_put_back(
                client, page_map, origin, list(route), root, home, routes
            )
            if ended == ASTRAY:
                _LOGGER.debug(
                    "The recorded route back to page %s did not arrive, so the panel is "
                    "left on the home screen",
                    origin,
                )
                await _async_back_to(client, page_map, home, hops=_BACK_HOPS)
            return
        if here != home:
            if await _async_back_to(client, page_map, origin, hops=6, stop_at=home):
                return
            if await client.async_current_page() == here:
                return
            here = await client.async_current_page()
        if here == home and origin != root:
            return
        if (
            await client.async_goto_operation_root()
            and await client.async_current_page() == origin
        ):
            return
        await client.async_goto_home()
    except CtcWebError:
        _LOGGER.debug("Could not restore the panel to page %s", origin)


def pages_to_storage(pages: list[SlowPage]) -> list[dict[str, Any]]:
    """Serialise the catalogue so it survives a restart without rescanning."""
    return [
        {
            "page": page.page,
            "title": page.title,
            "screens": list(page.screens),
            "route": [list(step) for step in page.route],
            "values": [
                {
                    "key": value.key,
                    "label": value.label,
                    "screen": value.screen,
                    "fmt": value.fmt,
                    "vars": list(value.var_indices),
                    "unit": value.unit,
                    "scale": value.scale,
                    # Only where the row has moved: a release that does not
                    # know the field reads past it (roadmap L2).
                    **({"previous_key": value.previous_key} if value.previous_key else {}),
                }
                for value in page.values
            ],
        }
        for page in pages
    ]


def pages_from_storage(stored: list[dict[str, Any]] | None) -> list[SlowPage]:
    """Rebuild the catalogue saved by :func:`pages_to_storage`."""
    pages: list[SlowPage] = []
    for item in stored or []:
        try:
            page = SlowPage(
                page=int(item["page"]),
                title=str(item.get("title", "")),
                screens=[int(s) for s in item.get("screens", [])],
                route=[(int(step[0]), int(step[1])) for step in item.get("route", [])],
            )
            for raw in item.get("values", []):
                page.values.append(
                    SlowValue(
                        key=str(raw["key"]),
                        label=str(raw.get("label", raw["key"])),
                        page=page.page,
                        screen=int(raw["screen"]),
                        fmt=str(raw.get("fmt", "%d")),
                        var_indices=[int(i) for i in raw.get("vars", [])],
                        unit=raw.get("unit"),
                        scale=float(raw.get("scale", 1.0)),
                        previous_key=(
                            raw["previous_key"]
                            if isinstance(raw.get("previous_key"), str) and raw["previous_key"]
                            else None
                        ),
                    )
                )
            pages.append(page)
        except (KeyError, TypeError, ValueError) as err:
            _LOGGER.debug("Discarding a stored page: %s", err)
    return pages


def live_page_among(stored: Any, pages: Iterable[SlowPage]) -> int | None:
    """The stored live page, where it is one of ``pages``, else None.

    The option holds a page number, None, or nothing at all on an entry from
    before it existed; a page that is no longer harvested, unticked or gone
    from the menu, is no page for the panel to rest on. One rule for set-up,
    the coordinator and the forms, so none of them parks the panel on a page
    the others do not read.
    """
    if isinstance(stored, bool) or not isinstance(stored, int):
        return None
    return stored if any(page.page == stored for page in pages) else None


def live_page_chosen(raw: Any, pages: Iterable[SlowPage]) -> int | None:
    """The live page a form chose: one of ``pages``, or None for none or a page not offered.

    The select hands over the page's number as text, or LIVE_PAGE_NONE; a
    form without the select hands over nothing.
    """
    if raw in (None, "", LIVE_PAGE_NONE):
        return None
    try:
        return live_page_among(int(raw), pages)
    except (TypeError, ValueError):
        return None


def live_interval_chosen(raw: Any, fallback: int) -> int:
    """The live interval a form chose, held to the floor, or ``fallback`` where it chose none."""
    if raw is None:
        return fallback
    try:
        return max(MIN_LIVE_INTERVAL, int(raw))
    except (TypeError, ValueError):
        return fallback


def menu_is_due(stored_version: str | None, version: str, tries: int, limit: int) -> bool:
    """Whether the whole menu is worth reading again, and whether a try is left.

    The stored menu was parsed by whichever version read it, so a newer one reads
    it again: a newer parser makes sense of rows and pages the older one passed
    over. A display that was busy for a moment is worth another try, but the
    panel is a physical thing, so the tries are counted and they stop.
    """
    return stored_version != version and tries < limit


def menu_wait(last: float | None, now: float, interval: float) -> float:
    """Seconds left of the pause between two readings of the menu.

    On the clock rather than in a sleep, because writing the options reloads the
    entry: a pause that lived only in a sleeping task would be skipped by the very
    reload that a successful identity reading causes, and the panel would be walked
    twice in a row.
    """
    if last is None:
        return 0.0
    return max(0.0, interval - (now - last))


def keep_unreached(
    stored: list[SlowPage],
    discovered: list[SlowPage],
    keep: Callable[[SlowPage], bool] | None = None,
) -> list[SlowPage]:
    """The pages a reading found, with the stored pages it did not reach that still belong.

    Pages the reading found come with the definitions it brought, in the
    stored order, and pages the menu has not had before come last. A stored
    page the reading did not reach stays in its place when ``keep`` says it
    still belongs, or always without one, which is how an interrupted
    reading is offered: what it did not get to is not evidence of anything.
    """
    fresh_by_page = {page.page: page for page in discovered}
    known = {page.page for page in stored}
    menu = [
        fresh_by_page.get(page.page, page)
        for page in stored
        if page.page in fresh_by_page or keep is None or keep(page)
    ]
    menu += [page for page in discovered if page.page not in known]
    return menu


def still_offered(
    selection: Iterable[int],
    root: int | None,
    screen_pages: frozenset[int] | None,
) -> Callable[[SlowPage], bool]:
    """Whether a stored page a whole reading did not reach still belongs in the menu.

    A sweep that ran to its end has still not seen every page the menu has:
    the i550 Pro's operation data root is a schematic picture, and it draws
    the tile that leads to Kompressor VP1 only in some operating states. A
    whole reading taken while PT charged hot water (2026-10-13) did not reach
    the page, and folding that reading in as the menu took the page off the
    harvest and out of the options, with its 35 rows left unprovided.

    So a page the reading did not reach stays, ticked as it was, when it is
    ticked (unticking it is how the owner lets it go: the next whole reading
    that does not reach it drops it), when it was reached by a recorded route
    or is the root itself (a page with neither was only the page the panel
    showed when the set-up could not find the menu), and when the display's
    screen map still has its number (a page the display's software no longer
    has is gone, not hidden). A harvest that cannot reach a kept page gives
    it up after the first tap that does not move the panel, says so among its
    missed pages, and lets its rows go unavailable.
    """
    ticked = set(selection)

    def keep(page: SlowPage) -> bool:
        return (
            page.page in ticked
            and (bool(page.route) or page.page == root)
            and (screen_pages is None or page.page in screen_pages)
        )

    return keep


def menu_after_rescan(
    stored_menu: list[SlowPage],
    stored_selection: list[SlowPage],
    discovered: list[SlowPage],
    complete: bool = True,
    root: int | None = None,
    screen_pages: frozenset[int] | None = None,
) -> tuple[list[SlowPage], bool]:
    """The menu to offer after "read the menu again", and whether it is fresh.

    A reading that gave nothing is not a menu with no pages on it, it is a
    reading that did not happen: the display was busy, or the operation data
    menu could not be found. Writing it would wipe the menu an earlier reading
    built, and every tick box with it. So the stored menu stands, and since it
    is not fresh the version stamp is left alone and the reading stays owed.
    An installation from before the whole menu was kept has only its harvested
    pages to fall back on.

    A reading that was interrupted is offered for what it found, with its new
    definitions, but the stored pages it did not reach stand beside it in
    their old place, so saving the form cannot make a page disappear. It is
    not fresh either: the version stays unstamped and the reading owed. A
    whole reading is fresh, and keeps a stored page it did not reach as long
    as that page still belongs (see still_offered).
    """
    stored = union_by_page(stored_menu, stored_selection)
    if discovered and complete:
        keep = still_offered([page.page for page in stored_selection], root, screen_pages)
        return keep_unreached(stored, discovered, keep), True
    return keep_unreached(stored, discovered), False


def merge_menu(
    previous_menu: list[SlowPage],
    previous_selection: list[int],
    discovered: list[SlowPage],
    root: int | None = None,
    screen_pages: frozenset[int] | None = None,
) -> tuple[list[SlowPage], list[int]]:
    """Fold a whole reading of the menu into what the user has already chosen.

    A page switched off stays off, because that was a deliberate choice: every
    harvested page means the panel is walked there and back. Anything the menu
    has not offered before starts on, so a page a new version can use is used
    without a visit to the options, and the first reading switches everything on.
    A stored page the reading did not reach stays, ticked as it was, while it
    still belongs (see still_offered).
    """
    known = {page.page for page in previous_menu}
    switched_off = known - set(previous_selection)
    keep = still_offered(previous_selection, root, screen_pages)
    menu = keep_unreached(previous_menu, discovered, keep)
    selected = [page.page for page in menu if page.page not in switched_off]
    return menu, selected
