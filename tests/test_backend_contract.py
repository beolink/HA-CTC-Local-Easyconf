"""Every field the daily report builds must survive the trip to stats.rnet.se (roadmap R63).

test_stats.py and test_report_keys.py hold the report to its closed lists and
to what stats.py lets through. Neither knew what the far end does with it, and
the far end is quiet about it: an unknown top-level key rejects the whole
report, but a metric it does not list is dropped without a word, a number
outside a metric's range is clamped to the edge, and a model or a feature key
outside its pattern simply goes missing. The Nibe integration's version of this
test found three fields that never arrived as sent, among them a product code
stored as 9999 because the backend's range ends there. 0.16.0 added the flags
cop_floor, cop_stuck and cop_implausible and, on a fault, the two lifetime
totals heat_total_kwh and consumption_total_kwh, and nothing held those to the
backend's rules either.

The rules below are copied from the backend, beolink/stats, as they stand on
2026-10-13: lib/ingest.ts (ALLOWED_KEYS, ACCEPTED_SCHEMAS, METRICS,
FIRMWARE_SLOTS, HA_TYPES, the regular expressions and the limits in validate())
and lib/entry-state.ts (ENTRY_STATES). When the backend changes, change them
here in the same round; when this integration needs a new field, the backend
takes it first and ships first, because a client that sends a key the server
does not know has every report refused. backend_validate() repeats validate()
decision for decision, JavaScript's rounding included, and the reports of three
installations like the ones in the fleet go through it after the same JSON
round trip the wire gives them: an i255 with an EcoAir 720M like VSH, an i550
Pro whose counters stand still like PT's, and an i360 on the older display
whose consumed energy comes from Modbus. Every field has to come out as it went
in, in lower case where the backend lower-cases, with one deliberate exception:
the Python version, which the backend cuts to major.minor.

stats.py itself is not this integration's to change. It is the same file, byte
for byte, in all seven beolink integrations, so it is pinned by its hash, and
its schema version and the keys it lets an integration add are pinned exactly.
"""

from __future__ import annotations

import ast
import hashlib
import json
import math
import pathlib
import re

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
COMPONENT = ROOT / "custom_components" / "ctc_ecozenith"
STATS_PY = COMPONENT / "stats.py"

# ---------------------------------------------------------------- the backend
# Copied from beolink/stats lib/ingest.ts and lib/entry-state.ts, 2026-10-13.

#: lib/ingest.ts SCHEMA_VERSION and ACCEPTED_SCHEMAS.
BACKEND_SCHEMA_VERSION = 3
ACCEPTED_SCHEMAS = {1, 2, 3}
#: lib/ingest.ts MAX_BODY, enforced by app/api/v1/report/route.ts.
MAX_BODY = 4096

#: lib/ingest.ts ALLOWED_KEYS. Anything else rejects the whole report.
ALLOWED_KEYS = {
    "schema", "install_id", "integration", "version", "ha_version", "ha_type",
    "python_version", "country", "language", "entities", "devices", "models",
    "features", "errors", "lat", "lon", "metrics", "firmware", "firmwares",
    "ha_id", "log_errors", "log_warnings", "entry_state",
}

#: lib/ingest.ts METRICS, the entries this integration sends. A metric not in
#: the backend's list is dropped in silence; one outside its range is clamped.
METRICS = {
    "cop_day": {"max": 10, "min": 0.5, "dec": 2},
    "cop_year": {"max": 10, "min": 0.5, "dec": 2},
    "cop_first_year": {"max": 10, "min": 0.5, "dec": 2},
    "cop_lifetime": {"max": 10, "min": 0.5, "dec": 2},
    # beolink/stats 9a026db, 2026-10-21: the estimate, taken before 0.28.1 sends it.
    "cop_estimate_day": {"max": 10, "min": 0.5, "dec": 2},
    "cop_estimate_lifetime": {"max": 10, "min": 0.5, "dec": 2},
    "heat_total_kwh": {"max": 1000000, "dec": 1},
    "consumption_total_kwh": {"max": 1000000, "dec": 1},
    "built_year": {"max": 2100, "min": 1990, "dec": 0},
    "built_week": {"max": 53, "min": 1, "dec": 0},
    "product_code": {"max": 9999, "min": 1000, "dec": 0},
}

