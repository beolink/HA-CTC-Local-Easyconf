"""What this integration contributes to the anonymous daily report.

Deliberately free of Home Assistant imports, so the unit tests can prove
without a Home Assistant installation that nothing but the agreed fields can
leave the house. Everything here is either a fixed slug from a closed list or
a plain count. No string that came from the unit, the network or the user is
ever passed through.

See https://stats.rnet.se/integritet for the full list and the reasoning.
"""

from __future__ import annotations

import re
from typing import Any

from .const import MODEL_FAMILIES

#: Display family to a short slug, by the model name discovery gives it, out of
#: const.MODEL_FAMILIES. Unknown models report "other" rather than their name,
#: so a future model cannot leak an unreviewed string.
MODEL_SLUGS = {name: slug for name, slug in MODEL_FAMILIES.values()}


def model_slug(model: str | None) -> str:
    """Map a discovered model name to a slug from the closed list above."""
    if not model:
        return "unknown"
    return MODEL_SLUGS.get(model.strip(), "other")


#: The stem of the display's settings file names the family of controllers it
#: belongs to: ezi2xx on an i255, ezi5xx on an i550 Pro. A model reported as
#: "other" says nothing about which family turned up, so for those alone the
#: stem goes along as a flag of its own, family_<stem>, and only when it has
#: the shape of a stem: two to sixteen lowercase letters and digits. The key
#: is the one feature that is built rather than listed (see FEATURE_KEYS),
#: and the pattern is what keeps anything but a family code out of it.
FAMILY_PREFIX = "family_"
FAMILY_STEM = re.compile(r"^[a-z0-9]{2,16}$")
_MODEL_STEM = re.compile(r"\(([^()]*)\)\s*$")


def entry_stem(data: Any) -> str | None:
    """An entry's settings file stem, from what its data holds.

    ``settings_stem`` where the entry was made with one; otherwise the name of
    the settings file the display gave at set-up, which every entry keeps;
    otherwise the parentheses at the end of the model name, which is where an
    entry for a family the integration did not know put the stem. Lower case
    and stripped, not yet checked against FAMILY_STEM.
    """
    if not hasattr(data, "get"):
        return None
    name = data.get("settings_name")
    if isinstance(name, str):
        name = name.strip().removeprefix("settings_").removesuffix(".bin")
    model = data.get("model")
    found = _MODEL_STEM.search(model) if isinstance(model, str) else None
    for candidate in (data.get("settings_stem"), name, found.group(1) if found else None):
        if isinstance(candidate, str) and candidate.strip():
            return candidate.strip().lower()
    return None


def family_feature(slug: str, stem: Any) -> str | None:
    """The family flag's key for a model reported as "other", or None."""
    if slug != "other" or not isinstance(stem, str):
        return None
    cleaned = stem.strip().lower()
    return FAMILY_PREFIX + cleaned if FAMILY_STEM.match(cleaned) else None


#: CTC names its outdoor units EA or EP followed by three digits and sometimes
#: an M. Matching the shape rather than keeping a list means a model released
#: tomorrow still reports as itself, while anything else reports as "other", so
#: no free text can reach the database.
HEATPUMP_PATTERN = re.compile(r"^(EA|EP)\d{3}M?$", re.I)


def heatpump_slug(model: str | None) -> str:
    """Map the outdoor unit's name to a slug, or "other"."""
    if not model:
        return "unknown"
    cleaned = model.strip()
    return cleaned.lower() if HEATPUMP_PATTERN.match(cleaned) else "other"


#: Firmware written as a date, which is how both the display and the heat pump
#: control board report theirs.
FIRMWARE_PATTERN = re.compile(r"^\d{8}$")


def firmware_value(raw: Any) -> str | None:
    """Keep a firmware only if it is the eight digit date CTC writes."""
    if raw is None:
        return None
    text = str(raw).strip()
    return text if FIRMWARE_PATTERN.match(text) else None


