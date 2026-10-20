"""What a diagnostics download carries, built and washed without Home Assistant.

When two i360 installations stood without a coefficient of performance, one
with no page of its display read and one with six pages and empty counters,
the only way to find out why was to add flags to the anonymous report and wait
a release for the answer. The answer was on the coordinators all along: when
each display row was last read, why the last harvest gave way, which Modbus
blocks the model lacks, how far the last reading of the menu got. This module
gathers that into one dictionary, and diagnostics.py, the thin Home Assistant
shell, hands it over from the integration's menu as a file to attach to an
issue.

Redacting keys is not enough on its own. The host is also in the entry's
title, in the device's identifiers and in the error a display client writes
when it cannot reach the address, and an entry the DHCP flow has moved keeps
the address it was created with as its device key; the serial number's
sequence group, the one that identifies a machine, is also the tail of the
display's MAC address and of its host name (CTC0001). So besides the keys Home
Assistant redacts, every string is washed: any IPv4 address, any MAC address
and the entry's own host and device key become a placeholder, the display's
host name loses its digits, and a serial number in any spelling is cut down to
its product and week groups, which describe a production run rather than a
machine (see stats_extra). Kept free
of Home Assistant so a test can dump stand-ins and prove that nothing of the
kind survives.

Every section is built on its own and a section that fails says so in place
of its content: a download is asked for when something is wrong, and that is
exactly when an object may be missing a field.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import asdict, is_dataclass
from datetime import date, datetime, timedelta
import re
from typing import Any

from .const import (
    CONF_IDENTITY,
    CONF_IDENTITY_SCREENS,
    CONF_MENU,
    CONF_MENU_ROOT,
    CONF_MENU_VERSION,
    CONF_SLOW_PAGES,
)
from .cop import SHARE_SPANS, cop_for_report, current_totals, immersion_now, share_for
from .stats_extra import serial_made, serial_product

#: What Home Assistant puts in place of a redacted value, used for the wash too.
REDACTED = "**REDACTED**"

#: Keys whose values are taken out whole wherever they occur, by Home
#: Assistant's async_redact_data in diagnostics.py. The device key is the
#: address the entry was created with, which after a move is not the host.
TO_REDACT = frozenset(
    {"serial", "mac", "host", "device_key", "title", "configuration_url", "identifiers"}
)

#: How many of the newest transitions and daily energy samples are shown. The
#: tracker keeps a year of samples; the newest week says what a report needs.
TRANSITIONS_SHOWN = 20
SAMPLES_SHOWN = 7

#: The serial number's sequence group, replaced wherever it is spelt out.
SEQUENCE_PLACEHOLDER = "XXXX"

#: Four groups of digits, not inside a longer run of them: a digit or a dot and
#: a digit may not stand on either side, but a full stop that ends a sentence
#: may follow, "could not reach 192.0.2.55.", and a dot may come before.
_IPV4 = re.compile(r"(?<!\d)(?<!\d\.)(?:\d{1,3}\.){3}\d{1,3}(?!\d|\.\d)")
#: Six pairs of hex digits set apart by a colon or a dash, not inside a longer
#: run of them: a hex digit, or a separator and a hex digit, may not stand on
#: either side, but a separator alone may follow, "02:00:00:00:00:01: gone".
_MAC = re.compile(
    r"(?<![0-9A-Fa-f])(?<![0-9A-Fa-f][:-])[0-9A-Fa-f]{2}(?:[:-][0-9A-Fa-f]{2}){5}"
    r"(?![0-9A-Fa-f]|[:-][0-9A-Fa-f])"
)
#: Three groups of four digits, run together or set apart by a space or a dash:
#: how CTC writes a serial number, and how it ends up in a title or a message.
_SERIAL = re.compile(r"(?<!\d)(\d{4})[ -]?(\d{4})[ -]?(\d{4})(?!\d)")
#: The display's host name: CTC and the serial number's sequence group.
_HOSTNAME = re.compile(r"(?<![A-Za-z0-9])CTC-?\d{4}(?!\d)", re.I)


# --------------------------------------------------------------- the wash


class Secrets:
    """The strings of one installation that name it, to be washed out of a dump."""

    def __init__(
        self, host: Any = None, serial: Any = None, mac: Any = None, key: Any = None
    ) -> None:
        self.patterns: list[re.Pattern[str]] = []
        # The device key first: a key of its own is the address with a number
        # after it, "192.0.2.55#2", and goes whole rather than leaving "#2".
        for name in dict.fromkeys(str(found) for found in (key, host) if found):
            # As a whole token, so a short host name does not eat into words,
            # though a full stop that ends a sentence may follow it.
            self.patterns.append(
                re.compile(r"(?<![\w.-])" + re.escape(name) + r"(?![\w-]|\.[\w-])", re.I)
            )
        if mac:
            hexes = re.sub(r"[^0-9A-Fa-f]", "", str(mac))
            if len(hexes) == 12:
                self.patterns.append(
                    re.compile(r"(?<![0-9A-Fa-f])" + hexes + r"(?![0-9A-Fa-f])", re.I)
                )
        digits = "".join(ch for ch in str(serial or "") if ch.isdigit())
        self.sequence = digits[8:12] if len(digits) >= 12 else None

    def wash_text(self, text: str) -> str:
        for pattern in self.patterns:
            text = pattern.sub(REDACTED, text)
        text = _MAC.sub(REDACTED, text)
        text = _IPV4.sub(REDACTED, text)
        text = _HOSTNAME.sub("CTC" + SEQUENCE_PLACEHOLDER, text)
        text = _SERIAL.sub(lambda m: f"{m.group(1)} {m.group(2)} {SEQUENCE_PLACEHOLDER}", text)
        if self.sequence:
            text = re.sub(r"(?<!\d)" + self.sequence + r"(?!\d)", SEQUENCE_PLACEHOLDER, text)
        return text


def secrets_for(
    data: Mapping[str, Any], options: Mapping[str, Any], runtime: Any = None
) -> Secrets:
    """What names this installation: its host and device key, its serial number and its MAC address.

    From the running identity where there is one, since it may hold what the
    options do not have yet, and from the options otherwise.
    """
    stored = options.get(CONF_IDENTITY) if isinstance(options.get(CONF_IDENTITY), Mapping) else {}
    identity = getattr(runtime, "identity", None)
    serial = getattr(identity, "serial", None) or stored.get("serial")
    mac = getattr(identity, "mac", None) or stored.get("mac")
    return Secrets(data.get("host"), serial, mac, data.get("device_key"))


def wash(value: Any, secrets: Secrets) -> Any:
    """``value`` as plain JSON types, with every string washed.

    Keys are washed as well as values, since a dictionary keyed by something
    the display said could carry it there. Sets come out sorted, so two
    downloads of the same state read the same.
    """
    if isinstance(value, str):
        return secrets.wash_text(value)
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, timedelta):
        return value.total_seconds()
    if is_dataclass(value) and not isinstance(value, type):
        return wash(asdict(value), secrets)
    if isinstance(value, Mapping):
        return {str(wash(str(key), secrets)): wash(item, secrets) for key, item in value.items()}
    if isinstance(value, (set, frozenset)):
        return [wash(item, secrets) for item in sorted(value, key=str)]
    if isinstance(value, Iterable):
        return [wash(item, secrets) for item in value]
    return secrets.wash_text(str(value))


# --------------------------------------------------------------- the parts


def _section(build: Callable[[], Any]) -> Any:
    """One section, or what went wrong building it instead of the whole download."""
    try:
        return build()
    except Exception as err:  # a diagnostics download must never fail
        return {"unavailable": f"{type(err).__name__}: {err}"}


def reduced_serial(raw: Any) -> dict[str, Any]:
    """The serial number as the product and the week it was made, never the machine."""
    return {
        "serial_known": bool(raw),
        "product": serial_product(raw),
        "made": serial_made(raw),
    }


def identity_section(identity: Any) -> dict[str, Any]:
    """What the unit says about itself, with the serial cut down and the MAC left out."""
    if isinstance(identity, Mapping):
        get = identity.get
    else:
        def get(name: str) -> Any:
            return getattr(identity, name, None)
    found = reduced_serial(get("serial"))
    found["mac_known"] = bool(get("mac"))
    for name in ("display_firmware", "bootloader", "heatpump_model", "heatpump_firmware"):
        found[name] = get(name)
    return found


def _page_numbers(stored: Any) -> list[Any]:
    return [item.get("page") for item in stored or [] if isinstance(item, Mapping)]


def options_section(options: Mapping[str, Any]) -> dict[str, Any]:
    """The options as stored, with the menu and the identity told apart in their own sections."""
    shown: dict[str, Any] = {}
    for key, value in options.items():
        if key == CONF_MENU:
            shown[key] = f"{len(value or [])} pages, under menu"
        elif key == CONF_SLOW_PAGES:
            shown[key] = _page_numbers(value)
        elif key == CONF_IDENTITY:
            shown[key] = "under identity"
        else:
            shown[key] = value
    return shown


def _stored_page(item: Any) -> Any:
    """A stored page with its title under ``name``.

    Home Assistant redacts a key called ``title`` at every depth, since an
    entry's title carries its address, and a page's title is the menu's own
    caption, which is what a report about a page needs to see.
    """
    if not isinstance(item, Mapping):
        return item
    page = {key: value for key, value in item.items() if key != "title"}
    return {"name": item.get("title"), **page}


def menu_section(
    options: Mapping[str, Any],
    runtime: Any,
    menu_tries: int,
    walked: bool,
    swept: bool,
    menu_outcome: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """The stored menu, which pages are ticked and harvested, and the walks this run."""
    pages = getattr(runtime, "pages", None)
    return {
        "version": options.get(CONF_MENU_VERSION),
        "root": options.get(CONF_MENU_ROOT),
        "readings_this_run": menu_tries,
        # How far the last walk through the menu got (roadmap L6), None
        # before the first walk of this run.
        "last_walk": dict(menu_outcome) if menu_outcome else None,
        "selected": _page_numbers(options.get(CONF_SLOW_PAGES)),
        "harvested": [page.page for page in pages] if pages is not None else None,
        "system_information_walked_this_run": walked,
        "identity_screens_swept_this_run": swept,
        "identity_screens": options.get(CONF_IDENTITY_SCREENS),
        "pages": [_stored_page(item) for item in options.get(CONF_MENU) or []],
    }


def display_section(runtime: Any) -> dict[str, Any] | None:
    """The harvest's own bookkeeping, the client's counters and the display's values."""
    web = getattr(runtime, "web", None)
    client = getattr(runtime, "web_client", None)
    found: dict[str, Any] = {}
    if client is not None:
        found["client"] = {
            "root": getattr(client, "root", None),
            "requests": getattr(client, "requests", None),
            "taps": getattr(client, "taps", None),
            "text_misses": getattr(client, "text_misses", None),
        }
    if web is None:
        found["harvest"] = None
        return found
    patience = getattr(web, "patience", None)
    found["harvest"] = {
        "interval": getattr(web, "interval", None),
        "last_update_success": getattr(web, "last_update_success", None),
        "patience": {
            "failures": getattr(patience, "failures", None),
            "next_wait": getattr(patience, "seconds", None),
        },
        "last_attempt": getattr(web, "last_attempt", None),
        "next_attempt": getattr(web, "next_attempt", None),
        "last_harvest": getattr(web, "last_harvest", None),
        "last_failure": getattr(web, "last_failure", None),
        "pages_read": getattr(web, "pages_read", None),
        "pages_missed": getattr(web, "pages_missed", None),
        "skips_in_a_row": getattr(web, "skips_in_a_row", None),
        "last_skip_reason": getattr(web, "last_skip_reason", None),
        "home_page": getattr(web, "home_page", None),
        "expected_page": getattr(web, "_expected_page", None),
        "read_at": dict(getattr(web, "read_at", None) or {}),
        "values": dict(getattr(web, "data", None) or {}),
    }
    return found