#: lib/ingest.ts FIRMWARE_SLOTS.
FIRMWARE_SLOTS = {"display", "heatpump", "control", "inverter"}
#: lib/ingest.ts HA_TYPES.
HA_TYPES = {"os", "supervised", "container", "core", "unknown"}
#: lib/entry-state.ts ENTRY_STATES.
ENTRY_STATES = {
    "loaded", "setup_error", "migration_error", "setup_retry",
    "not_loaded", "failed_unload", "setup_in_progress", "unload_in_progress",
}

RE_SLUG = re.compile(r"^[a-z][a-z0-9_]{2,31}$")
RE_UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I)
RE_HAID = re.compile(r"^[0-9a-f]{32}$")
RE_VERSION = re.compile(r"^[0-9A-Za-z][0-9A-Za-z._+-]{0,31}$")
RE_PY = re.compile(r"^3\.\d{1,2}")
RE_CC = re.compile(r"^[A-Za-z]{2}$")
RE_LANG = re.compile(r"^[a-z]{2}$")
RE_MODEL = re.compile(r"^[a-z0-9][a-z0-9._-]{0,31}$")
RE_FEATURE = re.compile(r"^[a-z][a-z0-9_]{0,31}$")

#: validate(): b.models.slice(0, 8), entries of features slice(0, 24), each
#: number through clampInt(v, 100000), firmwares slice(0, 8).
MAX_MODELS = 8
MAX_FEATURES = 24
MAX_FEATURE_VALUE = 100000
MAX_FIRMWARES = 8
#: POS_DECIMALS: one decimal of latitude and longitude.
POS_DECIMALS = 1


def _is_number(value) -> bool:
    """JavaScript's typeof v === 'number' after JSON: a bool is not one."""
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _js_round(value: float) -> float:
    """Math.round: halves go up, unlike Python's round()."""
    return math.floor(value + 0.5)


def _clamp_metric(value, spec):
    if not _is_number(value) or not math.isfinite(value):
        return None
    bounded = min(spec["max"], max(spec.get("min", 0), value))
    factor = 10 ** spec["dec"]
    return _js_round(bounded * factor) / factor


def _clamp_int(value, ceiling):
    if not _is_number(value) or not math.isfinite(value):
        return None
    number = math.trunc(value)
    return 0 if number < 0 else min(number, ceiling)


def _snap(value, limit):
    if not _is_number(value) or not math.isfinite(value) or not -limit <= value <= limit:
        return None
    return _js_round(value * 10**POS_DECIMALS) / 10**POS_DECIMALS


def _matching(value, pattern):
    return value if isinstance(value, str) and pattern.match(value) else None