def control_firmware_value(raw: Any) -> int | None:
    """The control unit reports its software as a plain number."""
    if raw is None:
        return None
    try:
        number = int(raw)
    except (TypeError, ValueError):
        return None
    return number if 0 < number < 100000 else None


#: CTC writes a serial number as three groups of four digits: which product it
#: is, the year and week it was made, and a sequence number. Only the first two
#: groups are reported. They are shared by a whole production run, so they say
#: when a machine was built without saying which machine it is. The sequence
#: number, which does identify it, never leaves the house.
#: https://ctc.se/blogg/varmepump/guide-for-produktens-serienummer
SERIAL_GROUP = 4


def _serial_digits(raw: Any) -> str | None:
    if raw is None:
        return None
    digits = "".join(ch for ch in str(raw) if ch.isdigit())
    return digits if len(digits) >= SERIAL_GROUP * 3 else None


def serial_product(raw: Any) -> str | None:
    """The first group: which product this is."""
    digits = _serial_digits(raw)
    return digits[:SERIAL_GROUP] if digits else None


#: The product codes the statistics backend keeps (lib/ingest.ts, METRICS):
#: a number outside this is clamped to the edge without a word, so a product
#: group with a leading zero, 0712, would arrive as 1000, another product's
#: code. Such a group is left out of the report instead.
PRODUCT_CODE_MIN = 1000
PRODUCT_CODE_MAX = 9999


def product_code_value(product: str | None) -> int | None:
    """The product group as the report sends it, or None where the backend would change it.

    The first group of a serial is four digits, so it can only fall outside
    the backend's range at the bottom: a group that starts with a zero. No
    CTC serial seen so far does (7208, 1002, 7312), but one that did must be
    left out rather than stored as somebody else's product.
    """
    if not product:
        return None
    try:
        code = int(product)
    except ValueError:
        return None
    return code if PRODUCT_CODE_MIN <= code <= PRODUCT_CODE_MAX else None


def serial_made(raw: Any) -> str | None:
    """The second group: the year and week it was made, as YYWW.

    Rejected unless the week is a real one, so a serial in some other format
    cannot be read as a date that never existed.
    """
    digits = _serial_digits(raw)
    if not digits:
        return None
    made = digits[SERIAL_GROUP : SERIAL_GROUP * 2]
    week = int(made[2:])
    return made if 1 <= week <= 53 else None


def energy_value(raw: Any) -> float | None:
    """A lifetime counter in kilowatt hours, or nothing when it is not a number."""
    if raw is None:
        return None
    try:
        value = round(float(raw), 1)
    except (TypeError, ValueError):
        return None
    return value if 0.0 <= value <= 1_000_000.0 else None


def cop_value(raw: Any) -> float | None:
    """A coefficient of performance, rejected unless it is physically sane."""
    if raw is None:
        return None
    try:
        value = round(float(raw), 2)
    except (TypeError, ValueError):
        return None
    return value if 0.5 <= value <= 10.0 else None


class ErrorCounter:
    """Turns a cumulative failure count into 'since the previous report'.

    A reload resets the coordinator's counter, so a value lower than the one
    seen last time means a fresh start, not a negative number of failures.
    """

    def __init__(self) -> None:
        self._seen = 0

    def delta(self, total: int) -> int:
        if total < self._seen:
            self._seen = 0
        change = total - self._seen
        self._seen = total
        return change


