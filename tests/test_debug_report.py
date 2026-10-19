"""The voluntary debug report (debug_report.py, switch.py), worked on stand-ins.

Off until the owner switches it on, under Configure or with its switch on the
CTC page, which are one setting; nothing is collected or sent while it is off.
On, a report goes at once, a short while after each set-up and every half hour,
and it is exactly the diagnostics download with the integration's own warnings
and errors beside it, washed of every address, MAC address, host name and the
serial number's running number, with the stand-ins test_diagnostics.py plants
them with. The installation is named by the anonymous statistics' own id,
read from their store and made there where it is missing. Switching it, with
the switch or in Configure alone, reloads nothing: a reload can lock the
pump's one Modbus place. An answer that is not "stored" and a failure on the
wire never raise, and keep the lines for the next report.

The calls into Home Assistant are the module's small functions under "Home
Assistant", replaced here; the run under a real core, with the real
diagnostics, the real store and the real reload listener, is
test_homeassistant_debug_report.py.
"""

from __future__ import annotations

import ast
import asyncio
from datetime import timedelta
import json
import logging
import pathlib
import re
from types import SimpleNamespace
import uuid

import pytest

from conftest import COMPONENT, load
import ha_stub
from test_diagnostics import HOST, MAC, SERIAL, data, dump, options, runtime

ROOT = pathlib.Path(__file__).resolve().parent.parent
NAMESPACE = "custom_components.ctc_ecozenith"
SEQUENCE = re.compile(r"(?<!\d)0001(?!\d)")
#: A second heat pump of the same integration, whose name a line can carry.
OTHER_HOST = "pump-two.lan"


@pytest.fixture(scope="module")
def debug_report():
    return load("debug_report")


@pytest.fixture(scope="module")
def diagnostics_data():
    return load("diagnostics_data")


def _source(name: str) -> str:
    return (COMPONENT / name).read_text(encoding="utf-8")


def run(coro):
    return asyncio.run(coro)


# ------------------------------------------------------------ the stand-ins


class FakeEntry:
    """A config entry: its data, its options, and the listeners of its writes."""

    def __init__(self, const, entry_id="entry", host=HOST, opts=None, runtime_data=None):
        self.entry_id = entry_id
        self.data = {**data(const), "host": host}
        self.options = dict(opts if opts is not None else options(const))
        self.title = f"EcoZenith i255 ({host})"
        self.disabled_by = None
        self.update_listeners: list = []
        if runtime_data is not None:
            self.runtime_data = runtime_data

    def add_update_listener(self, listener):
        self.update_listeners.append(listener)
        return lambda: self.update_listeners.remove(listener)


class FakeConfigEntries:
    """Home Assistant's config entries as far as the report and the switch use them."""

    def __init__(self, hass) -> None:
        self.hass = hass
        self.entries: list[FakeEntry] = []
        self.writes: list[dict] = []
        self.reloads: list[str] = []

    def async_get_entry(self, entry_id):
        return next((entry for entry in self.entries if entry.entry_id == entry_id), None)

    def async_entries(self, domain=None):
        return list(self.entries)

    def async_update_entry(self, entry, *, options=None):
        """As Home Assistant does it: nothing for no change, else every listener as a task."""
        if options is None or dict(options) == dict(entry.options):
            return False
        entry.options = dict(options)
        self.writes.append(dict(options))
        for listener in list(entry.update_listeners):
            self.hass.track(listener(self.hass, entry))
        return True

    async def async_reload(self, entry_id):
        self.reloads.append(entry_id)


class FakeHass:
    def __init__(self) -> None:
        self.data: dict = {}
        self.config_entries = FakeConfigEntries(self)
        self.tasks: list[asyncio.Task] = []

    def track(self, coro):
        """A task started eagerly, as Home Assistant starts its own and its listeners."""
        task = asyncio.Task(coro, loop=asyncio.get_running_loop(), eager_start=True)
        self.tasks.append(task)
        return task

    def async_create_background_task(self, coro, name, eager_start=True):
        return self.track(coro)

    async def settle(self) -> None:
        """Let every task started so far, and the ones they start, run to their end."""
        for _ in range(10):
            await asyncio.sleep(0)
            pending = [task for task in self.tasks if not task.done()]
            if not pending:
                return
            await asyncio.wait(pending, timeout=2)


class FakeResponse:
    def __init__(self, answer) -> None:
        self.answer = answer
        self.status = None

    async def __aenter__(self):
        if isinstance(self.answer, BaseException):
            raise self.answer
        self.status = self.answer
        return self

    async def __aexit__(self, *exc) -> bool:
        return False

    async def text(self) -> str:
        return '{"error": "log[0].level"}'


class FakeSession:
    """Home Assistant's shared session: every post written down, answered in turn."""

    def __init__(self) -> None:
        self.answers: list = [204]
        self.posts: list[dict] = []

    def post(self, url, *, json=None, **kwargs):
        self.posts.append({"url": url, "json": json, **kwargs})
        answer = self.answers.pop(0) if len(self.answers) > 1 else self.answers[0]
        return FakeResponse(answer)


