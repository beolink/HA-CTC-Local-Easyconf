"""Alarms as the display prints them: the code, the text, and ten episodes.

Modbus says only that the heat pump is off because of an alarm, code 7 in
register 62017, without which one or since when. The display prints the alarm
itself out of its own catalogue, "[E017] Givare solpaneler ut", and on the
pages captured so far it draws that text in the header icon at the top left;
the status field of a heat pump page is the other text the panel fills from a
variable. This module reads those two places off the values the harvest has
already fetched, through the client (web_api.async_alarm_readout), keeps the
alarm the panel showed last, and remembers the last ten episodes: when each
began and ended, its code, and the outdoor temperature when it began.

An episode is opened by a round that shows an alarm and closed by a round that
reads every page that can print one and shows none. A round that reads fewer
pages than that says nothing about the alarm, so the log keeps the episode as
it is; and the log keeps when a round last saw the open episode on the panel,
so that an episode nothing has seen for a while is not shown as a live alarm.

Nothing is polled for this, nothing is navigated for it, the catalogue is
never enumerated, and none of it goes to the statistics backend. Free of Home
Assistant imports; the store is handed in, as for cop.CopTracker.

Where the panel writes the alarm has been seen on one page of one model so
far, the history page of an i550 Pro on 2026-10-05, and is to be confirmed
the next time a unit alarms.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import datetime, timedelta
import logging
import re
from typing import Any, NamedTuple

from .attribute_names import episode_in_english
from .const import ALARM_EPISODES, ALARM_TEXT_PREFIX, SlowPage
from .web_api import CtcWebError

_LOGGER = logging.getLogger(__name__)

STORAGE_VERSION = 1

#: How long a change to the log may wait before it is written down. Short: an
#: alarm is the kind of thing worth having on disk before the next restart.
SAVE_DELAY_SECONDS = 5

#: The confirmation an open episode is held to: a round has to have seen it on
#: the panel this long ago or less. Fifteen minutes, or one harvest interval
#: and a margin where that is longer, since the harvest is what sees it; the
#: default harvest is every thirty minutes, which fifteen minutes alone would
#: switch the alarm off between two harvests.
CONFIRM_MINUTES = 15
CONFIRM_MARGIN = timedelta(minutes=5)

#: "[E017] Givare solpaneler ut": the code in brackets, the text after it.
_ALARM = re.compile(r"^\[(E[^\]]*)\]\s*(.*)$")


def confirm_window(interval: float) -> timedelta:
    """How long a sighting counts for a harvest every ``interval`` seconds."""
    return max(timedelta(minutes=CONFIRM_MINUTES), timedelta(seconds=interval) + CONFIRM_MARGIN)


def parse_alarm(text: str | None) -> tuple[str, str] | None:
    """The code and the text of an alarm as the panel prints it, or None.

    "[E017] Givare solpaneler ut" gives ("E017", "Givare solpaneler ut"). An
    information text, "[I...]", and anything else is no alarm.
    """
    if not text:
        return None
    match = _ALARM.match(text.strip())
    if match is None:
        return None
    return match.group(1).strip(), match.group(2).strip()


def alarm_text(candidates: Iterable[str | None]) -> str | None:
    """The first candidate that is an alarm text, as the panel printed it."""
    for text in candidates:
        if text and text.strip().startswith(ALARM_TEXT_PREFIX) and parse_alarm(text):
            return text.strip()
    return None


class AlarmRound(NamedTuple):
    """What the harvest's last round said about the alarm.

    ``read`` is whether any page was read in it. ``shown`` is the alarm the
    panel printed in those pages, if any. ``complete`` is whether every page
    that can print an alarm was read, which is what lets a round that shows
    none close an episode.
    """

    read: bool
    shown: str | None
    complete: bool


class _Note(NamedTuple):
    text: str | None
    at: datetime
    carries: bool


class AlarmWatch:
    """What each harvested page said about an alarm, and which of it is new.

    The harvest notes every page it reads; the log, which lives on the
    runtime, asks once per harvest what was read since it last asked. Judging
    per harvest rather than per page is what keeps an alarm from flapping
    should the panel print it on some pages and not on others: one page
    showing it is an alarm, no page read this round showing it is none.
    """

    def __init__(self, pages: Iterable[int] = ()) -> None:
        #: The pages the harvest reads. One the watch has never been able to
        #: judge counts as one that can print an alarm, so that a round which
        #: does not reach it cannot close an episode; a page judged to carry
        #: nothing stops counting once it has been judged.
        self._pages = frozenset(pages)
        self._notes: dict[int, _Note] = {}
        self._handed: dict[int, datetime] = {}

    async def async_note(
        self,
        client: Any,
        page: SlowPage,
        values_by_screen: Mapping[int, list[Any]],
        read_at: datetime,
    ) -> str | None:
        """Read a page's alarm texts off the values just harvested.

        Only the screens that carry one of the page's own rows are looked at:
        the chrome screens a page shares with every other hold the clock and
        the back button, not the header icon. A screen whose definition the
        display would not give up leaves the page unnoted this round, which is
        no information rather than "no alarm".
        """
        screens = [
            screen
            for screen in page.screens
            if screen in values_by_screen and any(v.screen == screen for v in page.values)
        ]
        if not screens:
            return None
        read = getattr(client, "async_alarm_readout", None)
        if read is None:
            # A client that cannot read texts, a stand-in among them: no
            # information, not "no alarm".
            return None
        carries = False
        candidates: list[str] = []
        for screen in screens:
            try:
                reading = await read(screen, values_by_screen[screen])
            except CtcWebError as err:
                _LOGGER.debug("Alarm texts of screen %s not read: %s", screen, err)
                return None
            carries = carries or reading.carries
            candidates.extend(reading.texts)
        text = alarm_text(candidates)
        self._notes[page.page] = _Note(text, read_at, carries)
        return text

    def fresh(self) -> AlarmRound:
        """What was read since the last call, and whether every alarm page was read."""
        new = {
            page: note
            for page, note in self._notes.items()
            if self._handed.get(page) != note.at
        }
        for page, note in new.items():
            self._handed[page] = note.at
        if not new:
            return AlarmRound(False, None, False)
        expected = {
            page
            for page in self._pages | set(self._notes)
            if page not in self._notes or self._notes[page].carries
        }
        shown = next((note.text for note in new.values() if note.text), None)
        return AlarmRound(True, shown, expected <= set(new))


def _stamp(when: datetime) -> str:
    return when.isoformat(timespec="seconds")


class AlarmLog:
    """The alarm the panel shows, and the last ten episodes of it.

    An episode begins when a round shows an alarm text and no episode with
    that code is open. It ends when a round shows another code, or when a
    round that read every page that can print an alarm shows none. Episodes
    are kept newest first, each with the code, the text, the panel's own print
    of it, when it began and ended, and the outdoor temperature when it began.

    ``last_confirmed`` is when a round last saw the open episode on the panel.
    It is kept in memory only: after a restart an open episode is not confirmed
    until a round sees it again, so a stored alarm is never shown as live on
    the strength of an old sighting.
    """

    def __init__(
        self, store: Any, confirm_within: timedelta = timedelta(minutes=CONFIRM_MINUTES)
    ) -> None:
        self._store = store
        self._within = confirm_within
        self.episodes: list[dict[str, Any]] = []
        self.last_confirmed: datetime | None = None

    async def async_load(self) -> None:
        stored = await self._store.async_load()
        if not isinstance(stored, Mapping) or not isinstance(stored.get("episodes"), list):
            return
        episodes: list[dict[str, Any]] = []
        for item in stored["episodes"]:
            if not isinstance(item, Mapping) or not item.get("code") or not item.get("start"):
                continue
            outdoor = item.get("outdoor")
            episodes.append(
                {
                    "code": str(item["code"]),
                    "text": str(item.get("text") or ""),
                    "shown": str(
                        item.get("shown") or f"[{item['code']}] {item.get('text') or ''}".strip()
                    ),
                    "start": str(item["start"]),
                    "end": str(item["end"]) if item.get("end") else None,
                    "outdoor": (
                        float(outdoor)
                        if isinstance(outdoor, (int, float)) and not isinstance(outdoor, bool)
                        else None
                    ),
                }
            )
        self.episodes = episodes[:ALARM_EPISODES]

    @property
    def latest(self) -> dict[str, Any] | None:
        """The newest episode, open or closed."""
        return self.episodes[0] if self.episodes else None

    @property
    def active(self) -> bool:
        latest = self.latest
        return latest is not None and latest["end"] is None

    def confirmed(self, now: datetime) -> bool:
        """Whether the open episode was seen on the panel within the confirmation window."""
        return (
            self.active
            and self.last_confirmed is not None
            and now - self.last_confirmed < self._within
        )

    def confirmed_until(self) -> datetime | None:
        """When the open episode's confirmation runs out, or None when nothing is confirmed."""
        if not self.active or self.last_confirmed is None:
            return None
        return self.last_confirmed + self._within

    def note(
        self, shown: str | None, when: datetime, outdoor: float | None, *, complete: bool = False
    ) -> bool:
        """Take in what a round of the harvest showed. Returns whether the stored log changed.

        ``shown`` is the alarm text as the panel printed it, or None when the
        round showed none. ``complete`` says the round read every page that
        can print an alarm; only such a round closes an episode on a reading
        of none, and it is off unless the caller says otherwise. A round that
        read nothing is not to be noted at all; that is the watch's business.
        """
        parsed = parse_alarm(shown)
        open_episode = self.latest if self.active else None
        if parsed is not None:
            code, text = parsed
            if open_episode is not None and open_episode["code"] == code:
                self.last_confirmed = when
                return False
            if open_episode is not None:
                open_episode["end"] = _stamp(when)
            self.episodes.insert(
                0,
                {
                    "code": code,
                    "text": text,
                    "shown": (shown or "").strip(),
                    "start": _stamp(when),
                    "end": None,
                    "outdoor": float(outdoor)
                    if isinstance(outdoor, (int, float)) and not isinstance(outdoor, bool)
                    else None,
                },
            )
            del self.episodes[ALARM_EPISODES:]
            self.last_confirmed = when
            self._save()
            return True
        if complete and open_episode is not None:
            open_episode["end"] = _stamp(when)
            self.last_confirmed = None
            self._save()
            return True
        return False

    def _save(self) -> None:
        try:
            self._store.async_delay_save(
                lambda: {"episodes": [dict(e) for e in self.episodes]}, SAVE_DELAY_SECONDS
            )
        except Exception as err:  # a lost save costs the log, not the alarm
            _LOGGER.debug("Could not schedule saving the alarm log: %s", err)

    def attributes(self, english: bool = False) -> list[dict[str, Any]]:
        """The episodes as an entity shows them, newest first, in the page's words.

        With ``english`` under the English keys that ``episodes`` carries beside
        ``episoder`` (attribute_names.py), the same values either way.
        """
        shown = [
            {
                "kod": episode["code"],
                "text": episode["text"],
                "start": episode["start"],
                "slut": episode["end"],
                "utetemperatur": episode["outdoor"],
            }
            for episode in self.episodes
        ]
        if english:
            return [episode_in_english(episode) for episode in shown]
        return shown