def backend_validate(body: dict) -> tuple[str | None, dict]:
    """lib/ingest.ts validate(): the reason a report is refused, or what is stored.

    The install id and the Home Assistant id are hashed with the server's
    pepper before they are stored; here they are kept as they came, so a test
    can see whether they were accepted.
    """
    for key in body:
        if key not in ALLOWED_KEYS:
            return f"unknown_field:{key[:24]}", {}
    if not _is_number(body.get("schema")) or body["schema"] not in ACCEPTED_SCHEMAS:
        return "bad_schema_version", {}
    integration = body["integration"].lower() if isinstance(body.get("integration"), str) else ""
    if not RE_SLUG.match(integration):
        return "bad_integration", {}
    if not isinstance(body.get("install_id"), str) or not RE_UUID.match(body["install_id"]):
        return "bad_install_id", {}
    if not isinstance(body.get("version"), str) or not RE_VERSION.match(body["version"]):
        return "bad_version", {}

    models = []
    if isinstance(body.get("models"), list):
        for model in body["models"][:MAX_MODELS]:
            if isinstance(model, str) and RE_MODEL.match(model.lower()):
                models.append(model.lower())

    features = {}
    if isinstance(body.get("features"), dict):
        for name, flag in list(body["features"].items())[:MAX_FEATURES]:
            key = name.lower()
            if not RE_FEATURE.match(key):
                continue
            if isinstance(flag, bool):
                features[key] = flag
            elif _is_number(flag) and math.isfinite(flag):
                features[key] = _clamp_int(flag, MAX_FEATURE_VALUE)

    metrics = {}
    if isinstance(body.get("metrics"), dict):
        for name, number in body["metrics"].items():
            spec = METRICS.get(name.lower())
            if spec is None:
                continue
            clamped = _clamp_metric(number, spec)
            if clamped is not None:
                metrics[name.lower()] = clamped

    firmwares = {}
    if isinstance(body.get("firmwares"), dict):
        for slot_name, given in list(body["firmwares"].items())[:MAX_FIRMWARES]:
            slot = slot_name.lower()
            if slot not in FIRMWARE_SLOTS:
                continue
            version = _matching(json.dumps(given) if _is_number(given) else given, RE_VERSION)
            if version:
                firmwares[slot] = version

    python = _matching(body.get("python_version"), RE_PY)
    ha_id = body["ha_id"].lower() if isinstance(body.get("ha_id"), str) else ""
    language = body.get("language")
    entry_state = body.get("entry_state")
    country = _matching(body.get("country"), RE_CC)
    return None, {
        "integration": integration,
        "install_id": body["install_id"],
        "version": body["version"],
        "ha_version": _matching(body.get("ha_version"), RE_VERSION),
        "ha_type": body.get("ha_type") if body.get("ha_type") in HA_TYPES else None,
        "python_version": ".".join(python.split(".")[:2]) if python else None,
        "country": country.upper() if country else None,
        "language": _matching(language.lower() if isinstance(language, str) else None, RE_LANG),
        "firmware": _matching(body.get("firmware"), RE_VERSION),
        "ha_id": ha_id if RE_HAID.match(ha_id) else None,
        "log_errors": _clamp_int(body.get("log_errors"), 1000000),
        "log_warnings": _clamp_int(body.get("log_warnings"), 1000000),
        "entry_state": entry_state if entry_state in ENTRY_STATES else None,
        "firmwares": firmwares,
        "entities": _clamp_int(body.get("entities"), 100000),
        "devices": _clamp_int(body.get("devices"), 10000),
        "errors": _clamp_int(body.get("errors"), 1000000),
        "models": models,
        "features": features,
        "lat": _snap(body.get("lat"), 90),
        "lon": _snap(body.get("lon"), 180),
        "metrics": metrics,
    }


# ------------------------------------------------------------- stats.py

#: sha256 of custom_components/ctc_ecozenith/stats.py, line endings as git
#: keeps them. The reference copy is client/homeassistant/stats.py in
#: beolink/stats.
STATS_PY_SHA256 = "921b1744a832d058045e4fbe6504f6b83e64167f85e9f3c638b1dd0860468fce"


def _stats_py() -> ast.Module:
    return ast.parse(STATS_PY.read_text(encoding="utf-8"))


def _constant(name: str):
    """A module level constant of stats.py, read without importing it.

    stats.py imports Home Assistant, which this suite runs without.
    """
    for node in _stats_py().body:
        targets = node.targets if isinstance(node, ast.Assign) else (
            [node.target] if isinstance(node, ast.AnnAssign) else []
        )
        if any(getattr(target, "id", None) == name for target in targets):
            value = node.value
            if isinstance(value, ast.Call) and getattr(value.func, "id", None) == "frozenset":
                return frozenset(ast.literal_eval(value.args[0]))
            return ast.literal_eval(value)
    raise AssertionError(f"{name} not found in stats.py")


def _payload_keys() -> set[str]:
    """Every top-level key StatsReporter.async_payload writes itself."""
    function = next(
        node for node in ast.walk(_stats_py())
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "async_payload"
    )
    keys: set[str] = set()
    for node in ast.walk(function):
        if isinstance(node, ast.AnnAssign) and getattr(node.target, "id", None) == "payload":
            keys |= {key.value for key in node.value.keys}
        if (
            isinstance(node, ast.Subscript)
            and getattr(node.value, "id", None) == "payload"
            and isinstance(node.ctx, ast.Store)
            and isinstance(node.slice, ast.Constant)
        ):
            keys.add(node.slice.value)
    return keys