class FakeStore:
    def __init__(self, stored=None) -> None:
        self.stored = stored
        self.saves: list = []

    async def async_load(self):
        return self.stored

    async def async_save(self, value):
        self.stored = value
        self.saves.append(value)


class Timers:
    """The half-hourly timer, written down rather than started."""

    def __init__(self) -> None:
        self.armed: list[dict] = []

    def __call__(self, hass, action, interval):
        timer = {"action": action, "interval": interval, "live": True}
        self.armed.append(timer)

        def cancel() -> None:
            timer["live"] = False

        return cancel

    def live(self) -> list[dict]:
        return [timer for timer in self.armed if timer["live"]]


@pytest.fixture()
def world(monkeypatch, debug_report, diagnostics_data, const, cop, identity):
    """A Home Assistant of stand-ins with one i255 in it, the debug report off."""
    hass = FakeHass()
    store = FakeStore({"install_id": "6f1c2a7e-0d3b-4c8e-9a51-2b7d4e9f0c13"})
    session = FakeSession()
    timers = Timers()
    pump = runtime(const, cop, load("transitions"), load("alarms"), identity)
    # A figure no JSON can carry, as a sum or a panel could give one.
    pump.modbus.data["hp1_power"] = float("nan")
    entry = FakeEntry(const, runtime_data=pump)
    hass.config_entries.entries.append(entry)

    async def diagnostics(_hass, of):
        found, _text = dump(
            diagnostics_data, of.data, of.options, getattr(of, "runtime_data", None)
        )
        return found

    monkeypatch.setattr(debug_report, "_stats_store", lambda _hass: store)
    monkeypatch.setattr(debug_report, "_session", lambda _hass: session)
    monkeypatch.setattr(debug_report, "_every", timers)
    monkeypatch.setattr(debug_report, "_ha_version", lambda: "2026.10.2")
    monkeypatch.setattr(debug_report, "_json_dumps", lambda: debug_report.encode)
    monkeypatch.setattr(debug_report, "_async_diagnostics", diagnostics)
    monkeypatch.setattr(debug_report, "START_DELAY", timedelta(seconds=0.01))
    monkeypatch.setattr(debug_report, "SWITCHED_ON_DELAY", timedelta(seconds=0.02))
    namespace = logging.getLogger(NAMESPACE)
    level = namespace.level
    yield SimpleNamespace(
        hass=hass, entry=entry, store=store, session=session, timers=timers, const=const,
        module=debug_report, pump=pump,
    )
    namespace.setLevel(level)
    for handler in list(namespace.handlers):
        if isinstance(handler, debug_report.LogRing):
            namespace.removeHandler(handler)


def _warn(text: str, *args, logger: str = "web_api") -> None:
    logging.getLogger(f"{NAMESPACE}.{logger}").warning(text, *args)


def _plant_lines() -> None:
    """Warnings and errors that name the machine every way it can be named."""
    _warn("Cannot connect to host %s:80 ssl:default [Connect call failed ('10.1.2.3', 80)]", HOST)
    _warn("The display at %s did not answer.", HOST, logger="coordinator")
    _warn("Panel ctc0001.local reports serial 7208 2540 0001 and MAC %s: ok", MAC)
    logging.getLogger(f"{NAMESPACE}.modbus_api").error(
        "No answer from %s, the other heat pump; serial %s", OTHER_HOST, SERIAL
    )


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


def _names_nothing(report: dict) -> str:
    """The report as the wire carries it, after proving it names no machine."""
    text = json.dumps(report, ensure_ascii=False)
    assert HOST not in text and "192.0.2." not in text
    assert "10.1.2.3" not in text
    assert MAC not in text and "020000000001" not in text.replace(":", "")
    for spelling in (SERIAL, "7208 2540 0001", "7208-2540-0001"):
        assert spelling not in text, spelling
    assert not [s for s in _strings(report) if SEQUENCE.search(s)], "löpnumret står kvar"
    assert "ctc0001" not in text.lower(), "displayens värdnamn bär löpnumret"
    assert OTHER_HOST not in text, "den andra värmepumpens namn"
    return text


def _armed(world, *, on: bool):
    """Set the entry's option and arm its reporter, as a set-up does, on the running loop."""
    world.entry.options[world.const.CONF_DEBUG_REPORT] = on
    reporter = world.module.async_setup_debug_report(world.hass, world.entry, "0.26.0")
    return reporter


# ------------------------------------------------------------- the setting


