"""The diagnostics download says how the integration sees the pump, and names no machine (R55).

A dump of stand-ins with the address, the serial number and the MAC address
planted everywhere they turn up for real: the entry's title and data, the
device's identifiers and link, the stored identity, a page caption, an alarm
text, the display client's message about an address it could not reach, and
the display's host name. After Home Assistant's redaction (copied below for a
suite without Home Assistant; the real one runs in
test_homeassistant_diagnostics.py) and the wash, nothing of them may be left
but the serial number's product and week groups.
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
import json
import pathlib
import re
from types import SimpleNamespace

import pytest

from conftest import load

ROOT = pathlib.Path(__file__).resolve().parent.parent
COMPONENT = ROOT / "custom_components" / "ctc_ecozenith"

HOST = "192.0.2.55"
SERIAL = "720825400001"
MAC = "02:00:00:00:00:01"
NOW = datetime(2026, 10, 9, 12, 0, tzinfo=UTC)


@pytest.fixture(scope="module")
def diagnostics_data():
    return load("diagnostics_data")


def redact(data, to_redact):
    """Home Assistant's async_redact_data, rule for rule, for a suite without it."""
    if not isinstance(data, (Mapping, list)):
        return data
    if isinstance(data, list):
        return [redact(item, to_redact) for item in data]
    redacted = {**data}
    for key, value in redacted.items():
        if value is None or (isinstance(value, str) and not value):
            continue
        if key in to_redact:
            redacted[key] = "**REDACTED**"
        elif isinstance(value, Mapping):
            redacted[key] = redact(value, to_redact)
        elif isinstance(value, list):
            redacted[key] = [redact(item, to_redact) for item in value]
    return redacted


class FakeStore:
    def __init__(self) -> None:
        self.data = None

    async def async_load(self):
        return self.data

    async def async_save(self, data):
        self.data = data

    def async_delay_save(self, build, delay):
        self.data = build()


def run(coro):
    return asyncio.run(coro)


# ------------------------------------------------------------ the stand-ins


MENU = [
    {
        "page": 118,
        "title": "Driftinfo",
        "screens": [117, 118],
        "route": [],
        "values": [
            {"key": "p118_utetemperatur", "label": "Utetemperatur", "screen": 118,
             "fmt": "%.1f°C", "vars": [3], "unit": "°C", "scale": 1.0},
        ],
    },
    {
        # A caption no display writes, carrying the serial number the way a
        # page could if an installer typed it in.
        "page": 128,
        "title": "System 7208-2540-0001",
        "screens": [128],
        "route": [[240, 250]],
        "values": [],
    },
]


def options(const):
    return {
        const.CONF_MENU: MENU,
        const.CONF_SLOW_PAGES: [MENU[0]],
        const.CONF_MENU_VERSION: "0.18.0",
        const.CONF_MENU_ROOT: 118,
        const.CONF_IDENTITY: {
            "serial": SERIAL,
            "mac": MAC,
            "display_firmware": "20260610",
            "heatpump_model": "EA720M",
        },
        const.CONF_SLOW_INTERVAL: 1800,
    }


def data(const):
    return {
        "host": HOST,
        const.CONF_MODBUS_PORT: 502,
        const.CONF_WEB_PORT: 80,
        const.CONF_SLAVE: 1,
        "model": "EcoZenith i255",
        "settings_name": "settings_ezi2xx.bin",
    }