def test_stats_py_is_the_shared_file_unchanged():
    data = STATS_PY.read_bytes().replace(b"\r\n", b"\n")
    assert hashlib.sha256(data).hexdigest() == STATS_PY_SHA256, (
        "stats.py is shared byte for byte by the seven beolink integrations (ems, "
        "ctc_ecozenith, intellipool, ferroamp_control, ctek_ccu, miele_totalhome, "
        "nibe_local_easyconf), with the reference copy in beolink/stats "
        "client/homeassistant/stats.py. Do not change it here: change the reference "
        "copy, copy it into all seven repositories, ship the backend first if the "
        "payload changed, and only then update STATS_PY_SHA256 in this test."
    )


def test_stats_py_speaks_schema_three():
    assert _constant("SCHEMA_VERSION") == 3
    assert _constant("SCHEMA_VERSION") == BACKEND_SCHEMA_VERSION
    assert _constant("SCHEMA_VERSION") in ACCEPTED_SCHEMAS


def test_stats_py_lets_exactly_these_keys_through_from_the_integration():
    assert _constant("EXTRA_KEYS") == (
        "models", "features", "errors", "metrics", "firmware", "firmwares",
    )


def test_stats_py_and_the_backend_agree_on_everything_else_it_sends():
    sent = _payload_keys() | set(_constant("EXTRA_KEYS"))
    assert sent <= ALLOWED_KEYS, f"the backend refuses every report over {sent - ALLOWED_KEYS}"
    assert _constant("ENTRY_STATES") == ENTRY_STATES
    assert {*_constant("_INSTALL_TYPES").values(), "unknown"} <= HA_TYPES
    assert _constant("POSITION_DECIMALS") == POS_DECIMALS
    assert _constant("ENDPOINT") == "https://stats.rnet.se/api/v1/report"


def test_the_integrations_own_name_and_version_pass():
    manifest = json.loads((COMPONENT / "manifest.json").read_text(encoding="utf-8"))
    assert RE_SLUG.match(manifest["domain"])
    assert RE_VERSION.match(manifest["version"])


# ------------------------------------------------- what the report carries


def test_every_metric_the_report_can_carry_is_one_the_backend_keeps(stats_extra):
    unknown = stats_extra.METRIC_KEYS - set(METRICS)
    assert not unknown, (
        f"the backend drops {sorted(unknown)} without a word: add them to lib/ingest.ts "
        "METRICS first, then to METRICS here"
    )


def test_every_flag_the_report_can_carry_fits_the_backends_pattern(stats_extra):
    for key in stats_extra.FEATURE_KEYS:
        assert RE_FEATURE.match(key), key
    longest_family = stats_extra.FAMILY_PREFIX + "a" * 16
    assert RE_FEATURE.match(longest_family)
    # Every flag at once, and the family flag beside them, is still within the
    # 24 the backend reads; the rest would be cut off in the order they came.
    assert len(stats_extra.FEATURE_KEYS) + 1 <= MAX_FEATURES


def test_every_model_slug_fits_the_backends_pattern(stats_extra):
    for slug in {*stats_extra.MODEL_SLUGS.values(), "other", "unknown"}:
        assert RE_MODEL.match(slug) and slug == slug.lower(), slug
    for outdoor in ("EA720M", "EA420", "EA610M", "EP615", "EA622M"):
        slug = stats_extra.heatpump_slug(outdoor)
        assert RE_MODEL.match(slug) and slug == slug.lower(), slug