def test_the_debug_report_is_off_until_the_owner_switches_it_on(debug_report, const):
    assert const.CONF_DEBUG_REPORT == "debug_report"
    assert debug_report.debug_report_on({}) is False
    assert debug_report.debug_report_on({const.CONF_DEBUG_REPORT: False}) is False
    assert debug_report.debug_report_on({const.CONF_DEBUG_REPORT: True}) is True
    # No time limit: once on, nothing but the option turns it off.
    assert "expires" not in _source("debug_report.py").lower()
    flow = _source("config_flow.py")
    assert "CONF_DEBUG_REPORT: False," in flow.split("FIRST_FORM_DEFAULTS")[1].split("}")[0]
    assert "vol.Optional(CONF_DEBUG_REPORT, default=debug_report_on(options)): bool" in flow


def test_the_option_and_the_switch_are_one_setting(world):
    ha_stub.install()
    switch = load("switch").CtcDebugReportSwitch(world.entry, world.pump)
    assert switch.is_on is False
    world.entry.options[world.const.CONF_DEBUG_REPORT] = True
    assert switch.is_on is True
    assert switch._attr_translation_key == world.const.CONF_DEBUG_REPORT
    assert switch._attr_unique_id == "ctc_ecozenith_192.0.2.55_debug_report"
    assert switch._attr_entity_category.value == "config"
    assert "switch" in world.const.PLATFORMS


def test_the_texts_name_the_option_and_the_switch_in_both_languages():
    def read(name: str) -> dict:
        return json.loads((COMPONENT / name).read_text(encoding="utf-8"))

    swedish = read("translations/sv.json")
    for name in ("strings.json", "translations/en.json"):
        english = read(name)
        assert english["entity"]["switch"]["debug_report"]["name"] == (
            "Report all values to the developer"
        )
        init = english["options"]["step"]["init"]
        assert init["data"]["debug_report"] == "Report all values to the developer"
        said = init["data_description"]["debug_report"]
        for promise in ("30 minutes", "{endpoint}", "IP addresses", "MAC addresses",
                        "serial number", "host names", "14 days", "Off by default",
                        "independent of the anonymous statistics", "Switch it off at any time"):
            assert promise in said, (name, promise)
    assert swedish["entity"]["switch"]["debug_report"]["name"] == (
        "Rapportera alla värden till utvecklaren"
    )
    init = swedish["options"]["step"]["init"]
    assert init["data"]["debug_report"] == "Rapportera alla värden till utvecklaren"
    said = init["data_description"]["debug_report"]
    for promise in ("30:e minut", "{endpoint}", "IP-adresser", "MAC-adresser", "serienumrets",
                    "värdnamn", "14 dagar", "Av som förval", "oberoende av den anonyma statistiken",
                    "Stäng av den när du vill"):
        assert promise in said, promise
    # Commas, colons and full stops, never a dash as punctuation.
    assert "–" not in said and "—" not in said and " - " not in said


# ------------------------------------------------------------ no reload


def test_a_write_of_the_debug_report_alone_takes_no_reload_and_any_other_does(
    debug_report, identity, const
):
    """The rule of the reload listener, _takes_a_reload, as it composes the two."""

    def takes_a_reload(applied: dict, written: dict) -> bool:
        return not identity.only_identity_differs(
            debug_report.without_the_debug_report(applied),
            debug_report.without_the_debug_report(written),
        )

    applied = {const.CONF_FAST_INTERVAL: 30, const.CONF_IDENTITY: {"serial": SERIAL}}
    assert not takes_a_reload(applied, {**applied, const.CONF_DEBUG_REPORT: True})
    assert not takes_a_reload(
        {**applied, const.CONF_DEBUG_REPORT: True}, {**applied, const.CONF_DEBUG_REPORT: False}
    )
    # With the identity filled in at the same time, still none.
    assert not takes_a_reload(
        applied, {**applied, const.CONF_DEBUG_REPORT: True, const.CONF_IDENTITY: {"mac": MAC}}
    )
    # Anything else beside it takes the reload it always took.
    assert takes_a_reload(
        applied, {**applied, const.CONF_DEBUG_REPORT: True, const.CONF_FAST_INTERVAL: 45}
    )
    assert takes_a_reload(applied, {const.CONF_DEBUG_REPORT: True})


def test_configure_writes_the_debug_report_alone_where_nothing_else_changes_in_effect(
    debug_report, const
):
    defaults = {
        const.CONF_FAST_INTERVAL: 30, const.CONF_SLOW_INTERVAL: 1800,
        const.CONF_RESTORE_PAGE: True, const.CONF_ENABLE_CONTROL: True,
        const.CONF_VISIT_SYSTEM_INFO: True, const.CONF_CHECK_UPDATES: True,
        const.CONF_SEND_STATISTICS: True, const.CONF_DEBUG_REPORT: False,
    }
    # An entry as the set-up flow makes it, never saved under Configure.
    stored = {
        const.CONF_SLOW_PAGES: [], const.CONF_SLOW_INTERVAL: 1800,
        const.CONF_FAST_INTERVAL: 30, const.CONF_RESTORE_PAGE: True,
        const.CONF_ENABLE_CONTROL: True, const.CONF_LANGUAGE: 1,
    }
    # The first form writes every field it has, the ones the entry lacked with
    # the values it already had.
    form = {**stored, **{k: v for k, v in defaults.items() if k not in stored}}
    only = debug_report.only_the_debug_report_changes
    assert only(stored, {**form, const.CONF_DEBUG_REPORT: True}, defaults)
    assert only(stored, form, defaults), "inget ändrat alls"
    assert not only(stored, {**form, const.CONF_CHECK_UPDATES: False}, defaults)
    assert not only(stored, {**form, const.CONF_FAST_INTERVAL: 45}, defaults)
    assert not only(stored, {**form, const.CONF_SLOW_PAGES: [{"page": 118}]}, defaults)
    assert not only(stored, {**form, "rescan_menu": True}, defaults), "en nyckel utan förval"
    assert not only({**stored, const.CONF_MENU_ROOT: 118}, form, defaults), "en nyckel försvinner"


