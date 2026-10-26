#!/usr/bin/env python3
"""Load the integration under a real Home Assistant: its modules, its names and its forms.

The ordinary test suite runs against stand-ins for Home Assistant
(tests/ha_stub.py), and the Home Assistant tests run against the one release
pytest-homeassistant-custom-component pins. A Home Assistant name that has
moved, been renamed or not yet come (imported or reached for anywhere in the
source, inside a function too), a selector config the real selector refuses, or
a Lovelace class whose constructor has changed is invisible to the first and
seen by the second only for that release; on a house it shows as an entry that
does not load, or as a feature that stops without a word. CI runs this against
the release the houses run, against the floor hacs.json promises, and every
week against the newest release (roadmap R58).

Nothing here touches a network or a heat pump. In order:

- every module of the package is imported (pkgutil.walk_packages);
- every Home Assistant import in the package is run as it is written, at every
  depth, inside functions and try blocks too; every attribute reached from a
  name such an import binds (frontend.async_panel_exists, er.async_get) is
  looked up, every call of a function or class reached that way is held to its
  real signature, and so is the call seen_history.py hands the recorder;
- the config flow's forms that need no network are built: the first menu, the
  address forms, the busy and confirm steps, and the search and page steps with
  the network sweep and the menu reading replaced by one found unit and a menu
  of two pages;
- the options form is built with an empty and with a filled menu, and so is the
  form after reading the menu again, and the search for one page's two forms,
  the one that asks for the page and the one that shows what was found; the
  page schema is built on its own, empty and filled, through the real
  selectors;
- every form goes through the code that hands it to the frontend, and
  strings.json must name each step, each menu option, each field and each
  error the form shows;
- the CTC page's Lovelace class is built, made once and asked for its layout;
- services.yaml is read and validated with Home Assistant's own schema, and
  must list exactly the actions services.py registers.

An error logged by the integration itself during any of it fails the run.

What an object has is not seen: a method on hass.config_entries, a ConfigEntry
or a selector made from its class, and the arguments it takes, are reached
through a value, not an import. Code that starts using one newer than the floor
in hacs.json needs its line in NEEDS in tests/test_ha_floor.py, by hand.

With --strict, the weekly run against the newest release, a DeprecationWarning
attributed to one of the integration's own modules is an error, and so is Home
Assistant's own notice that the integration uses something on its way out.
Home Assistant gives those notices in its log (helpers.frame and
helpers.deprecation), not as DeprecationWarning, so the run reads its log as
well. Each import and lookup of the names step is made as from the file and
line it is written on, so the notice names the integration for a name reached
inside a function too. Without --strict such a notice is printed and the run
still passes: the release the houses run is not to be failed over a removal a
year ahead.

Usage:
    pip install homeassistant==<version>
    python scripts/smoke_real_ha.py [--strict]
"""

from __future__ import annotations

import argparse
import ast
import asyncio
import contextlib
import enum
import importlib
import inspect
import ipaddress
import json
import logging
import os
from pathlib import Path
import pkgutil
import re
import sys
import tempfile
import traceback
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch
import warnings

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

DOMAIN = "ctc_ecozenith"
PACKAGE = f"custom_components.{DOMAIN}"
COMPONENT = ROOT / "custom_components" / DOMAIN
STRINGS = json.loads((COMPONENT / "strings.json").read_text(encoding="utf-8"))

#: A documentation address (RFC 5737), never a house's.
HOST = "192.0.2.55"
#: The operation data root the made-up menu below was read from.
MENU_ROOT = 10

#: Words Home Assistant's own notices about something on its way out use.
_DEPRECATION_WORDS = ("deprecat", "stop working", "will be removed")


class Report:
    """What passed and what failed, printed as it goes."""

    def __init__(self) -> None:
        self.failures: list[str] = []
        self.count = 0

    @contextlib.contextmanager
    def step(self, kind: str, what: str):
        self.count += 1
        try:
            yield
        except Exception:
            self.failures.append(f"{kind} {what}")
            print(f"{kind:8} FAIL  {what}")
            traceback.print_exc(limit=6, file=sys.stdout)
        else:
            print(f"{kind:8} ok    {what}")