#: The houses' installations as the report sees them, every builder argument
#: set the way _stats_extra_for in __init__.py sets it, and both lifetime totals
#: given in all three (the builder takes them; __init__.py passes them only on
#: a fault). The serial is the tests' made-up one.
VSH = {
    "model": "EcoZenith i255", "has_display": True, "control_enabled": True,
    "page_count": 7, "read_failures": 0, "heatpump_model": "EA720M",
    "serial": "720825400001", "display_firmware": "20260610",
    "heatpump_firmware": "20260522", "control_firmware": 610,
    "history_page": True, "heat_counter": True, "consumption_counter": True,
    "consumption_modbus": True, "heat_total": True, "consumption_total": True,
    "cop_floor": False, "cop_stuck": False, "cop_implausible": False,
    "menu_pages": 7, "menu_home": True, "menu_root": True, "settings_stem": "ezi2xx",
    "heat_total_kwh": 22499.4, "consumption_total_kwh": 9116.0,
    "cop_day": 2.92, "cop_year": 2.61, "cop_first_year": 2.53, "cop_lifetime": 2.48,
}
#: Both counters at zero after twenty thousand hours: stuck, so the totals go.
PT = {
    "model": "EcoZenith i550 Pro", "has_display": True, "control_enabled": False,
    "page_count": 4, "read_failures": 2, "heatpump_model": "EA420",
    "serial": None, "display_firmware": "20250925", "heatpump_firmware": None,
    "control_firmware": 925,
    "history_page": True, "heat_counter": True, "consumption_counter": True,
    "consumption_modbus": True, "heat_total": True, "consumption_total": True,
    "cop_floor": False, "cop_stuck": True, "cop_implausible": False,
    "menu_pages": 4, "menu_home": True, "menu_root": True, "settings_stem": "ezi5xx",
    "heat_total_kwh": 0.0, "consumption_total_kwh": 0.0,
    "cop_day": None, "cop_year": None, "cop_first_year": None, "cop_lifetime": None,
}
#: The older display: delivered heat on the display, consumed energy from
#: Modbus 62341, and a quotient no heat pump gives, so the totals go.
I360 = {
    "model": "EcoZenith i360", "has_display": True, "control_enabled": False,
    "page_count": 1, "read_failures": 1, "heatpump_model": "EA610M",
    "serial": "720825400001", "display_firmware": "20240115", "heatpump_firmware": None,
    "control_firmware": 412,
    "history_page": True, "heat_counter": True, "consumption_counter": False,
    "consumption_modbus": True, "heat_total": True, "consumption_total": True,
    "cop_floor": False, "cop_stuck": False, "cop_implausible": True,
    "menu_pages": 6, "menu_home": True, "menu_root": True, "settings_stem": None,
    "heat_total_kwh": 12000.0, "consumption_total_kwh": 800.5,
    "cop_day": None, "cop_year": None, "cop_first_year": None, "cop_lifetime": 2.5,
    # The estimate from the refrigerant circuit, as on a 600 series without a flow meter.
    "cop_estimate_day": 3.16, "cop_estimate_lifetime": 3.614, "cop_estimate_carnot": True,
}
#: A model the integration does not know: reported as other, with its family.
UNKNOWN = {**VSH, "model": "CTC (ezi9xx)", "settings_stem": "ezi9xx"}

CASES = {"vsh": VSH, "pt": PT, "i360": I360, "unknown": UNKNOWN}

#: What stats.py puts around the integration's part, as on a Home Assistant
#: OS in Sweden. The address-like fields are what stats.py makes of them: a
#: random uuid, a 32 digit hash, a position rounded to one decimal.
BASE = {
    "schema": 3,
    "install_id": "0f8fad5b-d9cb-469f-a165-70867728950e",
    "integration": "ctc_ecozenith",
    "version": "0.20.0",
    "ha_version": "2026.10.0",
    "ha_type": "os",
    "python_version": "3.14.7",
    "country": "SE",
    "language": "sv",
    "entities": 201,
    "devices": 1,
    "lat": 59.4,
    "lon": 18.1,
    "ha_id": "0123456789abcdef0123456789abcdef",
    "entry_state": "loaded",
    "log_errors": 0,
    "log_warnings": 3,
}


def report(stats_extra, case: dict) -> dict:
    """The payload as stats.py sends it, after the JSON round trip of the wire."""
    extra_keys = _constant("EXTRA_KEYS")
    payload = dict(BASE)
    for key, value in stats_extra.build_extra(**case).items():
        if key in extra_keys:
            payload[key] = value
    payload = {key: value for key, value in payload.items() if value is not None}
    return json.loads(json.dumps(payload))


def test_the_base_fields_here_are_the_ones_stats_py_writes():
    assert set(BASE) == _payload_keys()


@pytest.mark.parametrize("name", sorted(CASES))
def test_the_report_is_accepted(stats_extra, name):
    reason, _stored = backend_validate(report(stats_extra, CASES[name]))
    assert reason is None