def test_the_listener_and_the_form_are_held_to_the_rule():
    init = _source("__init__.py")
    reload = init.split("async def _async_reload")[1].split("\nasync def ")[0]
    assert reload.index("_takes_a_reload(entry, runtime, entry.options)") < reload.index(
        "async_reload(entry.entry_id)"
    )
    flow = _source("config_flow.py")
    save = flow.split("async def _async_save")[1].split("\n    async def ")[0]
    assert "only_the_debug_report_changes(stored, options, FIRST_FORM_DEFAULTS)" in save
    # The debug report alone goes over the stored options, nothing else.
    alone = save.split("only_the_debug_report_changes(")[1].split("if data is not None:")[0]
    assert "options = dict(stored)" in alone
    assert "options[CONF_DEBUG_REPORT] = wanted" in alone
    # Every setting the first form writes has its default in the table, so a
    # field the options lacked never counts as a change.
    tree = ast.parse(flow)
    table = next(
        node.value for node in tree.body
        if isinstance(node, ast.AnnAssign)
        and getattr(node.target, "id", "") == "FIRST_FORM_DEFAULTS"
    )
    defaults = {key.id for key in table.keys}
    pending = next(
        node.value for node in ast.walk(tree)
        if isinstance(node, ast.Assign)
        and any(ast.unparse(target) == "self._pending" for target in node.targets)
    )
    written = {key.id for key in pending.keys if key is not None}
    assert written == defaults, written ^ defaults


def test_the_switch_writes_the_option_alone_and_reloads_nothing(world):
    ha_stub.install()
    switch = load("switch").CtcDebugReportSwitch(world.entry, world.pump)
    switch.hass = world.hass
    shown: list[bool] = []
    switch.async_write_ha_state = lambda: shown.append(switch.is_on)
    removers: list = []
    switch.async_on_remove = removers.append
    before = dict(world.entry.options)
    key = world.const.CONF_DEBUG_REPORT

    async def scenario():
        await switch.async_added_to_hass()
        await switch.async_turn_on()
        await world.hass.settle()
        await switch.async_turn_on()  # already on: nothing is written again
        # Configure writes it too, and the switch shows what it says.
        world.hass.config_entries.async_update_entry(
            world.entry, options={**world.entry.options, key: False}
        )
        await world.hass.settle()

    run(scenario())
    writes = world.hass.config_entries.writes
    assert writes[0] == {**before, key: True}, "bara alternativet skrivs"
    assert len(writes) == 2
    assert world.hass.config_entries.reloads == [], "ingen omladdning"
    assert shown[0] is True and shown[-1] is False
    assert len(removers) == 1, "lyssnaren tas bort med entiteten"
    assert world.entry.update_listeners, "lyssnaren är registrerad"


# ---------------------------------------------------------------- the log


def test_the_ring_keeps_the_warnings_and_errors_of_the_integrations_own_loggers(debug_report):
    ring = debug_report.LogRing()
    namespace = logging.getLogger(NAMESPACE)
    level = namespace.level
    namespace.setLevel(logging.DEBUG)
    namespace.addHandler(ring)
    try:
        logging.getLogger(f"{NAMESPACE}.coordinator").info("an info line")
        logging.getLogger(f"{NAMESPACE}.coordinator").debug("a debug line")
        logging.getLogger("pymodbus.client").warning("someone else's warning")
        logging.getLogger("homeassistant.config_entries").error("Home Assistant's error")
        _warn("a warning")
        logging.getLogger(f"{NAMESPACE}.web_api").error("an error")
        logging.getLogger(NAMESPACE).critical("critical")
        logging.getLogger(f"{NAMESPACE}.web_api").log(35, "a level of its own")
    finally:
        namespace.removeHandler(ring)
        namespace.setLevel(level)
    lines = ring.lines()
    assert [line.message for line in lines] == [
        "a warning", "an error", "critical", "a level of its own",
    ]
    assert [line.level for line in lines] == ["WARNING", "ERROR", "CRITICAL", "WARNING"]
    assert [line.seq for line in lines] == [1, 2, 3, 4]
    assert lines[0].logger == f"{NAMESPACE}.web_api"
    assert lines[0].at.utcoffset() == timedelta(0)