def modbus_section(runtime: Any) -> dict[str, Any] | None:
    """The block plan, the blocks this model was learnt to lack, and the readings."""
    modbus = getattr(runtime, "modbus", None)
    if modbus is None:
        return None
    interval = getattr(modbus, "update_interval", None)
    missing = getattr(getattr(modbus, "_missing", None), "missing", None)
    return {
        "interval": interval,
        "last_update_success": getattr(modbus, "last_update_success", None),
        # The coordinator's own plan, read as it stands: the first block is
        # the one every model answers, the rest in address order.
        "block_plan": [list(block) for block in getattr(modbus, "_blocks", None) or []],
        "missing_blocks": sorted(missing or ()),
        "read_failures": getattr(modbus, "read_failures", None),
        "unknown_codes": dict(getattr(modbus, "unknown_codes", None) or {}),
        "registers_answered": len(getattr(modbus, "answered", None) or ()),
        "values": dict(getattr(modbus, "data", None) or {}),
    }


def control_section(runtime: Any) -> dict[str, Any]:
    control = getattr(runtime, "control", None)
    active = getattr(control, "active", None) or {}
    return {
        "enabled": getattr(runtime, "control_enabled", None),
        "active": {str(address): raw for address, raw in dict(active).items()},
    }


def cop_section(runtime: Any) -> dict[str, Any]:
    """The counters behind the coefficient of performance and what the tracker holds."""
    energy_out = getattr(runtime, "energy_out", None)
    energy_in = getattr(runtime, "energy_in", None)
    snapshot = getattr(runtime, "consumption_snapshot", None)
    out, consumed = current_totals(runtime)
    found: dict[str, Any] = {
        "heat_counter": getattr(energy_out, "key", None),
        "consumption_counter": getattr(energy_in, "key", None),
        "consumption_from_modbus": snapshot is not None,
        "totals": {"heat_kwh": out, "consumption_kwh": consumed},
        "figures": cop_for_report(runtime),
        # R28: the immersion heater's counter as taken with the display's
        # counters, which the samples and the share of auxiliary heat use.
        "immersion_kwh": immersion_now(runtime),
    }
    tracker = getattr(runtime, "cop", None)
    if tracker is None:
        found["tracker"] = None
        return found
    found["aux_share"] = {}
    for span in SHARE_SPANS:
        try:
            result, reason = share_for(runtime, span)
        except Exception as err:  # the diagnostics must come whatever a figure does
            found["aux_share"][span] = {"error": type(err).__name__}
            continue
        found["aux_share"][span] = {"value": result.value, "reason": reason}
    samples = dict(getattr(tracker, "_samples", None) or {})
    newest = sorted(samples)[-SAMPLES_SHOWN:]
    found["tracker"] = {
        "anchor": getattr(tracker, "_anchor", None),
        "first_year": getattr(tracker, "_first_year", None),
        "daily_samples": len(samples),
        "newest_daily_samples": {day: samples[day] for day in newest},
        "recent_samples": list(getattr(tracker, "_recent", None) or []),
    }
    return found


