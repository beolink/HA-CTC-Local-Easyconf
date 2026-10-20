"""Client for the CTC display's built in web server ("screen mirror").

The display serves the same interface that myUplink's remote view proxies under
``/remoteweb/disp``. It is undocumented, so everything here was derived from the
JavaScript the display itself serves.

Endpoints
    GET  /main.html            entry point, the root path answers 400
    GET  /sm/all               page map, ``page:screen|screen|...|page:...``
    GET  /wp/<n>               screen definition, always gzipped
    GET  /vars/<n>             values for screen n
    GET  /vars/glob            date and time
    GET  /vars/menu            first field is the page the panel is showing
    GET  /txt/<lang>/<id>      one label in one language
    GET  /settings/name        settings file name, encodes the model family
    POST /click/<path>         body "x,y"
    POST /scroll/<path>        body "<delta>"

The path for click and scroll is a list, ``glob;menu;<screen>;<screen>;...``,
naming the screens the answer should contain. The answer has one line per list
entry separated by carriage returns.

Two properties matter for anything built on this:

* Only the page the panel is currently showing is kept up to date. Every other
  screen returns a frozen snapshot from the last time it was rendered. Asking
  for another page's screens in the click path does not refresh them.
* Navigating moves the physical panel. There is one shared display state and no
  second session; ``/click2/`` and ``/scroll2/`` exist in the JavaScript but the
  firmware answers 400 to them.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
import contextlib
from dataclasses import dataclass
import gzip
import json
import logging
from typing import Any, NamedTuple

import aiohttp

from .const import (
    CHROME_BUTTON,
    HEADER_ICON_MAX_X,
    PAGE_HEADER_HEIGHT,
    ROW_TOLERANCE,
    STATUS_CAPTIONS,
    WEB_MAX_CONCURRENCY,
)

_LOGGER = logging.getLogger(__name__)

REQUEST_TIMEOUT = aiohttp.ClientTimeout(total=10)
#: A second chance for a reading that was merely slow. The display is a small
#: embedded server and sometimes takes its time; a tap, on the other hand, is
#: never repeated, because a tap that did land would move the panel twice.
PATIENT_TIMEOUT = aiohttp.ClientTimeout(total=25)

#: The panel is 480 by 272. A tap outside it is a verified no-operation, used
#: to fetch screens without pressing anything.
PANEL_WIDTH = 480
PANEL_HEIGHT = 272


class CtcWebError(Exception):
    """Raised when the display's web server cannot be used."""


class AlarmReading(NamedTuple):
    """What one screen says about an alarm: where the panel could print one, and what it prints.

    ``carries`` is whether the screen has a place the panel fills with an alarm
    text, a header icon or a status field picked by a variable, whatever the
    place shows now; the alarm watch needs that to tell a page that can show an
    alarm from one that cannot. ``texts`` are the texts printed in those places
    at the moment, the visible ones only.
    """

    carries: bool
    texts: list[str]


@dataclass
class Widget:
    """One drawable element on a screen, with its resolved geometry."""

    index: int
    kind: int
    x: int
    y: int
    width: int
    height: int
    visible: bool
    label: str | None = None
    value_fmt: str | None = None
    value_vars: list[int] | None = None
    #: Content of a string element (widget kind 5). The panel uses these for the
    #: serial number, the MAC address and the firmware versions, which are
    #: written once and therefore readable whatever page the panel is showing.
    text_value: str | None = None
    #: The catalogue entry ``label`` was read from, kept so the same caption can
    #: be looked up in another language without resolving the screen again.
    text_id: int | None = None

    @property
    def centre(self) -> tuple[int, int]:
        return self.x + self.width // 2, self.y + self.height // 2

    def overlaps_horizontally(self, other: Widget) -> bool:
        return self.x < other.x + other.width and other.x < self.x + self.width


@dataclass
class ScreenDef:
    """A parsed screen definition from ``/wp/<n>``."""

    index: int
    c0: list[int]
    c1: list[list[Any]]
    v0: list[int]
    t0: list[int]
    t1: list[list[Any]]
    t2: list[list[Any]]