def test_levels_are_the_three_the_server_takes(debug_report):
    assert debug_report.level_name(logging.WARNING) == "WARNING"
    assert debug_report.level_name(35) == "WARNING"
    assert debug_report.level_name(logging.ERROR) == "ERROR"
    assert debug_report.level_name(45) == "ERROR"
    assert debug_report.level_name(logging.CRITICAL) == "CRITICAL"
    assert debug_report.level_name(60) == "CRITICAL"


def test_a_traceback_is_not_kept_only_the_errors_type_and_message(debug_report):
    ring = debug_report.LogRing()
    logger = logging.getLogger(f"{NAMESPACE}.web_api")
    logger.addHandler(ring)
    try:
        try:
            raise ValueError(f"bad answer from {HOST}")
        except ValueError:
            logger.warning("Could not read the page", exc_info=True)
    finally:
        logger.removeHandler(ring)
    (line,) = ring.lines()
    assert line.message == f"Could not read the page\nValueError: bad answer from {HOST}"
    assert "Traceback" not in line.message and ".py" not in line.message


def test_the_ring_keeps_the_newest_200_and_lets_go_of_what_was_stored(debug_report):
    ring = debug_report.LogRing()
    logger = logging.getLogger(f"{NAMESPACE}.harvest")
    logger.addHandler(ring)
    try:
        for number in range(250):
            logger.warning("line %d", number)
    finally:
        logger.removeHandler(ring)
    lines = ring.lines()
    assert len(lines) == debug_report.MAX_LINES == 200
    assert lines[0].message == "line 50" and lines[-1].message == "line 249"
    ring.drop_through(lines[99].seq)
    assert [line.message for line in ring.lines()][:1] == ["line 150"]
    assert len(ring.lines()) == 100


def test_a_long_message_is_cut_but_never_inside_a_name(debug_report):
    cut = debug_report._cut
    assert cut("short", 2000) == "short"
    long = "x " * 1500
    assert len(cut(long, 2000)) <= 2000 and cut(long, 2000).endswith("...")
    # A host name across the limit goes whole, or the wash would miss it.
    text = "a" * 1990 + " varmepump.lan and more"
    assert cut(text, 2000) == "a" * 1990 + " ..."
    assert "varmepump" not in cut(text, 2000)


def test_the_log_is_washed_of_every_heat_pumps_names(world, debug_report, const):
    other = FakeEntry(const, entry_id="other", host=OTHER_HOST, opts={})
    world.hass.config_entries.entries.append(other)
    async def scenario():
        reporter = _armed(world, on=True)
        try:
            _plant_lines()
            return debug_report.log_lines(reporter._ring.lines(), reporter._washer())
        finally:
            reporter.close()

    lines = run(scenario())
    report = {"log": lines}
    _names_nothing(report)
    assert lines[0]["message"] == (
        "Cannot connect to host **REDACTED**:80 ssl:default [Connect call failed "
        "('**REDACTED**', 80)]"
    )
    assert lines[1]["message"] == "The display at **REDACTED** did not answer."
    assert lines[2]["message"] == (
        "Panel CTCXXXX.local reports serial 7208 2540 XXXX and MAC **REDACTED**: ok"
    )
    assert lines[3]["message"] == (
        "No answer from **REDACTED**, the other heat pump; serial 7208 2540 XXXX"
    )
    assert [line["level"] for line in lines] == ["WARNING", "WARNING", "WARNING", "ERROR"]
    assert set(lines[0]) == {"at", "level", "logger", "message"}
    assert re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\+00:00", lines[0]["at"])


def test_the_log_is_capped_at_200_lines_of_2000_characters(debug_report, diagnostics_data):
    ring = debug_report.LogRing()
    logger = logging.getLogger(f"{NAMESPACE}.harvest")
    logger.addHandler(ring)
    try:
        for number in range(205):
            logger.error("%d %s", number, "word " * 900)
    finally:
        logger.removeHandler(ring)
    lines = debug_report.log_lines(ring.lines(), debug_report.Washer([]))
    assert len(lines) == 200
    assert lines[0]["message"].startswith("5 ")
    assert all(len(line["message"]) <= 2000 for line in lines)
    # Without any installation's secrets the wash still takes what names any.
    (washed,) = debug_report.log_lines(
        [debug_report.LogLine(1, ring.lines()[0].at, "WARNING", "x", f"at {HOST}.")],
        debug_report.Washer([]),
    )
    assert washed["message"] == "at **REDACTED**."


# ------------------------------------------------------------- the report