def _transition(item: Any) -> dict[str, Any]:
    return {
        "seq": getattr(item, "seq", None),
        "kind": getattr(item, "kind", None),
        "at": getattr(item, "at", None),
        "from": getattr(item, "from_code", None),
        "to": getattr(item, "to_code", None),
        "outdoor": getattr(item, "outdoor", None),
        "minutes": getattr(item, "minutes", None),
    }


def transitions_section(runtime: Any) -> dict[str, Any] | None:
    watch = getattr(runtime, "transitions", None)
    if watch is None:
        return None
    log = list(getattr(watch, "log", None) or [])
    return {
        "running": getattr(watch, "running", None),
        "defrosting": getattr(watch, "defrosting", None),
        "counting_since": getattr(watch, "counting_since", None),
        "starts_today": getattr(watch, "starts_today", None),
        "defrosts_today": getattr(watch, "defrosts_today", None),
        "last_start": getattr(watch, "last_start", None),
        "last_run": getattr(watch, "last_run", None),
        "last_defrost": getattr(watch, "last_defrost", None),
        "log": [_transition(item) for item in log[-TRANSITIONS_SHOWN:]],
    }


def alarms_section(runtime: Any) -> dict[str, Any] | None:
    alarms = getattr(runtime, "alarms", None)
    if alarms is None:
        return None
    confirmed = getattr(alarms, "last_confirmed", None)
    return {
        "active": getattr(alarms, "active", None),
        "last_confirmed": confirmed.isoformat(timespec="seconds") if confirmed else None,
        "episodes": [dict(episode) for episode in getattr(alarms, "episodes", None) or []],
    }