def _balanced_array(source: str, name: str) -> str | None:
    """Return the literal JavaScript array assigned to ``name``."""
    start = source.find(name + "=")
    if start < 0:
        return None
    open_at = source.find("[", start)
    if open_at < 0:
        return None
    depth = 0
    in_string = False
    escaped = False
    pos = open_at
    while pos < len(source):
        char = source[pos]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
        elif char == '"':
            in_string = True
        elif char == "[":
            depth += 1
        elif char == "]":
            depth -= 1
            if depth == 0:
                return source[open_at : pos + 1]
        pos += 1
    return None


def _parse_array(source: str, name: str) -> list:
    raw = _balanced_array(source, name)
    if raw is None:
        return []
    try:
        return json.loads(raw)
    except ValueError:
        return []


def parse_vars(payload: str) -> list[Any]:
    """Split a ``/vars/`` payload into ints, keeping quoted strings as text."""
    out: list[Any] = []
    for field in payload.strip().split("|"):
        if field == "":
            continue
        if field.startswith('"'):
            out.append(field.strip('"'))
            continue
        try:
            out.append(int(field))
        except ValueError:
            out.append(field)
    return out


def tap_target(widgets: list[Widget], label_widget: Widget) -> tuple[int, int]:
    """Return a point that actually presses the tile a caption belongs to.

    Home screen tiles are an icon with the caption drawn underneath it as a
    separate element. Matching finds the caption, but its own centre can fall
    outside the touch area, so the icon sitting directly above it is preferred.
    """
    candidates = [
        w
        for w in widgets
        if w is not label_widget
        and w.visible
        and w.kind in (0, 1)
        and w.width > 20
        and w.height > 20
        and w.y + w.height <= label_widget.y + 4
        and w.overlaps_horizontally(label_widget)
    ]
    if candidates:
        icon = max(candidates, key=lambda w: w.y)
        return icon.centre
    return label_widget.centre


def _select_from_group(group: Any, selector: int) -> tuple[str, int] | None:
    """Pick one alternative out of a t1 group.

    A group is a flat list of alternatives. Codes 0, 1 and 2 occupy three slots
    and point at a format entry, codes 3 and 4 occupy two and hold a text id and
    a format index respectively. ``selector`` counts alternatives, not slots.
    """
    if not isinstance(group, list):
        return None
    pos = 0
    ordinal = 0
    while pos < len(group):
        code = group[pos]
        if ordinal == selector:
            if code in (0, 1, 2) and pos + 2 < len(group):
                return ("fmt", group[pos + 2])
            if code == 3 and pos + 1 < len(group):
                return ("text", group[pos + 1])
            if code == 4 and pos + 1 < len(group):
                return ("fmt", group[pos + 1])
            return None
        pos += 3 if code in (0, 1, 2) else 2
        ordinal += 1
    return None


def _var_resolver(values: list[Any], globals_: list[Any]) -> Callable[[int, int], int]:
    """Build the lookup a screen definition's references are read through.

    Kind 0 reads the global variables, kind 1 the screen's own, and anything
    else is the literal value. A slot that is missing or holds text reads as -1,
    the same marker the display uses for nothing.
    """

    def var_value(kind: int, val: int) -> int:
        if kind == 0:
            got = globals_[val] if val < len(globals_) else -1
        elif kind == 1:
            got = values[val] if val < len(values) else -1
        else:
            return val
        return got if isinstance(got, int) else -1

    return var_value


def _picked_entry(
    definition: ScreenDef, entry: list, kind: int, var_value: Callable[[int, int], int]
) -> tuple[str, int] | None:
    """Follow a widget's text array index through t0 and t1.

    Images carry the index at position 10, text elements at position 12, and
    other widget kinds carry none. The result is ``("text", text id)`` or
    ``("fmt", t2 index)``, or None when the chain runs off the end of a table.
    """
    if kind in (0, 1):
        slot_pos = 10
    elif kind in (2, 3):
        slot_pos = 12
    else:
        return None
    if slot_pos >= len(entry) or not isinstance(entry[slot_pos], int):
        return None
    base = entry[slot_pos] * 3
    if base + 2 >= len(definition.t0):
        return None
    selector = var_value(definition.t0[base], definition.t0[base + 1])
    group_index = definition.t0[base + 2]
    if not isinstance(group_index, int) or group_index >= len(definition.t1):
        return None
    return _select_from_group(definition.t1[group_index], selector)