@pytest.mark.parametrize("name", sorted(CASES))
def test_every_field_survives_as_it_was_sent(stats_extra, name):
    sent = report(stats_extra, CASES[name])
    _reason, stored = backend_validate(sent)
    for key, nothing in (("models", []), ("features", {}), ("metrics", {}), ("firmwares", {})):
        assert stored[key] == sent.get(key, nothing), (
            f"{key}: sent {sent.get(key)!r}, stored {stored[key]!r}"
        )
    assert stored["errors"] == sent["errors"]
    for key in BASE:
        if key in ("schema", "python_version"):
            continue
        assert stored[key] == sent[key], key
    # The one field the backend cuts on purpose: Python's version to major.minor.
    assert stored["python_version"] == "3.14"


@pytest.mark.parametrize("name", sorted(CASES))
def test_nothing_in_the_report_depends_on_the_backend_lowering_it(stats_extra, name):
    sent = report(stats_extra, CASES[name])
    assert sent["models"] == [model.lower() for model in sent["models"]]
    assert all(key == key.lower() for key in sent["features"])
    assert all(key == key.lower() for key in sent.get("metrics", {}))
    assert all(key == key.lower() for key in sent.get("firmwares", {}))


@pytest.mark.parametrize("name", sorted(CASES))
def test_the_report_fits_the_backends_size_limit(stats_extra, name):
    # aiohttp's json= writes json.dumps with its default separators.
    assert len(json.dumps(report(stats_extra, CASES[name])).encode()) <= MAX_BODY


def test_the_three_houses_send_what_they_are(stats_extra):
    vsh, pt, i360 = (report(stats_extra, case) for case in (VSH, PT, I360))
    assert vsh["models"] == ["i255", "ea720m"]
    assert vsh["metrics"] == {
        "cop_day": 2.92, "cop_year": 2.61, "cop_first_year": 2.53, "cop_lifetime": 2.48,
        "heat_total_kwh": 22499.4, "consumption_total_kwh": 9116.0,
        "built_year": 2025, "built_week": 40, "product_code": 7208,
    }
    assert vsh["firmwares"] == {"display": "20260610", "heatpump": "20260522", "control": "610"}
    assert pt["models"] == ["i550", "ea420"]
    assert pt["features"]["cop_stuck"] is True
    assert pt["metrics"] == {"heat_total_kwh": 0.0, "consumption_total_kwh": 0.0}
    assert i360["models"] == ["i360", "ea610m"]
    assert i360["features"]["cop_implausible"] is True
    assert i360["features"]["consumption_counter"] is False
    assert i360["metrics"]["consumption_total_kwh"] == 800.5
    unknown = report(stats_extra, UNKNOWN)
    assert unknown["models"] == ["other", "ea720m"]
    assert unknown["features"]["family_ezi9xx"] is True


# --------------------------------------------- the edges of each number

EDGES = [
    ({"cop_day": 0.5, "cop_lifetime": 10.0}, {"cop_day": 0.5, "cop_lifetime": 10.0}),
    ({"cop_year": 3.456}, {"cop_year": 3.46}),
    ({"heat_total_kwh": 0.0, "consumption_total_kwh": 1_000_000.0},
     {"heat_total_kwh": 0.0, "consumption_total_kwh": 1_000_000.0}),
    ({"heat_total_kwh": 123.44}, {"heat_total_kwh": 123.4}),
    # Serials built for their edges, each with the made-up sequence number 0001:
    # the lowest and highest product group, year and week the builder takes.
    ({"serial": "100000010001"}, {"built_year": 2000, "built_week": 1, "product_code": 1000}),
    ({"serial": "999999530001"}, {"built_year": 2099, "built_week": 53, "product_code": 9999}),
]


@pytest.mark.parametrize(("given", "metrics"), EDGES)
def test_a_number_at_the_edge_of_what_the_builder_accepts_is_kept(stats_extra, given, metrics):
    case = {
        "model": "EcoZenith i255", "has_display": True, "control_enabled": False,
        "page_count": 1, "read_failures": 0, **given,
    }
    sent = report(stats_extra, case)
    assert sent["metrics"] == metrics
    _reason, stored = backend_validate(sent)
    assert stored["metrics"] == metrics


#: Just past what the backend keeps, or what the builder takes: the builder
#: leaves such a number out, it never sends it to be clamped.
BEYOND = [
    {"cop_day": 0.49},
    {"cop_year": 10.01},
    {"heat_total_kwh": -0.1},
    {"consumption_total_kwh": 1_000_000.1},
    {"serial": "720800000001"},  # week 00
    {"serial": "720899540001"},  # week 54
]