class Captured(logging.Handler):
    """Every record of warning level and above, kept for the end of the run."""

    def __init__(self) -> None:
        super().__init__(logging.WARNING)
        self.records: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)


def _module(name: str) -> Any:
    return importlib.import_module(f"{PACKAGE}.{name}")


def _pages() -> list[Any]:
    """A menu of two pages, made up, the way a reading of the display returns it."""
    const = _module("const")
    return [
        const.SlowPage(
            page=22,
            title="Värmepump",
            screens=[118],
            values=[
                const.SlowValue(key="p22_s118_v37", label="Kompressor", page=22, screen=118,
                                fmt="%d", var_indices=[37], unit="rps"),
                const.SlowValue(key="p22_s118_v12", label="Status", page=22, screen=118,
                                fmt="%d", var_indices=[12]),
            ],
            route=[(100, 120)],
        ),
        const.SlowPage(
            page=30,
            title="Lagrad driftdata",
            screens=[130],
            values=[
                const.SlowValue(key="p30_s130_v5", label="Avgiven värme totalt", page=30,
                                screen=130, fmt="%d", var_indices=[5], unit="kWh"),
            ],
            route=[(100, 200)],
        ),
    ]


class _NoNetwork:
    """The client session the flows are handed: anything asked of it fails the step.

    Home Assistant's own session needs the network integration set up, and the
    steps that would use it have the sweep and the menu reading replaced.
    """

    def __getattr__(self, name: str) -> Any:
        raise AssertionError(f"the smoke went on the network: session.{name}")


def _no_session(_hass: Any) -> _NoNetwork:
    return _NoNetwork()


def _reading() -> Any:
    return _module("catalogue").MenuReading(pages=_pages(), complete=True, root=MENU_ROOT)


def _frontend_view() -> Any:
    """The code that turns a flow's step into what the frontend is sent.

    Its serialiser moved from voluptuous_serialize to probatio between 2025.2
    and 2026.9, so the smoke goes through the view rather than naming either.
    """
    from homeassistant.helpers.data_entry_flow import _BaseFlowManagerView

    return _BaseFlowManagerView(None)


def _check_step(view: Any, where: str, result: Any) -> list[str]:
    """Send one step through the frontend's serialiser and check its texts.

    Returns the names of the form's fields.
    """
    from homeassistant.helpers.json import json_dumps

    data = view._prepare_result_json(result)
    json_dumps(data)
    step = result["step_id"]
    texts = STRINGS[where]["step"].get(step)
    assert texts is not None, f"strings.json has no {where}.step.{step}"
    if result["type"] == "menu":
        options = list(result["menu_options"])
        missing = [option for option in options if option not in texts.get("menu_options", {})]
        assert not missing, f"strings.json has no menu option {missing} for {where}.step.{step}"
        return []
    assert result["type"] == "form", result["type"]
    fields = [field["name"] for field in data.get("data_schema") or []]
    missing = [field for field in fields if field not in texts.get("data", {})]
    assert not missing, f"strings.json has no label for {missing} in {where}.step.{step}"
    errors = list((result.get("errors") or {}).values())
    missing = [error for error in errors if error not in STRINGS[where].get("error", {})]
    assert not missing, f"strings.json has no {where}.error for {missing}"
    return fields


def _import_everything(report: Report) -> set[str]:
    """Import the package and every module in it; returns the short names that imported."""
    package = None
    with report.step("import", PACKAGE):
        package = importlib.import_module(PACKAGE)
    if package is None:
        return set()
    imported: set[str] = set()
    names = sorted(
        info.name for info in pkgutil.walk_packages(package.__path__, f"{PACKAGE}.")
    )
    for name in names:
        with report.step("import", name):
            importlib.import_module(name)
            imported.add(name.rsplit(".", 1)[1])
    return imported