def _text_slot(entry: list, kind: int) -> int | None:
    """Where a widget keeps its text array index: images at 10, texts at 12."""
    if kind in (0, 1):
        slot_pos = 10
    elif kind in (2, 3):
        slot_pos = 12
    else:
        return None
    if slot_pos >= len(entry) or not isinstance(entry[slot_pos], int):
        return None
    return entry[slot_pos]


def _selector_kind(definition: ScreenDef, entry: list, kind: int) -> int | None:
    """What picks a widget's text: 0 a global variable, 1 one of the screen's
    own, 2 a literal. A text picked by a variable changes with the plant, as the
    status field and the header icon do; a literal is a caption."""
    slot = _text_slot(entry, kind)
    if slot is None:
        return None
    base = slot * 3
    if base >= len(definition.t0):
        return None
    return definition.t0[base]


def _primary_variant(spec: Any) -> tuple[str | None, list[int]]:
    """Return the normal format string of a t2 entry and the vars it reads.

    A t2 entry is a run of triples: format string, the selector value that picks
    it, and the list of variable references. The first triple is the normal
    case; the later ones are the "no sensor fitted" renderings.
    """
    if not isinstance(spec, list) or not spec or not isinstance(spec[0], str):
        return None, []
    fmt = spec[0]
    indices: list[int] = []
    refs = spec[2] if len(spec) > 2 else None
    if isinstance(refs, list):
        for i in range(0, len(refs) - 1, 2):
            if refs[i] == 1 and isinstance(refs[i + 1], int):
                indices.append(refs[i + 1])
    return fmt, indices


def parse_screen_map(payload: str) -> dict[int, list[int]]:
    """Parse ``/sm/all`` into ``{page: [screen, ...]}``."""
    pages: dict[int, list[int]] = {}
    current: int | None = None
    for token in payload.strip().split("|"):
        token = token.strip()
        if not token:
            continue
        if ":" in token:
            page_text, first = token.split(":", 1)
            try:
                current = int(page_text)
            except ValueError:
                current = None
                continue
            pages[current] = []
            if first:
                with contextlib.suppress(ValueError):
                    pages[current].append(int(first))
        elif current is not None:
            with contextlib.suppress(ValueError):
                pages[current].append(int(token))
    return pages