def _named(given: dict) -> str:
    return ",".join(f"{key}={value}" for key, value in given.items())


@pytest.mark.parametrize("given", BEYOND, ids=_named)
def test_a_number_past_the_edge_is_left_out_rather_than_clamped(stats_extra, given):
    case = {
        "model": "EcoZenith i255", "has_display": True, "control_enabled": False,
        "page_count": 1, "read_failures": 0, **given,
    }
    sent = report(stats_extra, case)
    _reason, stored = backend_validate(sent)
    assert stored["metrics"] == sent.get("metrics", {})


def test_a_product_group_with_a_leading_zero_is_not_changed_by_the_backend(stats_extra):
    """A product group under 1000 is left out, never clamped to another product's 1000.

    build_extra used to send int() of the serial's first group, so 0712 went as
    712 and lib/ingest.ts stored it as 1000 without an error. No CTC serial
    seen so far starts with a 0 (7208, 1002, 7312); one that did now sends no
    product code, and its build year and week still go.
    """
    sent = report(stats_extra, {
        "model": "EcoZenith i255", "has_display": True, "control_enabled": False,
        "page_count": 1, "read_failures": 0, "serial": "071225400001",
    })
    _reason, stored = backend_validate(sent)
    assert stored["metrics"].get("product_code") == sent["metrics"].get("product_code")
    assert "product_code" not in sent["metrics"]
    assert sent["metrics"] == {"built_year": 2025, "built_week": 40}


@pytest.mark.parametrize(
    ("product", "code"),
    [("1000", 1000), ("9999", 9999), ("7208", 7208), ("0999", None), ("0712", None),
     ("0000", None), ("", None), (None, None)],
)
def test_only_a_product_code_the_backend_keeps_as_it_is_is_sent(stats_extra, product, code):
    assert stats_extra.product_code_value(product) == code
    backend = METRICS["product_code"]
    kept = (stats_extra.PRODUCT_CODE_MIN, stats_extra.PRODUCT_CODE_MAX)
    assert kept == (backend["min"], backend["max"])


# ------------------------------------------- the copy of the backend itself


def test_the_copy_of_validate_refuses_what_the_backend_refuses():
    # The checks above are only as good as backend_validate, so it is held to
    # the backend's own refusals and clamps on a report that breaks them.
    good = {**BASE, "models": ["i255"], "features": {"modbus": True}, "metrics": {"cop_day": 3.0}}
    assert backend_validate(good)[0] is None
    assert backend_validate({**good, "serial": "x"})[0] == "unknown_field:serial"
    assert backend_validate({**good, "schema": 4})[0] == "bad_schema_version"
    assert backend_validate({**good, "integration": "ctc-ecozenith"})[0] == "bad_integration"
    assert backend_validate({**good, "install_id": "nope"})[0] == "bad_install_id"
    assert backend_validate({**good, "version": "-1"})[0] == "bad_version"
    _r, stored = backend_validate({
        **good,
        "models": ["I255", "has space", *(f"m{n}" for n in range(10))],
        "features": {"Pages": 3.7, "bad-key": True, "neg": -5, "big": 10**9},
        "metrics": {"product_code": 65443, "cop_day": 0.1, "unknown": 1.0, "cop_year": 2.346},
        "firmwares": {"display": 20260610, "mainboard": "1", "control": "bad version"},
        "lat": 59.37, "lon": 200.0, "country": "se", "python_version": "3.13.2",
        "entry_state": "broken", "ha_id": "ABCDEF0123456789ABCDEF0123456789",
    })
    assert stored["models"] == ["i255", "m0", "m1", "m2", "m3", "m4", "m5"]
    assert stored["features"] == {"pages": 3, "neg": 0, "big": MAX_FEATURE_VALUE}
    assert stored["metrics"] == {"product_code": 9999, "cop_day": 0.5, "cop_year": 2.35}
    assert stored["firmwares"] == {"display": "20260610"}
    assert (stored["lat"], stored["lon"], stored["country"]) == (59.4, None, "SE")
    assert stored["python_version"] == "3.13"
    assert stored["entry_state"] is None
    assert stored["ha_id"] == "abcdef0123456789abcdef0123456789"