#: The syntax that opens a scope of its own, and the part of it that takes arguments.
_FUNCTIONS = (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)
_SCOPES = (*_FUNCTIONS, ast.ClassDef, ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)

#: One name an import binds: the statement and the alias in it.
_Binding = tuple[ast.Import | ast.ImportFrom, ast.alias]


def _from_home_assistant(statement: ast.Import | ast.ImportFrom, alias: ast.alias) -> bool:
    if isinstance(statement, ast.ImportFrom):
        if statement.level:
            return False
        module = statement.module or ""
    else:
        module = alias.name
    return module == "homeassistant" or module.startswith("homeassistant.")


def _bound_name(alias: ast.alias) -> str:
    return alias.asname or alias.name.partition(".")[0]


def _same(binding: _Binding) -> tuple[str, ...]:
    """What a binding refers to, so that two imports of one thing count as one."""
    statement, alias = binding
    if isinstance(statement, ast.ImportFrom):
        return (statement.module or "", alias.name)
    return (alias.name if alias.asname else _bound_name(alias),)


class _Scope:
    """What one scope of a module binds: names from Home Assistant imports, and the rest."""

    def __init__(self, parent: _Scope | None, is_class: bool = False) -> None:
        self.parent = parent
        self.is_class = is_class
        self.imported: dict[str, list[_Binding]] = {}
        self.other: set[str] = set()

    def bind(self, name: str, binding: _Binding | None = None) -> None:
        if binding is None:
            self.other.add(name)
        else:
            self.imported.setdefault(name, []).append(binding)

    def lookup(self, name: str) -> _Binding | None:
        """The Home Assistant import a name read here is bound by, if it is one.

        A name the scope binds in any other way (an argument, an assignment, a
        loop) is a local and is never followed, nor is one bound to two
        different things.
        """
        scope, here = self, True
        while scope is not None:
            # A class body's own names are not seen from the functions in it.
            if here or not scope.is_class:
                if name in scope.other:
                    return None
                if name in scope.imported:
                    found = scope.imported[name]
                    return found[0] if len({_same(b) for b in found}) == 1 else None
            scope, here = scope.parent, False
        return None


def _scan(
    tree: ast.Module,
) -> tuple[list[_Binding], list[tuple[ast.Attribute, _Scope]], list[tuple[ast.Call, _Scope]]]:
    """Every Home Assistant import in a module at every depth, inside functions
    and try blocks too, and every attribute chain and call, with the scope each
    is read in."""
    imports: list[_Binding] = []
    chains: list[tuple[ast.Attribute, _Scope]] = []
    calls: list[tuple[ast.Call, _Scope]] = []
    module = _Scope(None)

    def walk(node: ast.AST, scope: _Scope) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
                scope.bind(child.name)
            if isinstance(child, _SCOPES):
                inner = _Scope(scope, is_class=isinstance(child, ast.ClassDef))
                if isinstance(child, _FUNCTIONS):
                    for arg in ast.walk(child.args):
                        if isinstance(arg, ast.arg):
                            inner.bind(arg.arg)
                walk(child, inner)
                continue
            if isinstance(child, ast.Import | ast.ImportFrom):
                for alias in child.names:
                    if alias.name == "*":
                        continue
                    if _from_home_assistant(child, alias):
                        imports.append((child, alias))
                        scope.bind(_bound_name(alias), (child, alias))
                    else:
                        scope.bind(_bound_name(alias))
            elif isinstance(child, ast.Name) and not isinstance(child.ctx, ast.Load):
                scope.bind(child.id)
            elif isinstance(child, ast.Global | ast.Nonlocal):
                for name in child.names:
                    scope.bind(name)
                    module.bind(name)
            elif isinstance(child, ast.ExceptHandler | ast.MatchAs | ast.MatchStar):
                if child.name:
                    scope.bind(child.name)
            elif isinstance(child, ast.MatchMapping) and child.rest:
                scope.bind(child.rest)
            elif isinstance(child, ast.Attribute) and not (
                isinstance(node, ast.Attribute) and node.value is child
            ):
                # An attribute assigned to is not looked up; what it is set on is.
                if isinstance(child.ctx, ast.Load):
                    chains.append((child, scope))
                elif isinstance(child.value, ast.Attribute):
                    chains.append((child.value, scope))
            if isinstance(child, ast.Call):
                calls.append((child, scope))
            walk(child, scope)

    walk(tree, module)
    return imports, chains, calls