def test_the_report_is_the_download_and_the_washed_log_and_names_no_machine(
    world, diagnostics_data, const
):
    # A second heat pump of the integration, whose name a line can carry.
    world.hass.config_entries.entries.append(
        FakeEntry(const, entry_id="other", host=OTHER_HOST, opts={})
    )
    reporter = _armed(world, on=False)
    world.entry.options[world.const.CONF_DEBUG_REPORT] = True

    async def scenario():
        reporter.async_apply(at_once=True)
        await world.hass.settle()
        # The first report went before the lines were logged; the next carries them.
        _plant_lines()
        await world.timers.live()[0]["action"]()

    try:
        run(scenario())
    finally:
        reporter.close()
    first, second = world.session.posts
    assert second["url"] == "https://stats.rnet.se/api/v1/diagnostics"
    assert set(second) == {"url", "json"}, "kroppen skrivs av Home Assistants session"
    report = second["json"]
    assert list(report) == [
        "schema", "install_id", "integration", "version", "ha_version", "diagnostics", "log",
    ]
    assert report["schema"] == 1
    assert report["install_id"] == "6f1c2a7e-0d3b-4c8e-9a51-2b7d4e9f0c13"
    assert report["integration"] == "ctc_ecozenith"
    assert report["version"] == "0.26.0"
    assert report["ha_version"] == "2026.10.2"
    # Exactly what Download diagnostics gives, the NaN as null.
    expected, _text = dump(diagnostics_data, world.entry.data, world.entry.options, world.pump)
    expected["modbus"]["values"]["hp1_power"] = None
    assert report["diagnostics"] == expected
    assert len(report["log"]) == 4 and first["json"]["log"] == []
    text = _names_nothing(report)
    # The size as the wire carries it.
    size = len(world.module.encode(report).encode("utf-8"))
    assert 2_000 < size < 100_000, size
    assert "NaN" not in text


def test_a_nan_in_the_download_is_null_on_the_wire(debug_report):
    report = debug_report.build_payload(
        install_id="x", version="0.26.0", ha_version="2026.10.2",
        diagnostics={"modbus": {"values": {"a": float("nan"), "b": [float("inf"), 1.5]}}},
        log=[],
    )
    assert report["diagnostics"] == {"modbus": {"values": {"a": None, "b": [None, 1.5]}}}
    # Whatever writes the body, the stdlib's json included, which writes NaN bare.
    assert "NaN" not in json.dumps(report) and "Infinity" not in json.dumps(report)
    assert '"a":null' in debug_report.encode(report)


def test_a_report_over_a_megabyte_lets_its_oldest_lines_go(debug_report):
    line = {"at": "2026-10-19T08:30:00+00:00", "level": "ERROR", "logger": "x",
            "message": "y" * 1990}
    log = [{**line, "message": f"{n:04d}" + "y" * 1990} for n in range(200)]
    report = debug_report.build_payload(
        install_id="x", version="0.26.0", ha_version="2026.10.2",
        diagnostics={"blob": "z" * 700_000}, log=log,
    )
    fitted, size, kept = debug_report.fitted(report)
    assert size <= debug_report.MAX_BYTES
    assert size == len(debug_report.encode(fitted).encode("utf-8"))
    assert 0 < kept < 200
    assert fitted["log"] == log[200 - kept :], "de äldsta raderna får ge plats"
    # A download that alone is over the limit is not sent at all.
    alone = {**report, "diagnostics": {"blob": "z" * 1_000_001}}
    assert debug_report.fitted(alone) is None
    # And a report well within it is sent whole.
    small = {**report, "diagnostics": {}, "log": log[:3]}
    assert debug_report.fitted(small)[2] == 3


# ------------------------------------------------------------ the install id


def test_the_statistics_install_id_is_reused(debug_report):
    store = FakeStore({"install_id": "6f1c2a7e-0d3b-4c8e-9a51-2b7d4e9f0c13"})
    found = run(debug_report.async_install_id(store))
    assert found == "6f1c2a7e-0d3b-4c8e-9a51-2b7d4e9f0c13"
    assert store.saves == [], "statistikens id skrivs inte över"


@pytest.mark.parametrize("stored", [None, {}, {"install_id": ""}])
def test_a_missing_install_id_is_made_as_the_statistics_make_it(debug_report, stored):
    store = FakeStore(stored)
    made = run(debug_report.async_install_id(store))
    assert uuid.UUID(made).version == 4 and str(uuid.UUID(made)) == made
    assert store.saves == [{"install_id": made}], "sparat som stats.py sparar det"
    # And the next reading, the statistics' own, finds the same one.
    assert run(debug_report.async_install_id(store)) == made


def test_the_store_is_the_statistics_own_and_stats_py_is_left_alone(debug_report):
    stats = (COMPONENT / "stats.py").read_text(encoding="utf-8")
    assert 'Store(hass, 1, f"{domain}.stats")' in stats
    assert 'await self._store.async_save({"install_id": install_id})' in stats
    assert debug_report.STATS_STORE_VERSION == 1
    assert debug_report.STATS_STORE_KEY == "ctc_ecozenith.stats"
    assert "Store(hass, STATS_STORE_VERSION, STATS_STORE_KEY)" in _source("debug_report.py")
    # Nothing of the report lives in the shared file.
    assert "api/v1/diagnostics" not in stats and "debug_report" not in stats