def runtime(const, cop, transitions, alarms, identity):
    """An i255 with its history page harvested, control in force and an alarm on record."""
    tracker = cop.CopTracker(FakeStore())
    run(tracker.async_set_anchor((NOW - timedelta(days=400)).date()))
    run(tracker.async_record(22400.0, 9100.0, now=NOW - timedelta(days=1)))
    run(tracker.async_record(22499.0, 9116.0, now=NOW))

    watch = transitions.TransitionWatch()
    watch.observe(transitions.Sample(NOW - timedelta(minutes=30), hp_status=1, outdoor=7.2))
    watch.observe(transitions.Sample(NOW - timedelta(minutes=20), hp_status=3, outdoor=7.1))

    log = alarms.AlarmLog(FakeStore())
    log.note("[E040] Lågt flöde, enhet 7208 2540 0001", NOW - timedelta(hours=3), 6.5)
    log.note(None, NOW - timedelta(hours=2), 6.8)

    web = SimpleNamespace(
        interval=1800,
        last_update_success=True,
        patience=SimpleNamespace(failures=1, seconds=300),
        last_attempt=NOW,
        next_attempt=NOW + timedelta(minutes=5),
        last_harvest=NOW - timedelta(minutes=30),
        last_failure=(
            "no selected page of the display could be read: /vars/menu failed: Cannot "
            f"connect to host {HOST}:80 ssl:default [Connect call failed ('10.1.2.3', 80)]"
        ),
        pages_read=[118],
        pages_missed=[128],
        skips_in_a_row=2,
        last_skip_reason="panelen används av någon annan",
        home_page=1,
        _expected_page=1,
        read_at={"p118_utetemperatur": NOW - timedelta(minutes=30), "out": NOW, "in": NOW},
        data={"p118_utetemperatur": 7.2, "out": 22499.0, "in": 9116.0},
    )
    modbus = SimpleNamespace(
        update_interval=timedelta(seconds=30),
        last_update_success=True,
        _blocks=[(62000, 60), (61500, 100)],
        _missing=SimpleNamespace(missing={61500}),
        read_failures=3,
        unknown_codes={"system_status": 12},
        answered={62000, 62001, 62341},
        data={"outdoor_temp": 7.2, "system_status": "Okänd"},
    )
    return SimpleNamespace(
        modbus=modbus,
        web=web,
        web_client=SimpleNamespace(root=118, requests=412, taps=9, text_misses=0),
        control=SimpleNamespace(active={1010: 205}),
        control_enabled=True,
        identity=identity.Identity(
            serial=SERIAL, mac=MAC, display_firmware="20260610", bootloader="ctc0001.local",
            heatpump_model="EA720M", heatpump_firmware="20260522",
        ),
        device={
            "identifiers": {(const.DOMAIN, HOST)},
            "configuration_url": f"http://{HOST}:80",
            "name": "CTC EcoZenith i255",
            "manufacturer": "CTC / Enertech",
            "model": "EcoZenith i255 + EA720M",
            "serial_number": SERIAL,
            "sw_version": "20260610",
        },
        pages=[SimpleNamespace(page=118)],
        cop=tracker,
        energy_out=SimpleNamespace(key="out"),
        energy_in=SimpleNamespace(key="in"),
        consumption_snapshot=None,
        transitions=watch,
        alarms=log,
    )


def dump(diagnostics_data, data, options, runtime, **books):
    """What the shell hands over: built, redacted by key, washed."""
    found = diagnostics_data.build(data=data, options=options, runtime=runtime, **books)
    found["entry"]["title"] = f"EcoZenith i255 ({HOST})"
    washed = diagnostics_data.wash(
        redact(found, diagnostics_data.TO_REDACT),
        diagnostics_data.secrets_for(data, options, runtime),
    )
    # Plain JSON throughout, or the download fails in Home Assistant's encoder.
    return washed, json.dumps(washed, ensure_ascii=False)


@pytest.fixture()
def full(diagnostics_data, const, cop, identity):
    transitions = load("transitions")
    alarms = load("alarms")
    return dump(
        diagnostics_data,
        data(const),
        options(const),
        runtime(const, cop, transitions, alarms, identity),
        menu_tries=2,
        walked=True,
        swept=False,
    )


# ------------------------------------------------------ nothing names it