def _module_name(path: Path) -> str:
    parts = path.relative_to(COMPONENT).with_suffix("").parts
    return ".".join((PACKAGE, *(parts[:-1] if parts[-1] == "__init__" else parts)))


def _as_written(path: Path, node: ast.stmt | ast.expr, names: dict[str, Any] | None = None) -> Any:
    """Run one import, or evaluate one expression, as from the file and line it is on.

    Home Assistant names the integration in its notice that something is on its
    way out by the file of the code that reached for it, and Python files a
    DeprecationWarning under the module that code runs in. Done from this
    script, the notice would name nobody and --strict would not see it.
    """
    namespace = {"__name__": _module_name(path), **(names or {})}
    if isinstance(node, ast.expr):
        return eval(compile(ast.Expression(node), str(path), "eval"), namespace)
    exec(compile(ast.Module([node], type_ignores=[]), str(path), "exec"), namespace)
    return namespace


def _qualified(owner: Any) -> str:
    if inspect.ismodule(owner):
        return owner.__name__
    return f"{owner.__module__}.{owner.__qualname__}"


def _attribute(path: Path, node: ast.Attribute, names: dict[str, Any], owner: Any) -> Any:
    """One step of a chain; a module's submodule counts even when nothing imported it yet."""
    try:
        return _as_written(path, node, names)
    except AttributeError:
        if inspect.ismodule(owner):
            submodule = f"{owner.__name__}.{node.attr}"
            try:
                return importlib.import_module(submodule)
            except ModuleNotFoundError as err:
                if err.name != submodule:
                    raise
        raise


def _follow(
    path: Path, root: str, start: Any, steps: list[ast.Attribute], where: str,
    missed: dict[str, BaseException],
) -> tuple[Any, Any] | None:
    """Walk a chain from a name an import binds, while each step is a module or a class.

    Returns what the whole chain reaches and what it was reached on, or None
    when a step is missing (written down in missed) or the chain goes on into
    something else, an object whose attributes are its own.
    """
    names = {root: start}
    target, owner = start, None
    for step in steps:
        if not (inspect.ismodule(target) or inspect.isclass(target)):
            return None
        owner = target
        try:
            target = _attribute(path, step, names, owner)
        except Exception as err:
            missed.setdefault(f"{where}:{step.lineno} {_qualified(owner)}.{step.attr}", err)
            return None
    return target, owner


def _fits(target: Any, owner: Any, call: ast.Call) -> bool:
    """Whether the call was held to the target's real signature; TypeError if it does not fit.

    Only the number of positional arguments and the keywords' names are held to
    it, and only for a function or class reached from an import: a method
    reached through its class takes self first, and a TypedDict or an enum is
    not called for an instance in the way a signature describes.
    """
    if any(isinstance(arg, ast.Starred) for arg in call.args) or any(
        keyword.arg is None for keyword in call.keywords
    ):
        return False
    if inspect.isclass(owner):
        return False
    if inspect.isclass(target):
        if issubclass(target, dict | enum.Enum) or hasattr(target, "__total__"):
            return False
    elif not (inspect.isfunction(target) or inspect.isbuiltin(target)):
        return False
    try:
        signature = inspect.signature(target)
    except (TypeError, ValueError):
        return False
    try:
        signature.bind(*([None] * len(call.args)), **dict.fromkeys(k.arg for k in call.keywords))
    except TypeError as err:
        raise TypeError(f"{err}; the signature is {signature}") from None
    return True