# ------------------------------------------------------------- the reporter


def test_nothing_is_sent_or_kept_while_it_is_off(world):
    reporter = _armed(world, on=False)

    async def scenario():
        _plant_lines()
        await world.hass.settle()
        await reporter.async_report()

    try:
        run(scenario())
        assert world.session.posts == []
        assert world.timers.armed == []
        assert reporter._ring is None
        assert not [
            h for h in logging.getLogger(NAMESPACE).handlers
            if isinstance(h, world.module.LogRing)
        ], "ingen logg samlas"
        assert world.store.saves == [] and world.store.stored is not None
    finally:
        reporter.close()


def test_switching_it_on_sends_a_report_at_once_and_every_half_hour_after(world):
    reporter = _armed(world, on=False)
    key = world.const.CONF_DEBUG_REPORT

    async def scenario():
        world.hass.config_entries.async_update_entry(
            world.entry, options={**world.entry.options, key: True}
        )
        await world.hass.settle()
        assert len(world.session.posts) == 1, "en rapport direkt"
        (timer,) = world.timers.live()
        assert timer["interval"] == timedelta(minutes=30)
        await timer["action"]()
        await timer["action"]()

    try:
        run(scenario())
    finally:
        reporter.close()
    assert len(world.session.posts) == 3
    assert world.hass.config_entries.reloads == []
    assert timedelta(minutes=30) == world.module.INTERVAL


def test_at_start_the_first_report_waits_for_the_set_up(world, monkeypatch):
    assert timedelta(seconds=0.01) == world.module.START_DELAY  # the fixture's
    assert load("debug_report").START_DELAY is world.module.START_DELAY
    slept: list[float] = []
    real_sleep = asyncio.sleep

    async def sleep(seconds):
        slept.append(seconds)
        await real_sleep(0)

    monkeypatch.setattr(world.module.asyncio, "sleep", sleep)

    async def scenario():
        reporter = _armed(world, on=True)
        await world.hass.settle()
        return reporter

    reporter = run(scenario())
    reporter.close()
    assert [seconds for seconds in slept if seconds] == [pytest.approx(0.01)]
    assert len(world.session.posts) == 1
    source = _source("debug_report.py")
    assert "START_DELAY = timedelta(minutes=2)" in source
    assert "SWITCHED_ON_DELAY = timedelta(seconds=5)" in source
    assert "INTERVAL = timedelta(minutes=30)" in source


def test_switched_on_the_first_report_waits_only_for_a_reload_to_cancel_it(world, monkeypatch):
    slept: list[float] = []
    real_sleep = asyncio.sleep

    async def sleep(seconds):
        slept.append(seconds)
        await real_sleep(0)

    monkeypatch.setattr(world.module.asyncio, "sleep", sleep)
    key = world.const.CONF_DEBUG_REPORT

    async def scenario():
        reporter = _armed(world, on=False)
        world.hass.config_entries.async_update_entry(
            world.entry, options={**world.entry.options, key: True}
        )
        # A reload stops the reporter before its report has gone.
        world.module.async_stop_debug_report(world.hass, world.entry)
        await world.hass.settle()
        return reporter

    run(scenario())
    assert [seconds for seconds in slept if seconds] == [pytest.approx(0.02)]
    assert world.session.posts == [], "omladdningen tog den, uppsättningen rapporterar själv"


def test_a_stored_report_lets_its_lines_go_and_keeps_those_logged_since(world):
    world.session.answers = [204]
    reporter = None

    async def scenario():
        nonlocal reporter
        reporter = _armed(world, on=True)
        await world.hass.settle()
        _plant_lines()
        assert len(reporter._ring.lines()) == 4
        await reporter.async_report()
        assert reporter._ring.lines() == [], "servern har dem"
        _warn("after the report")
        world.session.answers = [503]
        await reporter.async_report()
        world.session.answers = [204]
        await reporter.async_report()

    try:
        run(scenario())
    finally:
        reporter.close()
    logs = [post["json"]["log"] for post in world.session.posts]
    assert [len(log) for log in logs] == [0, 4, 1, 1]
    assert logs[3][0]["message"] == "after the report"


@pytest.mark.parametrize(
    "answer",
    [429, 400, 413, 503, 500, OSError("network is unreachable"), TimeoutError(),
     ValueError("anything at all")],
)
def test_too_soon_and_every_failure_never_raise_and_keep_the_lines(world, answer, caplog):
    world.session.answers = [answer]
    reporter = None

    async def scenario():
        nonlocal reporter
        reporter = _armed(world, on=True)
        await world.hass.settle()
        _plant_lines()
        await reporter.async_report()

    try:
        with caplog.at_level(logging.DEBUG):
            run(scenario())
        assert len(reporter._ring.lines()) == 4, "raderna väntar på nästa tur"
    finally:
        reporter.close()
    assert world.entry.update_listeners == []
    ours = [r for r in caplog.records if r.name.endswith(".debug_report")]
    assert ours and all(r.levelno <= logging.INFO for r in ours), "aldrig över info"