#: Every flag the report can carry under "features" and every number under
#: "metrics": two closed lists, kept beside the code that builds them and named
#: one by one in the consent text, the README and stats.rnet.se/integritet. A
#: test builds the report with everything set and requires the key sets to match
#: these exactly, so a key cannot turn up in the report without being written
#: here, and nothing written here goes unmentioned in what the owner agreed to.
#: The one feature outside the list is family_<stem>, built from FAMILY_PREFIX
#: and a stem that matches FAMILY_STEM, and only for a model reported as other.
FEATURE_KEYS = frozenset(
    {
        "modbus",
        "display",
        "control",
        "pages",
        "history_page",
        "heat_counter",
        "consumption_counter",
        "consumption_modbus",
        "heat_total",
        "consumption_total",
        "cop_floor",
        "cop_stuck",
        "cop_implausible",
        "power_rows",
        "power_values",
        "cop_integrated",
        "cop_estimate_carnot",
        "menu_pages",
        "menu_home",
        "menu_root",
    }
)
METRIC_KEYS = frozenset(
    {
        "cop_day",
        "cop_year",
        "cop_first_year",
        "cop_lifetime",
        "cop_estimate_day",
        "cop_estimate_lifetime",
        "heat_total_kwh",
        "consumption_total_kwh",
        "built_year",
        "built_week",
        "product_code",
    }
)