def _names(report: Report) -> dict[tuple[str, str], Any]:
    """Resolve every Home Assistant name the source spells, under the installed release.

    Importing a module runs only its top level. An import inside a function
    runs first when the function does, and an attribute of a Home Assistant
    module is looked up first when the line runs, at set-up or later; a
    feature that catches its own errors (the history in seen_history.py) then
    stops without a word, and one that does not keeps the entry from loading.
    So every Home Assistant import in the package is run here as it is
    written, at every depth and in try blocks too, every attribute reached
    from a name such an import binds is looked up (frontend.async_panel_exists,
    er.async_get, selector.SelectSelectorMode.LIST), and every call of a
    function or class reached that way is held to its real signature. A name
    the code binds any other way is a local and is not followed, so what an
    object has (hass.config_entries, a ConfigEntry, a selector made from its
    class) and the arguments its methods take are not seen here.

    Returns the objects the imports bound, by file and name, None for an
    import that failed.
    """
    resolved: dict[tuple[str, str], Any] = {}
    for path in sorted(COMPONENT.rglob("*.py")):
        imports, chains, calls = _scan(ast.parse(path.read_text(encoding="utf-8"), str(path)))
        if not imports:
            continue
        where = path.relative_to(COMPONENT).as_posix()
        objects: dict[int, Any] = {}
        missed: dict[str, BaseException] = {}
        for statement, alias in imports:
            alone = ast.copy_location(
                ast.ImportFrom(module=statement.module, names=[alias], level=0)
                if isinstance(statement, ast.ImportFrom)
                else ast.Import(names=[alias]),
                statement,
            )
            name = ".".join(filter(None, (getattr(statement, "module", None), alias.name)))
            try:
                objects[id(alias)] = _as_written(path, alone)[_bound_name(alias)]
            except Exception as err:
                missed.setdefault(f"{where}:{statement.lineno} {name}", err)
            resolved[(where, _bound_name(alias))] = objects.get(id(alias))

        followed: dict[tuple[tuple[str, ...], tuple[str, ...]], tuple[Any, Any] | None] = {}
        reached: dict[int, tuple[Any, Any] | None] = {}
        for chain, scope in chains:
            steps = [chain]
            while isinstance(steps[-1].value, ast.Attribute):
                steps.append(steps[-1].value)
            steps.reverse()
            root = steps[0].value
            binding = scope.lookup(root.id) if isinstance(root, ast.Name) else None
            if binding is None or id(binding[1]) not in objects:
                continue
            key = (_same(binding), tuple(step.attr for step in steps))
            if key not in followed:
                followed[key] = _follow(
                    path, root.id, objects[id(binding[1])], steps, where, missed
                )
            reached[id(chain)] = followed[key]

        bound = 0
        for call, scope in calls:
            if isinstance(call.func, ast.Name):
                binding = scope.lookup(call.func.id)
                found = (
                    (objects[id(binding[1])], None)
                    if binding is not None and id(binding[1]) in objects
                    else None
                )
            else:
                found = reached.get(id(call.func))
            if found is None:
                continue
            try:
                bound += _fits(*found, call)
            except Exception as err:
                missed.setdefault(f"{where}:{call.lineno} {ast.unparse(call.func)}(...)", err)

        for what, err in missed.items():
            with report.step("names", what):
                raise err
        if not missed:
            with report.step("names", f"{where}, {len(imports)} imports, "
                             f"{len(followed)} attributes, {bound} calls"):
                pass
    return resolved