def test_a_failing_download_or_store_never_raises(world, monkeypatch):
    async def broken(_hass, _entry):
        raise RuntimeError("half set up")

    monkeypatch.setattr(world.module, "_async_diagnostics", broken)

    async def scenario():
        reporter = _armed(world, on=True)
        try:
            await world.hass.settle()
            await reporter.async_report()
        finally:
            reporter.close()

    run(scenario())
    assert world.session.posts == []


def test_switching_it_off_stops_the_timer_the_ring_and_the_reports(world):
    key = world.const.CONF_DEBUG_REPORT
    reporter = None

    async def scenario():
        nonlocal reporter
        reporter = _armed(world, on=True)
        await world.hass.settle()
        _plant_lines()
        world.hass.config_entries.async_update_entry(
            world.entry, options={**world.entry.options, key: False}
        )
        await world.hass.settle()
        await reporter.async_report()
        _warn("while off")

    try:
        run(scenario())
        assert world.timers.live() == []
        assert reporter._ring is None and reporter._carried == []
        assert len(world.session.posts) == 1, "bara den vid starten"
        assert not [
            h for h in logging.getLogger(NAMESPACE).handlers
            if isinstance(h, world.module.LogRing)
        ]
    finally:
        reporter.close()
    assert world.hass.config_entries.reloads == []


def test_a_set_up_attempt_again_keeps_the_running_reporter(world):
    async def scenario():
        first = _armed(world, on=True)
        await world.hass.settle()
        again = _armed(world, on=True)
        await world.hass.settle()
        return first, again

    first, again = run(scenario())
    first.close()
    assert first is again
    assert len(world.session.posts) == 1, "ingen extra rapport per försök"
    assert len(world.timers.armed) == 1
    assert len(world.entry.update_listeners) == 0


def test_a_reload_hands_the_lines_on_and_a_removal_lets_them_go(world):
    module = world.module

    async def scenario():
        world.session.answers = [429]
        first = _armed(world, on=True)
        await world.hass.settle()
        _plant_lines()
        module.async_stop_debug_report(world.hass, world.entry)
        assert world.timers.live() == [] and world.entry.update_listeners == []
        second = _armed(world, on=True)
        assert second is not first
        await world.hass.settle()
        assert len(second._ring.lines()) == 4, "omladdningen tappar inga rader"
        module.async_stop_debug_report(world.hass, world.entry, keep_lines=False)
        assert world.hass.data[module.LINES_KEY] == {}
        third = _armed(world, on=True)
        await world.hass.settle()
        assert third._ring.lines() == []
        module.async_stop_debug_report(world.hass, world.entry, keep_lines=False)

    run(scenario())
    assert len(world.session.posts) == 3
    assert world.session.posts[1]["json"]["log"] != []


@pytest.mark.parametrize("gone", ["removed", "disabled"])
def test_an_entry_gone_or_disabled_stops_its_reporter(world, gone):
    async def scenario():
        _armed(world, on=True)
        await world.hass.settle()
        if gone == "removed":
            world.hass.config_entries.entries.clear()
        else:
            world.entry.disabled_by = "user"
        await world.timers.live()[0]["action"]()

    run(scenario())
    assert len(world.session.posts) == 1
    assert world.timers.live() == []
    assert world.entry.entry_id not in world.hass.data[world.module.DATA_KEY]
    assert world.entry.update_listeners == []


# ----------------------------------------------------------- the call sites


def test_the_report_is_armed_before_the_first_modbus_call_and_stopped_where_stats_is():
    init = _source("__init__.py")
    setup = init.split("async def async_setup_entry")[1].split("\nasync def ")[0]
    armed = setup.index("await _async_arm_debug_report(hass, entry)")
    assert setup.index("await _async_arm_statistics(hass, entry)") < armed
    assert armed < setup.index("async_config_entry_first_refresh()")
    assert "entry.async_on_unload(async_stop_debug_report" not in init
    unload = init.split("async def async_unload_entry")[1].split("\nasync def ")[0]
    assert "async_stop_debug_report(hass, entry)" in unload
    remove = init.split("async def async_remove_entry")[1]
    assert "async_stop_debug_report(hass, entry, keep_lines=False)" in remove


def test_the_sender_posts_with_home_assistants_shared_session_and_is_free_of_it_otherwise():
    source = _source("debug_report.py")
    assert "async_get_clientsession(hass)" in source
    assert ".post(ENDPOINT, json=report)" in source
    assert "TIMEOUT = 30" in source and "asyncio.timeout(TIMEOUT)" in source
    top = source.split("# " + "-" * 64 + " the rules")[0]
    assert "homeassistant" not in top.split('"""')[-1], "modulens topp är fri från Home Assistant"