def build_extra(
    model: str | None,
    *,
    has_display: bool,
    control_enabled: bool,
    page_count: int,
    read_failures: int,
    heatpump_model: str | None = None,
    serial: Any = None,
    display_firmware: Any = None,
    heatpump_firmware: Any = None,
    control_firmware: Any = None,
    history_page: bool | None = None,
    heat_counter: bool | None = None,
    consumption_counter: bool | None = None,
    consumption_modbus: bool | None = None,
    heat_total: bool | None = None,
    consumption_total: bool | None = None,
    cop_floor: bool | None = None,
    cop_stuck: bool | None = None,
    cop_implausible: bool | None = None,
    cop_integrated: bool | None = None,
    power_rows: bool | None = None,
    power_values: bool | None = None,
    menu_pages: int | None = None,
    menu_home: bool | None = None,
    menu_root: bool | None = None,
    settings_stem: str | None = None,
    heat_total_kwh: float | None = None,
    consumption_total_kwh: float | None = None,
    cop_day: Any = None,
    cop_year: Any = None,
    cop_first_year: Any = None,
    cop_lifetime: Any = None,
    cop_estimate_day: Any = None,
    cop_estimate_lifetime: Any = None,
    cop_estimate_carnot: bool | None = None,
) -> dict[str, Any]:
    """Build the integration specific part of the daily report.

    ``page_count`` is how many display pages are harvested, not which ones:
    the page names come from the unit's own menu and could carry an installer's
    text.
    """
    # The outdoor unit belongs in the model list beside the indoor one: the two
    # together are what an installation actually is.
    models = [model_slug(model)]
    outdoor = heatpump_slug(heatpump_model)
    if outdoor not in ("unknown", "other") and outdoor not in models:
        models.append(outdoor)

    payload: dict[str, Any] = {
        "models": models,
        "features": {
            # Modbus is always the base transport, the display is optional.
            "modbus": True,
            "display": bool(has_display),
            "control": bool(control_enabled),
            "pages": max(0, int(page_count)),
        },
        "errors": max(0, int(read_failures)),
    }

    # Whether a coefficient of performance is possible at all, and if not, why:
    # the history page is not among the harvested pages, the delivered heat
    # counter is not recognised on it, the display has no counter for consumed
    # energy, or Modbus register 62341 did not answer. Yes or no only, never
    # which page or what it says. ``consumption_modbus`` is whether the register
    # has answered at all since Home Assistant started, judged on the raw words
    # the coordinator keeps rather than on its decoded data, so CTC's marker for
    # a counter that is not fitted counts as an answer and a block that was
    # silent one round does not undo an earlier one. It is not whether the
    # register held a number: a register that answers zero is fitted and can be
    # found to be stuck, one that never answers cannot.
    #
    # The last two say whether each counter actually handed over a number at
    # the most recent read. Recognising the row and getting a reading out of it
    # are different things, and without that difference an installation with no
    # coefficient of performance looks the same whether the harvest never
    # delivered the row or the machine simply counts nothing. Both true and
    # still no figure means the consumed total sits below the floor the
    # calculation needs, which a clean zero does.
    flags = {
        "history_page": history_page,
        "heat_counter": heat_counter,
        "consumption_counter": consumption_counter,
        "consumption_modbus": consumption_modbus,
        "heat_total": heat_total,
        "consumption_total": consumption_total,
        # Why no figure: the counters have not counted far enough yet, one of
        # them stands still although the unit has been running, or the two give a
        # quotient no heat pump could produce. The first is a machine waiting,
        # the other two are faults.
        "cop_floor": cop_floor,
        "cop_stuck": cop_stuck,
        "cop_implausible": cop_implausible,
        # Whether the heat pump's page carries the two power rows at all, and
        # whether the figures rest on the integration's own sums of them rather
        # than on the machine's counters. The counters follow the outdoor unit:
        # the one 700 series in the fleet fills them, the 400 and 600 series
        # leave both at zero, and these two flags say whether the powers are
        # there to add up instead. A figure from that rougher basis is not to be
        # compared with a counter's as if it were the same measurement.
        "power_rows": power_rows,
        "power_values": power_values,
        "cop_integrated": cop_integrated,
        # Whether the estimated coefficient of performance below is worked out
        # from the refrigerant circuit (0.28.0) rather than from the display's
        # delivered heat: the rougher of the two, on a pump that reports no heat.
        "cop_estimate_carnot": cop_estimate_carnot,
    }
    payload["features"].update({k: bool(v) for k, v in flags.items() if v is not None})

    # Whether no page is harvested because nothing is ticked or because the
    # display's menu was never read, and if never, how far the last walk
    # through it got in this run: the home screen recognised, the operation
    # data menu entered. A count and two yes or no, never what the menu says.
    # The two steps are left out when no walk has been made in this run.
    if menu_pages is not None:
        payload["features"]["menu_pages"] = max(0, int(menu_pages))
    walk = {"menu_home": menu_home, "menu_root": menu_root}
    payload["features"].update({k: bool(v) for k, v in walk.items() if v is not None})

    # Which family a model the integration does not know belongs to, by its
    # settings file stem; a known model sends no such flag.
    family = family_feature(models[0], settings_stem)
    if family is not None:
        payload["features"][family] = True

    # The firmware in each board. Three separate versions, because a fault that
    # only shows up on one combination is exactly what this is for.
    firmwares = {
        "display": firmware_value(display_firmware),
        "heatpump": firmware_value(heatpump_firmware),
        "control": control_firmware_value(control_firmware),
    }
    firmwares = {k: str(v) for k, v in firmwares.items() if v is not None}
    if firmwares:
        payload["firmwares"] = firmwares

    # Numbers go where the backend already keeps numbers. The build week comes
    # from the serial number's middle group; the group that identifies the
    # machine itself is never touched.
    made = serial_made(serial)
    product = serial_product(serial)
    # The two lifetime counters themselves, and ONLY where they are wrong: a
    # counter standing still or a pair whose quotient is impossible. There is no
    # other way to tell a counter that reads zero from one the harvest never
    # delivered, and that difference decides whether anything can be fixed. A
    # machine that has merely not counted far enough yet sends no numbers.
    metrics = {
        "cop_day": cop_value(cop_day),
        "cop_year": cop_value(cop_year),
        "cop_first_year": cop_value(cop_first_year),
        "cop_lifetime": cop_value(cop_lifetime),
        # The estimated coefficient of performance the sensors show, today and
        # since the sums began, beside the counter based figures and never
        # instead of them: on a pump whose counters read zero it is the only one.
        "cop_estimate_day": cop_value(cop_estimate_day),
        "cop_estimate_lifetime": cop_value(cop_estimate_lifetime),
        "heat_total_kwh": energy_value(heat_total_kwh),
        "consumption_total_kwh": energy_value(consumption_total_kwh),
        "built_year": 2000 + int(made[:2]) if made else None,
        "built_week": int(made[2:]) if made else None,
        "product_code": product_code_value(product),
    }
    metrics = {k: v for k, v in metrics.items() if v is not None}
    if metrics:
        payload["metrics"] = metrics

    return payload