def _strings(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for key, item in value.items():
            yield key
            yield from _strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from _strings(item)


def test_no_sequence_number_survives_in_any_spelling(full):
    found, text = full
    for spelling in (SERIAL, "7208 2540 0001", "7208-2540-0001"):
        assert spelling not in text, spelling
    sequence = re.compile(r"(?<!\d)0001(?!\d)")
    assert not [s for s in _strings(found) if sequence.search(s)], "löpnumret står kvar"
    assert "ctc0001" not in text.lower(), "displayens värdnamn bär löpnumret"


def test_no_mac_address_survives(full):
    _found, text = full
    assert MAC not in text
    assert "020000000001" not in text.replace(":", "")


def test_no_address_survives(full):
    _found, text = full
    assert not re.search(r"192\.0\.2\.\d+", text)
    assert not re.search(r"(?<![\d.])10\.\d{1,3}\.\d{1,3}\.\d{1,3}", text)


def test_the_serial_number_is_cut_down_to_product_and_week(full):
    found, _text = full
    assert found["identity"]["product"] == "7208"
    assert found["identity"]["made"] == "2540"
    assert found["identity"]["serial_known"] is True
    assert found["identity"]["mac_known"] is True
    assert "serial" not in found["identity"] and "mac" not in found["identity"]
    # Spelt out in a caption, it keeps the same two groups and loses the third.
    names = [page["name"] for page in found["menu"]["pages"]]
    assert names == ["Driftinfo", "System 7208 2540 XXXX"]


def test_the_keys_that_name_a_machine_are_redacted(full):
    found, _text = full
    assert found["entry"]["title"] == "**REDACTED**"
    assert found["entry"]["data"]["host"] == "**REDACTED**"
    assert found["device"]["identifiers"] == "**REDACTED**"
    assert found["device"]["configuration_url"] == "**REDACTED**"
    assert "serial_number" not in found["device"]


# ----------------------------------------------- what a report needs is there


def test_the_menu_and_the_walks_of_this_run(full):
    found, _text = full
    menu = found["menu"]
    assert menu["version"] == "0.18.0"
    assert menu["root"] == 118
    assert menu["readings_this_run"] == 2
    assert menu["selected"] == [118]
    assert menu["harvested"] == [118]
    assert menu["system_information_walked_this_run"] is True
    assert menu["identity_screens_swept_this_run"] is False
    assert menu["pages"][0]["values"][0]["label"] == "Utetemperatur"
    # The options say where the menu is rather than carry it twice.
    assert found["entry"]["options"]["slow_pages"] == [118]


def test_the_harvest_says_when_each_row_was_read_and_why_it_gave_way(full):
    found, _text = full
    harvest = found["display"]["harvest"]
    assert harvest["read_at"]["p118_utetemperatur"] == (NOW - timedelta(minutes=30)).isoformat()
    assert harvest["skips_in_a_row"] == 2
    assert harvest["last_skip_reason"] == "panelen används av någon annan"
    assert harvest["patience"] == {"failures": 1, "next_wait": 300}
    assert harvest["pages_missed"] == [128]
    assert "Cannot connect to host **REDACTED**:80" in harvest["last_failure"]
    assert found["display"]["client"]["requests"] == 412


def test_modbus_shows_its_plan_what_it_learnt_and_what_it_could_not_name(full):
    found, _text = full
    modbus = found["modbus"]
    assert modbus["block_plan"] == [[62000, 60], [61500, 100]]
    assert modbus["missing_blocks"] == [61500]
    assert modbus["read_failures"] == 3
    assert modbus["unknown_codes"] == {"system_status": 12}
    assert modbus["registers_answered"] == 3
    assert modbus["interval"] == 30.0


def test_control_the_counters_the_transitions_and_the_alarms(full):
    found, _text = full
    assert found["control"] == {"enabled": True, "active": {"1010": 205}}
    cop = found["cop"]
    assert cop["totals"] == {"heat_kwh": 22499.0, "consumption_kwh": 9116.0}
    assert cop["tracker"]["daily_samples"] == 2
    assert len(cop["tracker"]["recent_samples"]) == 2
    assert cop["tracker"]["anchor"] == (NOW - timedelta(days=400)).date().isoformat()
    transitions = found["transitions"]
    assert transitions["running"] is True
    assert [item["kind"] for item in transitions["log"]] == ["kompressor_start"]
    episode = found["alarms"]["episodes"][0]
    assert episode["code"] == "E040"
    assert episode["end"] is not None
    assert "7208 2540 XXXX" in episode["text"]


def test_the_last_walk_through_the_menu_is_there_washed(diagnostics_data, const):
    # How far the last walk got (L6), with the display client's message about
    # an address it could not reach.
    walk = {
        "home_found": True,
        "tile_found": True,
        "root_entered": False,
        "pages": 0,
        "complete": False,
        "gaps": [],
        "error": "/sm/all failed: Cannot connect to host 10.1.2.3:80",
    }
    found, text = dump(diagnostics_data, data(const), options(const), None, menu_outcome=walk)
    last = found["menu"]["last_walk"]
    assert last["home_found"] is True and last["root_entered"] is False
    assert last["error"] == "/sm/all failed: Cannot connect to host **REDACTED**:80"
    assert "10.1.2.3" not in text
    # Before the first walk of the run there is nothing to say.
    found, _text = dump(diagnostics_data, data(const), options(const), None, menu_outcome={})
    assert found["menu"]["last_walk"] is None


# ------------------------------------------------- when things are missing


def test_an_entry_waiting_for_the_controller_still_gives_a_download(diagnostics_data, const):
    # No runtime: the set-up has not got through, which is when a download is
    # most wanted. The stored identity speaks for the unit, cut down the same way.
    found, text = dump(diagnostics_data, data(const), options(const), None)
    assert found["entry"]["loaded"] is False
    assert found["identity"]["product"] == "7208"
    assert found["display"] == {"harvest": None}
    assert found["modbus"] is None
    assert SERIAL not in text and MAC not in text and HOST not in text


def test_a_section_that_cannot_be_built_does_not_cost_the_download(
    diagnostics_data, const, cop, identity
):
    class Broken:
        @property
        def read_at(self):
            raise RuntimeError("half set up")

    pump = runtime(const, cop, load("transitions"), load("alarms"), identity)
    pump.web = Broken()
    found, _text = dump(diagnostics_data, data(const), options(const), pump)
    assert "unavailable" in found["display"]
    assert found["modbus"]["read_failures"] == 3, "resten av hämtningen står kvar"


def test_a_short_host_name_is_washed_as_a_word_only(diagnostics_data, const):
    # A host given by name is washed wherever it is spelt, but only as a whole
    # token: "ctc" as a host must not eat into ctc_ecozenith.
    entry = {**data(const), "host": "ctc"}
    found, _text = dump(diagnostics_data, entry, {"note": "ctc_ecozenith on ctc:80"}, None)
    assert found["entry"]["options"]["note"] == "ctc_ecozenith on **REDACTED**:80"


@pytest.mark.parametrize(
    "key", ["varmepump.hemma.se", "fd00::1234", f"{HOST}#2", "ctc0001.lan"]
)
def test_the_device_key_of_a_moved_entry_is_taken_out_as_well(diagnostics_data, const, key):
    # After a move the host is the new address and the key the one the entry
    # was created with: a name of the house's own DNS, an IPv6 address, or a
    # key of its own beside an entry that moved away. Neither the IPv4 wash
    # nor the host's own pattern catches the first two.
    assert const.CONF_DEVICE_KEY in diagnostics_data.TO_REDACT
    entry = {**data(const), "host": "192.0.2.77", const.CONF_DEVICE_KEY: key}
    note = {"note": f"device {key} answered", "key": key}
    found, text = dump(diagnostics_data, entry, note, None)
    assert found["entry"]["data"][const.CONF_DEVICE_KEY] == "**REDACTED**"
    assert found["entry"]["options"]["note"] == "device **REDACTED** answered"
    assert found["entry"]["options"]["key"] == "**REDACTED**"
    assert key not in text and key.lower() not in text.lower()
    assert "192.0.2" not in text and "#2" not in text


@pytest.mark.parametrize(
    ("said", "washed"),
    [
        # An address that ends a sentence: the full stop after it used to keep
        # it out of the IPv4 pattern, which took the dot for a fifth group.
        ("Cannot connect to host 10.1.2.3.", "Cannot connect to host **REDACTED**."),
        ("gave up on 198.51.100.7. Trying again", "gave up on **REDACTED**. Trying again"),
        # A MAC address with a colon after it, as a message that names a
        # device before saying what it did: the colon used to read as a
        # seventh group.
        ("02:00:00:00:00:02: no answer", "**REDACTED**: no answer"),
        ("02-00-00-00-00-02: no answer", "**REDACTED**: no answer"),
        ("seen 02:00:00:00:00:02.", "seen **REDACTED**."),
    ],
)
def test_an_address_before_a_full_stop_and_a_mac_before_a_colon_are_washed(
    diagnostics_data, said, washed
):
    # The patterns every installation is washed with, without its own secrets.
    assert diagnostics_data.Secrets().wash_text(said) == washed


def test_the_installations_own_address_and_name_are_washed_before_a_full_stop(
    diagnostics_data, const
):
    secrets = diagnostics_data.Secrets("varmepump.lan", SERIAL, MAC, HOST)
    assert secrets.wash_text(f"no answer from {HOST}.") == "no answer from **REDACTED**."
    assert secrets.wash_text("no answer from varmepump.lan.") == "no answer from **REDACTED**."
    assert secrets.wash_text(f"{MAC}: gone") == "**REDACTED**: gone"
    # Neither is washed out of something longer it is only the start of.
    assert secrets.wash_text("varmepump.lan.example") == "varmepump.lan.example"
    assert secrets.wash_text("version 1.2.3.4.5") == "version 1.2.3.4.5"
    assert secrets.wash_text("aa:bb:cc:dd:ee:ff:00") == "aa:bb:cc:dd:ee:ff:00"


def test_a_message_with_both_shapes_leaves_the_download_washed(diagnostics_data, const):
    note = {"note": f"Cannot reach {HOST}. {MAC}: no answer from 10.1.2.3."}
    found, text = dump(diagnostics_data, data(const), note, None)
    assert found["entry"]["options"]["note"] == (
        "Cannot reach **REDACTED**. **REDACTED**: no answer from **REDACTED**."
    )
    assert HOST not in text and MAC not in text and "10.1.2.3" not in text


# ------------------------------------------------------------- the shell


def test_the_shell_redacts_the_keys_and_washes_the_rest():
    source = (COMPONENT / "diagnostics.py").read_text(encoding="utf-8")
    assert "async_redact_data(found, TO_REDACT)" in source
    assert "secrets_for(entry.data, entry.options, runtime)" in source
    assert "wash(" in source


def test_the_redacted_keys_are_the_ones_that_name_a_machine(diagnostics_data):
    assert {"serial", "mac", "host", "title", "configuration_url", "identifiers"} <= (
        diagnostics_data.TO_REDACT
    )


def test_the_diagnostics_module_is_free_of_home_assistant():
    source = (COMPONENT / "diagnostics_data.py").read_text(encoding="utf-8")
    assert "homeassistant" not in source


# --------------------------------------------------------- the issue form


def test_the_bug_report_form_asks_for_the_diagnostics_file():
    form = (ROOT / ".github" / "ISSUE_TEMPLATE" / "bug_report.yml").read_text(encoding="utf-8")
    assert form.startswith("name: Bug report")
    assert "id: diagnostics" in form
    assert "Download diagnostics" in form
    assert "CTC Local Easyconf" in form


def test_the_texts_offer_the_download_only_where_home_assistant_does():
    """No download in the menu of an entry still waiting for the heat pump (F6.3).

    Home Assistant's frontend offers Download diagnostics only for a loaded
    entry, so a promise that it works while the entry waits sends the user
    looking for a menu item that is not there, in the very state a report is
    most likely written from. The texts say what to send instead.
    """
    form = (ROOT / ".github" / "ISSUE_TEMPLATE" / "bug_report.yml").read_text(encoding="utf-8")
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    flat = " ".join(readme.split())
    for text in (" ".join(form.split()), flat):
        assert "works while the integration is still waiting" not in text
        assert "only while the" in text and "loaded" in text
        assert "reason shown under the entry" in text
    assert "/api/diagnostics/config_entry/" in flat