def _statistics(hass: Any, report: Report, resolved: dict[tuple[str, str], Any]) -> None:
    """seen_history.py's call into the recorder, which goes through its executor.

    The function is handed to async_add_executor_job rather than called, so the
    arguments it gets are held to its real signature here, the ones the source
    gives it, read from the source.
    """
    path = COMPONENT / "seen_history.py"
    function = resolved.get(("seen_history.py", "statistics_during_period"))
    if function is None and ("seen_history.py", "statistics_during_period") in resolved:
        # Its import is a failure of its own above, with the reason.
        return
    call = next(
        (
            node
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8")))
            if isinstance(node, ast.Call)
            and node.args
            and isinstance(node.args[0], ast.Name)
            and node.args[0].id == "statistics_during_period"
        ),
        None,
    )
    line = call.lineno if call is not None else "?"
    with report.step("names", f"seen_history.py:{line} statistics_during_period(...), "
                     "the arguments the history gives it"):
        assert call is not None and function is not None, (
            "seen_history.py no longer imports statistics_during_period and hands it to "
            "the executor: follow the call here"
        )

        def given(node: ast.expr) -> Any:
            if isinstance(node, ast.Name) and node.id == "hass":
                return hass
            try:
                return ast.literal_eval(node)
            except ValueError:
                return ast.unparse(node)

        inspect.signature(function).bind(
            *(given(arg) for arg in call.args[1:]),
            **{keyword.arg: given(keyword.value) for keyword in call.keywords if keyword.arg},
        )


async def _config_flow(hass: Any, report: Report, view: Any) -> None:
    flow_module = _module("config_flow")
    discovery = _module("discovery")

    def new_flow() -> Any:
        flow = flow_module.CtcConfigFlow()
        flow.hass = hass
        flow.handler = DOMAIN
        flow.flow_id = "smoke"
        flow.context = {"source": "user"}
        return flow

    with report.step("flow", "config: user, the first menu"):
        result = await new_flow().async_step_user()
        assert result["type"] == "menu", result
        _check_step(view, "config", result)

    with report.step("flow", "config: manual, the address form"):
        fields = _check_step(view, "config", await new_flow().async_step_manual())
        assert fields == ["host", "modbus_port", "web_port", "slave"], fields

    with report.step("flow", "config: modbus_failed"):
        flow = new_flow()
        flow._host = HOST
        _check_step(view, "config", await flow.async_step_modbus_failed())

    with report.step("flow", "config: modbus_busy"):
        flow = new_flow()
        flow._host = HOST
        _check_step(view, "config", await flow.async_step_modbus_busy())

    with report.step("flow", "config: confirm, a unit that turned up by itself"):
        flow = new_flow()
        flow._host = HOST
        flow._model = "EcoZenith i255"
        _check_step(view, "config", await flow.async_step_confirm())

    async def networks(_hass: Any) -> list[ipaddress.IPv4Network]:
        return [ipaddress.IPv4Network("192.0.2.0/24")]

    async def sweep(*_args: Any, **_kwargs: Any) -> list[Any]:
        return [discovery.DiscoveredDisplay(HOST, "settings_ezi2xx.bin")]

    with report.step("flow", "config: scan, one unit found"):
        with (
            patch.object(flow_module, "async_home_assistant_networks", networks),
            patch.object(flow_module, "async_discover", sweep),
        ):
            result = await new_flow().async_step_scan()
        assert _check_step(view, "config", result) == ["picked"], result

    async def read_menu(*_args: Any, **_kwargs: Any) -> Any:
        return _reading()

    with report.step("flow", "config: slow, the display's pages as tick boxes"):
        flow = new_flow()
        flow._host = HOST
        with patch.object(flow_module, "async_discover_pages", read_menu):
            result = await flow.async_step_slow()
        fields = _check_step(view, "config", result)
        # 0.29.0: the set-up form offers the live page and its interval as well.
        assert fields == [
            "slow_pages", "slow_interval", "restore_page", "live_page", "live_interval",
        ], fields