def device_section(runtime: Any) -> dict[str, Any] | None:
    """The device as the integration describes it, without the serial number field."""
    device = getattr(runtime, "device", None)
    if not isinstance(device, Mapping):
        return None
    return {
        key: device.get(key)
        for key in (
            "name",
            "manufacturer",
            "model",
            "sw_version",
            "hw_version",
            "configuration_url",
            "identifiers",
        )
    }


def build(
    *,
    data: Mapping[str, Any],
    options: Mapping[str, Any],
    runtime: Any = None,
    menu_tries: int = 0,
    walked: bool = False,
    swept: bool = False,
    menu_outcome: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """The whole download, before the keys are redacted and the strings washed.

    ``runtime`` is None for an entry that is not set up, waiting for the
    controller to answer for one; the download still says what the entry is
    and what it has stored. ``menu_tries``, ``walked``, ``swept`` and
    ``menu_outcome`` are the package's books of this run: how many readings
    of the menu were spent, whether the walk to the system information page
    and the sweep for the identity's screens have been made, and how far the
    last walk through the menu got.
    """
    identity = getattr(runtime, "identity", None)
    if identity is None:
        identity = (
            options.get(CONF_IDENTITY) if isinstance(options.get(CONF_IDENTITY), Mapping) else {}
        )
    return {
        "entry": {
            "data": dict(data),
            "options": _section(lambda: options_section(options)),
            "loaded": runtime is not None,
        },
        "identity": _section(lambda: identity_section(identity)),
        "device": _section(lambda: device_section(runtime)),
        "menu": _section(
            lambda: menu_section(options, runtime, menu_tries, walked, swept, menu_outcome)
        ),
        "display": _section(lambda: display_section(runtime)),
        "modbus": _section(lambda: modbus_section(runtime)),
        "control": _section(lambda: control_section(runtime)),
        "cop": _section(lambda: cop_section(runtime)),
        "transitions": _section(lambda: transitions_section(runtime)),
        "alarms": _section(lambda: alarms_section(runtime)),
    }