class CtcWebClient:
    """Read, and when asked navigate, the CTC display over HTTP."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        host: str,
        port: int = 80,
        language: int = 1,
    ) -> None:
        self._session = session
        self._host = host
        self._port = port
        self._language = language
        self._semaphore = asyncio.Semaphore(WEB_MAX_CONCURRENCY)
        #: Held while the panel is being walked. The harvester and the walk to
        #: the system information page both move the physical display, and two
        #: of them at once would leave it somewhere neither expects. A lock of
        #: the client's own until somebody lends it another: a loaded entry
        #: puts its own in its place at every set-up, one lock for every
        #: client its reloads build, so a walk on the client of a runtime that
        #: has since been reloaded still holds the new one's walks back. The
        #: client the options build for an entry that is not loaded is lent
        #: the entry's lock as well; one the set-up flow builds keeps its own.
        self.panel = asyncio.Lock()
        self._screen_cache: dict[int, ScreenDef] = {}
        self._text_cache: dict[int, str] = {}
        self._screen_map: dict[int, list[int]] | None = None
        #: The home screen's page, once it has been recognised. The display's
        #: page numbers do not change, so a page recognised as home once is
        #: home for the rest of the run, even in a moment when its tile's
        #: caption cannot be read; see async_goto_home.
        self._home: int | None = None
        #: The operation data tile per home page, once found: which screen it
        #: is on, the page's screens, and where to press. Static on a given
        #: panel, and finding it costs the home page's every widget and label,
        #: which every harvest used to pay again for every page it visited.
        self._tiles: dict[int, tuple[int, list[int], tuple[int, int]]] = {}
        #: The operation data root, the page the tile leads to: given from the
        #: stored menu where it is known, otherwise learnt the first time the
        #: tile is pressed in this run. Between two pages of a harvest the
        #: panel steps back to it and the number is checked before a route is
        #: replayed, so a route is only ever replayed from the page it was
        #: recorded on. See async_goto_page.
        self.root: int | None = None
        #: Whether the last attempt to enter the operation data root found the
        #: tile on the home screen: None before any attempt and when the home
        #: screen itself was not found. The menu sweep reports it (roadmap L6),
        #: since a tile that is not there and a tile that leads nowhere are
        #: two different faults behind the same False.
        self.tile_found: bool | None = None
        #: How many requests this client has made, and how many of them were
        #: taps that moved the panel. Cumulative; the harvester logs the
        #: difference per walk, which is how a walk that costs too much is seen.
        self.requests = 0
        self.taps = 0
        #: How many captions the catalogue would not give up, counted where
        #: async_text swallows the failure. Cumulative, like the two above;
        #: the menu sweep reads it before and after a page, since a caption
        #: that read as nothing renames the row it belonged to, and a page
        #: read that way is not a reading of the page.
        self.text_misses = 0

    @property
    def base_url(self) -> str:
        return f"http://{self._host}:{self._port}"

    async def _request(self, path: str, body: str | None = None) -> str:
        """Perform one request, decompressing when the server gzips regardless.

        A reading that was slow or dropped is asked for once more, with longer
        patience. A tap is asked for only once: a tap that landed and then
        looked like a failure would move the panel a second time.
        """
        url = f"{self.base_url}{path}"
        timeouts = (REQUEST_TIMEOUT, PATIENT_TIMEOUT) if body is None else (REQUEST_TIMEOUT,)
        async with self._semaphore:
            for attempt, timeout in enumerate(timeouts, start=1):
                self.requests += 1
                try:
                    if body is None:
                        response = await self._session.get(url, timeout=timeout)
                    else:
                        response = await self._session.post(
                            url, data=body.encode(), timeout=timeout
                        )
                    async with response:
                        if response.status != 200:
                            raise CtcWebError(f"{path} answered HTTP {response.status}")
                        raw = await response.read()
                    break
                except (TimeoutError, aiohttp.ClientError) as err:
                    if attempt < len(timeouts):
                        _LOGGER.debug("%s was slow, asking once more: %s", path, err)
                        continue
                    if isinstance(err, asyncio.TimeoutError):
                        raise CtcWebError(f"{path} timed out") from err
                    raise CtcWebError(f"{path} failed: {err}") from err
        if raw[:2] == b"\x1f\x8b":
            raw = gzip.decompress(raw)
        return raw.decode("utf-8", "replace").lstrip("﻿")

    # ---------------------------------------------------------------- probing

    async def async_settings_name(self) -> str:
        """Return the settings file name, e.g. ``settings_ezi255.bin``."""
        return (await self._request("/settings/name")).strip()

    async def async_probe(self) -> str:
        """Confirm this host is a CTC display and return its model family."""
        name = await self.async_settings_name()
        if not name.startswith("settings_") or not name.endswith(".bin"):
            raise CtcWebError(f"unexpected settings name {name!r}")
        return name

    # ------------------------------------------------------------------ reads

    async def async_screen_map(self, refresh: bool = False) -> dict[int, list[int]]:
        if self._screen_map is None or refresh:
            self._screen_map = parse_screen_map(await self._request("/sm/all"))
        return self._screen_map

    async def async_vars(self, screen: int | str) -> list[Any]:
        return parse_vars(await self._request(f"/vars/{screen}"))

    async def async_current_page(self) -> int:
        """Return the page the physical panel is showing right now."""
        values = await self.async_vars("menu")
        if not values or not isinstance(values[0], int):
            raise CtcWebError("could not read the current page")
        return values[0]

    async def async_text(self, text_id: int, language: int | None = None) -> str:
        """One label out of the display's text catalogue, or "" if it would not say.

        Only an answer is kept. A failure used to be cached as "" as well, for
        the rest of the run: the home screen is recognised by the English
        caption "Operation data", read through this very cache, so a label that
        was slow twice during one bad minute left every harvest after it unable
        to find home until Home Assistant was restarted. The failure is still
        swallowed rather than raised, because one label must not take a whole
        screen with it; it is simply asked for again next time.
        """
        lang = self._language if language is None else language
        cache_key = text_id if language is None else -(text_id * 100 + language)
        cached = self._text_cache.get(cache_key)
        if cached is not None:
            return cached
        try:
            value = (await self._request(f"/txt/{lang}/{text_id}")).strip()
        except CtcWebError as err:
            _LOGGER.debug("Text %s in language %s was not answered: %s", text_id, lang, err)
            self.text_misses += 1
            return ""
        self._text_cache[cache_key] = value
        return value

    async def async_screen_def(self, screen: int) -> ScreenDef:
        if screen in self._screen_cache:
            return self._screen_cache[screen]
        source = await self._request(f"/wp/{screen}")
        definition = ScreenDef(
            index=screen,
            c0=_parse_array(source, f"p{screen}c0"),
            c1=_parse_array(source, f"p{screen}c1"),
            v0=_parse_array(source, f"p{screen}v0"),
            t0=_parse_array(source, f"p{screen}t0"),
            t1=_parse_array(source, f"p{screen}t1"),
            t2=_parse_array(source, f"p{screen}t2"),
        )
        self._screen_cache[screen] = definition
        return definition

    # -------------------------------------------------------------- rendering

    async def async_widgets(
        self, screen: int, values: list[Any] | None = None, globals_: list[Any] | None = None
    ) -> list[Widget]:
        """Resolve a screen's widgets, with geometry and labels."""
        definition = await self.async_screen_def(screen)
        if values is None:
            values = await self.async_vars(screen)
        if globals_ is None:
            globals_ = await self.async_vars("glob")
        var_value = _var_resolver(values, globals_)

        def ref(index: int) -> int:
            if index * 2 >= len(definition.v0):
                return -1
            return var_value(definition.v0[index * 2], definition.v0[index * 2 + 1])

        def raw_ref(index: int) -> str | None:
            """Resolve a reference without forcing it to a number.

            Geometry is always numeric, but a string element points at a text
            variable, and coercing that to an integer would throw the content
            away.
            """
            if index * 2 >= len(definition.v0):
                return None
            kind, val = definition.v0[index * 2], definition.v0[index * 2 + 1]
            if kind == 0:
                got = globals_[val] if val < len(globals_) else None
            elif kind == 1:
                got = values[val] if val < len(values) else None
            else:
                got = val
            if got is None or got == "":
                return None
            return str(got)

        widgets: list[Widget] = []
        for order, entry in enumerate(definition.c1):
            if not isinstance(entry, list) or len(entry) < 9:
                continue
            widget = Widget(
                index=order,
                kind=entry[0],
                x=ref(entry[2]),
                y=ref(entry[3]),
                width=ref(entry[4]),
                height=ref(entry[5]),
                visible=ref(entry[8]) != 0,
            )
            # A string element keeps its content where the others keep their
            # text array index, and it is resolved the same way the geometry is.
            if widget.kind == 5:
                if len(entry) > 12 and isinstance(entry[12], int):
                    widget.text_value = raw_ref(entry[12])
                widgets.append(widget)
                continue

            picked = _picked_entry(definition, entry, widget.kind, var_value)
            if picked is None:
                widgets.append(widget)
                continue
            kind, payload = picked
            if kind == "text":
                widget.text_id = payload
                widget.label = await self.async_text(payload)
            elif kind == "fmt" and payload < len(definition.t2):
                spec = definition.t2[payload]
                fmt, indices = _primary_variant(spec)
                if fmt is not None:
                    widget.value_fmt = fmt
                    widget.value_vars = indices
            widgets.append(widget)
        return widgets

    async def async_alarm_readout(self, screen: int, values: list[Any]) -> AlarmReading:
        """What a screen says about an alarm, read from fresh values.

        Two places, and only where the panel picks the text by a variable. The
        header icon at the top left, whose caption the panel picks from a
        variable: on the history page of an i550 Pro it resolved to "[E017]
        Givare solpaneler ut". And the status field, the text the panel fills
        from a variable on the row captioned Status. A caption the screen
        definition fixes is a label of its icon and not a reading: VSH's panel
        has "[E050] Stopp, låg överhettning exp.ventil" fixed on the header
        icon of a heat pump page, and "[E195] Driver: 209" fixed on the start
        page, where no alarm shows. Nothing else on the screen is resolved, so
        a page that lists past alarms cannot pass one of them off as current.

        This moves nothing and reads nothing off the panel: the screen
        definition is fetched once a run and cached, the captions come through
        the text cache, and the values are the ones the harvest has just read.
        The globals are not fetched for this, so a text picked by a global
        variable reads as none; no screen captured so far picks anything that
        way. ``carries`` counts a place whatever its visibility now, since the
        panel may show the icon or the field on another reading.
        """
        definition = await self.async_screen_def(screen)
        var_value = _var_resolver(values, [])

        def ref(index: int) -> int:
            if index * 2 >= len(definition.v0):
                return -1
            return var_value(definition.v0[index * 2], definition.v0[index * 2 + 1])

        # Header icons as (x, text id, visible); the texts beside a row as
        # (x, y, text id, visible) when the panel picks them by a variable, and
        # the captions a row may carry the same way.
        header: list[tuple[int, int, bool]] = []
        dynamic: list[tuple[int, int, int, bool]] = []
        captions: list[tuple[int, int, int, bool]] = []
        for entry in definition.c1:
            if not isinstance(entry, list) or len(entry) < 9:
                continue
            kind = entry[0]
            if kind not in (0, 1, 2, 3):
                continue
            picked = _picked_entry(definition, entry, kind, var_value)
            if picked is None or picked[0] != "text":
                continue
            visible = ref(entry[8]) != 0
            x, y, width = ref(entry[2]), ref(entry[3]), ref(entry[4])
            by_variable = _selector_kind(definition, entry, kind) in (0, 1)
            if kind in (0, 1):
                if by_variable and 0 <= y < PAGE_HEADER_HEIGHT and 0 <= x < HEADER_ICON_MAX_X:
                    header.append((x, picked[1], visible))
                continue
            if y < PAGE_HEADER_HEIGHT or width <= 0:
                continue
            if by_variable:
                dynamic.append((x, y, picked[1], visible))
            else:
                captions.append((x, y, picked[1], visible))

        # A variable text is the status field where the caption to its left,
        # on the same row, reads Status. Its own visibility decides whether it
        # is read now; the row it sits on decides whether it carries.
        status: list[tuple[int, bool]] = []
        for x, y, text_id, visible in sorted(dynamic, key=lambda item: (item[1], item[0])):
            for caption_x, caption_y, caption_id, caption_visible in captions:
                if caption_x >= x or abs(caption_y - y) > ROW_TOLERANCE:
                    continue
                if (await self.async_text(caption_id)).strip().casefold() in STATUS_CAPTIONS:
                    status.append((text_id, visible and caption_visible))
                    break

        texts: list[str] = []
        for _x, text_id, visible in sorted(header):
            if visible:
                texts.append(await self.async_text(text_id))
        for text_id, shown in status:
            if shown:
                texts.append(await self.async_text(text_id))
        return AlarmReading(bool(header or status), [text for text in texts if text])

    async def async_english_label(
        self,
        screen: int,
        widget: Widget,
        values: list[Any] | None = None,
        globals_: list[Any] | None = None,
    ) -> str | None:
        """Resolve a widget's label in English, which is model independent.

        Text ids differ between models, 532 on an i255 and 570 on an i550 Pro for
        the same menu item, so navigation matches on the English string instead.

        A widget rendered by :meth:`async_widgets` carries the text id it was
        drawn with, so this is one lookup in the text cache and no second
        reading of the screen's variables. Those used to be fetched again for
        every widget, forty to sixty requests per hop to the home screen against
        a server that drops connections above five in flight. The variables are
        only needed for a widget built some other way, and a caller that has
        them passes them in rather than have them fetched.
        """
        text_id = widget.text_id
        if text_id is None:
            text_id = await self._async_text_id(screen, widget, values, globals_)
        if text_id is None:
            return None
        return await self.async_text(text_id, language=0)

    async def _async_text_id(
        self,
        screen: int,
        widget: Widget,
        values: list[Any] | None,
        globals_: list[Any] | None,
    ) -> int | None:
        """Work out which catalogue entry a widget's caption comes from."""
        definition = await self.async_screen_def(screen)
        if widget.index >= len(definition.c1):
            return None
        entry = definition.c1[widget.index]
        if not isinstance(entry, list):
            return None
        if values is None:
            values = await self.async_vars(screen)
        if globals_ is None:
            globals_ = await self.async_vars("glob")
        picked = _picked_entry(definition, entry, widget.kind, _var_resolver(values, globals_))
        if picked is None or picked[0] != "text":
            return None
        return picked[1]

    # ------------------------------------------------------------- navigation

    def _click_path(self, screens: list[int]) -> str:
        return ";".join(["glob", "menu", *(str(s) for s in screens)])

    async def async_click(self, screens: list[int], x: int, y: int) -> list[list[Any]]:
        """Send a tap. This moves the physical panel."""
        if 0 <= x < PANEL_WIDTH and 0 <= y < PANEL_HEIGHT:
            self.taps += 1
        payload = await self._request(f"/click/{self._click_path(screens)}", f"{x},{y}")
        return [parse_vars(line) for line in payload.split("\r")]

    async def async_click_noop(self, screens: list[int]) -> list[list[Any]]:
        """Fetch the listed screens using a tap that cannot hit anything.

        The coordinate is outside the 480 by 272 panel, which is a verified no
        operation: the current page is unchanged afterwards.
        """
        return await self.async_click(screens, 9999, 9999)

    async def async_goto_page(
        self,
        target: int,
        route: list[tuple[int, int]] | None = None,
    ) -> bool:
        """Walk the panel to ``target``.

        With a ``route`` recorded during setup the taps are simply replayed from
        the operation data root, which is reliable across models. Without one the
        only thing attempted is stepping back, which is enough to restore the
        page the panel started on.

        The root is regained the short way where it can be: from a page of the
        operation data subtree, which is where one harvested page leaves the
        panel for the next, a step back or two lands on the root, and its
        number says so. Only when that fails is the long way taken, over the
        home screen and its tile, which costs the home page's widgets and
        labels on every page passed. The root page itself, where it is
        harvested, is reached the same two ways; the back button is never
        pressed on the home screen for it, where that button is the quick menu.
        """
        page_map = await self.async_screen_map()
        if target not in page_map:
            raise CtcWebError(f"page {target} is not in the screen map")
        if await self.async_current_page() == target:
            return True

        if route:
            if (
                not await self._async_back_to_root()
                and not await self.async_goto_operation_root()
            ):
                return False
            # Every step of a route was recorded because it led to a new page,
            # so a step that leaves the panel where it was means the way is
            # not there just now: the i550 Pro's root draws some tiles only in
            # some operating states. The walk ends there, one tap spent, and
            # does not press on into whatever the next step would hit. So
            # does a step that lands on the home screen, on the root or on a
            # page this walk has passed already, none of which a recorded
            # route ever leads to: another tile in the same place leads
            # elsewhere just now, and the next step there is another control.
            # The pages in between are not stored with the route, so a step
            # that lands on some other page of the menu is not told from one
            # that arrived until the walk ends.
            here = await self.async_current_page()
            passed = {here}
            for x, y in route:
                await self.async_click(page_map.get(here, []), x, y)
                landed = await self.async_current_page()
                if landed in passed or landed in (self._home, self.root):
                    return False
                passed.add(landed)
                here = landed
            return here == target

        if target == self.root:
            if await self._async_back_to_root():
                return True
            return (
                await self.async_goto_operation_root()
                and await self.async_current_page() == target
            )

        return await self.async_step_back_to(target)

    async def _async_back_to_root(self, hops: int = 4) -> bool:
        """Step back to the operation data root, when it and the home screen are known.

        The number is checked after every step, so a route is only ever replayed
        from the page it was recorded on. Nothing is pressed on the home screen,
        whose top right button is not a back button, and a press that changed
        nothing ends the attempt; the caller then takes the long way round.
        """
        root, home = self.root, self._home
        if root is None or home is None:
            return False
        page_map = await self.async_screen_map()
        seen: set[int] = set()
        for _ in range(hops):
            here = await self.async_current_page()
            if here == root:
                return True
            if here == home or here in seen:
                return False
            seen.add(here)
            await self.async_click(page_map.get(here, []), *CHROME_BUTTON)
            if await self.async_current_page() == here:
                return False
        return await self.async_current_page() == root

    async def async_step_back_to(self, target: int, hops: int = 6) -> bool:
        """Press the chrome's back button until ``target`` is showing.

        The button at the top right steps back inside a submenu, but on the home
        screen the same spot is a button of its own, the quick menu on an i550
        Pro. So a home screen this client has recognised ends the walk without
        a press, as it does in _async_back_to_root and async_goto_home: a
        target that lies above the home screen, a page somebody parked the
        panel on before the harvest went ahead anyway, is not reached this
        way, and the caller leaves the panel on the home screen instead.
        Revisiting a page means backing out is going in circles, and the walk
        stops there too.
        """
        page_map = await self.async_screen_map()
        seen: set[int] = set()
        for _ in range(hops):
            here = await self.async_current_page()
            if here == target:
                return True
            if here == self._home or here in seen:
                return False
            seen.add(here)
            await self.async_click(page_map.get(here, []), *CHROME_BUTTON)
            if await self.async_current_page() == here:
                return False
        return await self.async_current_page() == target

    async def _async_operation_tile(
        self, page: int
    ) -> tuple[int, list[int], tuple[int, int]] | None:
        """Find the operation data tile on ``page``, if it is there.

        Remembered per page once found: the tile does not move, and finding it
        means reading every widget and label of the page. A page where it is
        not found is not remembered as such, since a label that was slow for a
        moment reads as nothing (see async_text), and the next look may find it.
        """
        from .const import OPERATION_DATA_LABEL_EN

        found = self._tiles.get(page)
        if found is not None:
            return found
        page_map = await self.async_screen_map()
        screens = page_map.get(page, [])
        for screen in screens:
            try:
                widgets = await self.async_widgets(screen)
            except CtcWebError:
                continue
            for widget in widgets:
                if not widget.visible or widget.label is None or widget.width <= 0:
                    continue
                if await self.async_english_label(screen, widget) == OPERATION_DATA_LABEL_EN:
                    found = (screen, screens, tap_target(widgets, widget))
                    self._tiles[page] = found
                    return found
        return None

    async def async_goto_home(self, hops: int = 6) -> int | None:
        """Step back until the home screen is showing.

        Home is recognised by carrying the operation data tile rather than by the
        back button ceasing to work, because on the home screen that spot is a
        button which would navigate somewhere else entirely.

        The tile is recognised by its caption, read from the display's text
        catalogue, and a catalogue that is slow for a moment answers nothing.
        Before, that moment made the home screen look like any other page and
        the back button was pressed on it, which on an i550 Pro opens the quick
        menu. So a page recognised as home once is remembered, and on it
        nothing is pressed: the panel is home, which is what the caller asked
        for, and whether the tile can be read right now is the caller's next
        question.
        """
        page_map = await self.async_screen_map()
        seen: set[int] = set()
        for _ in range(hops):
            here = await self.async_current_page()
            if await self._async_operation_tile(here) is not None:
                self._home = here
                return here
            if here == self._home:
                _LOGGER.debug(
                    "On the home screen (page %s), whose operation data tile could not "
                    "be read just now; pressing nothing",
                    here,
                )
                return here
            if here in seen:
                return None
            seen.add(here)
            await self.async_click(page_map.get(here, []), *CHROME_BUTTON)
            if await self.async_current_page() == here:
                return None
        here = await self.async_current_page()
        if await self._async_operation_tile(here) is not None:
            self._home = here
            return here
        return here if here == self._home else None

    async def async_goto_operation_root(self) -> bool:
        """Navigate to the operation data menu from wherever the panel is.

        The page the tile leads to is the root, and is remembered as such
        where no root is known yet. A press of the remembered tile that moves
        nothing drops it from memory, so a stale point costs one walk and not
        the rest of the run.
        """
        self.tile_found = None
        home = await self.async_goto_home()
        if home is None:
            return False
        found = await self._async_operation_tile(home)
        self.tile_found = found is not None
        if found is None:
            return False
        _screen, screens, (x, y) = found
        before = await self.async_current_page()
        await self.async_click(screens, x, y)
        after = await self.async_current_page()
        if after == before:
            self._tiles.pop(home, None)
            return False
        if self.root is None:
            self.root = after
        return True