async def _options_flow(hass: Any, report: Report, view: Any) -> None:
    flow_module = _module("config_flow")
    catalogue = _module("catalogue")
    const = _module("const")
    stored = catalogue.pages_to_storage(_pages())
    filled = {
        const.CONF_MENU: stored,
        const.CONF_SLOW_PAGES: stored[:1],
        const.CONF_MENU_ROOT: MENU_ROOT,
    }

    def new_flow(options: dict[str, Any]) -> Any:
        entry = SimpleNamespace(
            entry_id="smoke",
            data={"host": HOST, const.CONF_DISPLAY: True},
            options=options,
        )
        flow = flow_module.CtcOptionsFlow(entry)
        flow.hass = hass
        flow.handler = entry.entry_id
        flow.flow_id = "smoke-options"
        flow.context = {}
        return flow

    for label, options in (("empty menu", {}), ("filled menu", filled)):
        with report.step("flow", f"options: init, {label}"):
            fields = _check_step(view, "options", await new_flow(options).async_step_init())
            assert ("slow_pages" in fields) is bool(options), fields
            assert "find_page" in fields, fields

    async def read_again(*_args: Any, **_kwargs: Any) -> Any:
        return _reading()

    with report.step("flow", "options: rescan, the menu read again"):
        with patch.object(flow_module, "async_rescan_pages", read_again):
            result = await new_flow(filled).async_step_rescan()
        fields = _check_step(view, "options", result)
        assert fields == ["slow_pages", "slow_interval", "restore_page"], fields

    class _Shown:
        """A display client that says which page the panel shows, and nothing else."""

        panel = asyncio.Lock()

        async def async_current_page(self) -> int:
            return 42

    for label, client in (("the page shown", _Shown()), ("no page", None)):
        with report.step("flow", f"options: find_page, the form, {label}"):
            flow = new_flow(filled)
            if client is not None:
                flow._web_client = lambda client=client: client
                result = await flow.async_step_find_page()
            else:
                result = flow._find_page_form(None, "page_not_found")
            assert _check_step(view, "options", result) == ["page"], result

    with report.step("flow", "options: find_page_found, what the search found"):
        flow = new_flow(filled)
        page_search = _module("page_search")
        flow._search = page_search.PageSearch(
            target=22, reason=page_search.FOUND, page=_pages()[0], root=MENU_ROOT, tries=5
        )
        assert _check_step(view, "options", await flow.async_step_find_page_found()) == []


def _page_schema(report: Report, view: Any) -> None:
    """The tick boxes on their own, empty and filled, through the real selectors."""
    flow_module = _module("config_flow")
    for label, pages in (("empty", []), ("filled", _pages())):
        with report.step("schema", f"_slow_schema, {label} menu"):
            schema = flow_module._slow_schema(pages, [page.page for page in pages], 1800, True)
            data = view._prepare_result_json(
                {"type": "form", "step_id": "slow", "data_schema": schema}
            )
            options = data["data_schema"][0]["selector"]["select"]["options"]
            assert len(options) == len(pages), options


async def _dashboard(hass: Any, report: Report) -> None:
    from homeassistant.components.lovelace.dashboard import LovelaceConfig

    dashboard = _module("dashboard")
    with report.step("page", "dashboard._page_class(), made and asked for its layout"):
        page_class = dashboard._page_class()
        assert issubclass(page_class, LovelaceConfig), page_class.__mro__
        page = page_class(hass)
        config = await page.async_load(False)
        assert config.get("views"), config
        info = await page.async_get_info()
        assert info["mode"] == "auto-gen", info
        await page.async_json(False)


def _services(hass: Any, report: Report) -> None:
    from homeassistant.helpers import service
    from homeassistant.util.yaml import load_yaml_dict

    with report.step("actions", "services.yaml under Home Assistant's own schema"):
        schema = getattr(service, "_SERVICES_SCHEMA", None)
        assert schema is not None, (
            "homeassistant.helpers.service has no _SERVICES_SCHEMA any more: "
            "find where services.yaml is validated now and follow it here"
        )
        described = schema(load_yaml_dict(str(COMPONENT / "services.yaml")))
        _module("services").async_register_services(hass)
        registered = set(hass.services.async_services().get(DOMAIN, {}))
        assert registered == set(described), (registered, set(described))
        assert registered == set(STRINGS["services"]), (registered, set(STRINGS["services"]))


def _read_the_log(records: list[logging.LogRecord], strict: bool, report: Report) -> None:
    """The integration's own errors, and Home Assistant's notices of removals ahead."""
    for record in records:
        message = record.getMessage()
        if record.name.startswith(PACKAGE) and record.levelno >= logging.ERROR:
            report.failures.append(f"log {record.name}: {message}")
            print(f"log      FAIL  the integration logged an error: {message}")
        elif (
            record.name.startswith("homeassistant")
            and DOMAIN in message
            and any(word in message.lower() for word in _DEPRECATION_WORDS)
        ):
            if strict:
                report.failures.append(f"deprecated {message}")
                print(f"log      FAIL  {message}")
            else:
                print(f"log      note  {message}")


async def _start(hass: Any) -> None:
    """What bootstrap sets up before any integration, as far as the smoke needs it."""
    from homeassistant import config_entries, loader
    from homeassistant.helpers import (
        area_registry,
        category_registry,
        device_registry,
        entity_registry,
        floor_registry,
        frame,
        issue_registry,
        label_registry,
    )

    # The frame helper is how Home Assistant tells an integration that it uses
    # something on its way out, and the notice names where to report it from
    # the loader's data; without either set up such a notice raises instead.
    # The device registry wants setting up before it loads. Older releases,
    # 2025.1 among them, have no set-up call for the frame helper or the
    # device registry.
    loader.async_setup(hass)
    for module in (frame, device_registry):
        if hasattr(module, "async_setup"):
            module.async_setup(hass)
    for module in (area_registry, category_registry, device_registry, entity_registry,
                   floor_registry, issue_registry, label_registry):
        await module.async_load(hass)
    hass.config_entries = config_entries.ConfigEntries(hass, {})
    await hass.config_entries.async_initialize()


async def _run(strict: bool, captured: Captured) -> int:
    from homeassistant.const import __version__ as ha_version
    from homeassistant.core import HomeAssistant

    mode = ", strict" if strict else ""
    print(f"Home Assistant {ha_version}, Python {sys.version.split()[0]}{mode}")
    report = Report()
    with tempfile.TemporaryDirectory() as config_dir:
        hass = HomeAssistant(config_dir)
        try:
            await _start(hass)
            imported = _import_everything(report)
            _statistics(hass, report, _names(report))
            # What a module that did not import would have built is not tried:
            # its import has failed the run already, with the reason.
            if "config_flow" in imported:
                view = _frontend_view()
                with patch.object(_module("config_flow"), "async_get_clientsession", _no_session):
                    await _config_flow(hass, report, view)
                    await _options_flow(hass, report, view)
                _page_schema(report, view)
            if "dashboard" in imported:
                await _dashboard(hass, report)
            if "services" in imported:
                _services(hass, report)
        finally:
            await hass.async_stop(force=True)
    _read_the_log(captured.records, strict, report)

    if report.failures:
        print(f"\n{len(report.failures)} of {report.count} failed: " + "; ".join(report.failures))
        return 1
    print(f"\nAll {report.count} load and build under Home Assistant {ha_version}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--strict",
        action="store_true",
        help="fail on deprecations attributed to the integration, and on Home Assistant's "
        "notices that it uses something on its way out",
    )
    args = parser.parse_args()
    # The steps and the log in the order they happened, also through a pipe.
    sys.stdout.reconfigure(line_buffering=True)
    # Home Assistant's own deprecations of the libraries it uses are shown,
    # once each, but they are its to fix and fail nothing here.
    warnings.filterwarnings("default", category=DeprecationWarning, module=r"homeassistant(\.|$)")
    if args.strict:
        ours = rf"{re.escape(PACKAGE)}(\.|$)"
        for category in (DeprecationWarning, PendingDeprecationWarning):
            warnings.filterwarnings("error", category=category, module=ours)
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
    captured = Captured()
    logging.getLogger().addHandler(captured)
    return asyncio.run(_run(args.strict, captured))


if __name__ == "__main__":
    # Home Assistant's C extensions can crash Python's own shutdown after the
    # work is done (seen with Homebrew's 3.14), so the result is flushed and
    # the process leaves at once; everything was closed above.
    rc = main()
    logging.shutdown()
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(rc)
