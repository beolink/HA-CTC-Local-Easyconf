"""quality_scale.yaml is a checklist that cannot drift from the code (roadmap R56).

hassfest validates the file only for core integrations: for a custom one its
quality scale check returns before it opens the file, so a rule marked done
here would never be questioned in CI. This holds the file to what hassfest
asks of a core integration, every rule present with done, todo or exempt and
a comment where exempt, and to more than that: a comment on every row, the
checks hassfest runs for the rules marked done, a look at the code or the
README for the others where one can be had, and no quality scale in the
manifest until every rule of its tier and the tiers below is met.

The file is read without a YAML library, since the suite runs without one;
the reader below takes exactly the layout the file has and refuses anything
else. tests/test_homeassistant_quality_scale.py reads it with Home Assistant's
own YAML loader and compares the rules with the installed release.
"""

from __future__ import annotations

import ast
import json
import re

import pytest

from conftest import COMPONENT, ROOT

#: Every rule of the quality scale and its tier, as hassfest's ALL_RULES has
#: them in Home Assistant 2026.9 (script/hassfest/quality_scale.py).
TIERS = ("bronze", "silver", "gold", "platinum")
RULES: dict[str, str] = {
    **dict.fromkeys(
        (
            "action-setup", "appropriate-polling", "brands", "common-modules",
            "config-flow", "config-flow-test-coverage", "dependency-transparency",
            "docs-actions", "docs-conditions", "docs-high-level-description",
            "docs-installation-instructions", "docs-removal-instructions",
            "docs-triggers", "entity-event-setup", "entity-unique-id",
            "has-entity-name", "runtime-data", "test-before-configure",
            "test-before-setup", "unique-config-entry",
        ),
        "bronze",
    ),
    **dict.fromkeys(
        (
            "action-exceptions", "config-entry-unloading",
            "docs-configuration-parameters", "docs-installation-parameters",
            "entity-unavailable", "integration-owner", "log-when-unavailable",
            "parallel-updates", "reauthentication-flow", "test-coverage",
        ),
        "silver",
    ),
    **dict.fromkeys(
        (
            "devices", "diagnostics", "discovery", "discovery-update-info",
            "docs-data-update", "docs-examples", "docs-known-limitations",
            "docs-supported-devices", "docs-supported-functions",
            "docs-troubleshooting", "docs-use-cases", "dynamic-devices",
            "entity-category", "entity-device-class", "entity-disabled-by-default",
            "entity-translations", "exception-translations", "icon-translations",
            "reconfiguration-flow", "repair-issues", "stale-devices",
        ),
        "gold",
    ),
    **dict.fromkeys(("async-dependency", "inject-websession", "strict-typing"), "platinum"),
}

STATUSES = {"done", "todo", "exempt"}

_RULE = re.compile(r"^  ([a-z][a-z-]*):$")
_FIELD = re.compile(r"^    (status|comment): (\S.*)$")


def read_scale() -> dict[str, dict[str, str]]:
    """The rules as {rule: {"status": ..., "comment": ...}}, in file order."""
    lines = (COMPONENT / "quality_scale.yaml").read_text(encoding="utf-8").splitlines()
    rules: dict[str, dict[str, str]] = {}
    current: dict[str, str] | None = None
    seen_root = False
    for number, line in enumerate(lines, start=1):
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if line == "rules:":
            assert not seen_root, f"rad {number}: rules två gånger"
            seen_root = True
            continue
        assert seen_root, f"rad {number}: före rules:"
        if match := _RULE.match(line):
            assert match[1] not in rules, f"rad {number}: {match[1]} två gånger"
            current = rules[match[1]] = {}
            continue
        match = _FIELD.match(line)
        assert match and current is not None, f"rad {number}: oväntad rad {line!r}"
        assert match[1] not in current, f"rad {number}: {match[1]} två gånger"
        value = match[2]
        # A plain scalar, as the file writes them: a colon followed by a space
        # or a hash after a space would turn it into something else in YAML.
        assert ": " not in value and " #" not in value, f"rad {number}: {value!r}"
        assert value[0] not in "\"'&*!|>%@`{[,?-", f"rad {number}: {value!r}"
        current[match[1]] = value
    return rules


SCALE = read_scale()


def _manifest() -> dict:
    return json.loads((COMPONENT / "manifest.json").read_text(encoding="utf-8"))


def _module(name: str) -> ast.Module:
    return ast.parse((COMPONENT / name).read_text(encoding="utf-8"))


def _readme() -> str:
    return (ROOT / "README.md").read_text(encoding="utf-8")


def _done(rule: str) -> bool:
    return SCALE[rule]["status"] == "done"


# --------------------------------------------------------------- the file


def test_every_rule_of_the_scale_is_there_and_nothing_else():
    assert set(SCALE) == set(RULES)


def test_the_rules_stand_tier_by_tier():
    tiers = [RULES[name] for name in SCALE]
    assert tiers == sorted(tiers, key=TIERS.index)


@pytest.mark.parametrize("rule", sorted(RULES))
def test_every_row_has_a_status_and_says_why(rule):
    row = SCALE[rule]
    assert set(row) == {"status", "comment"}, rule
    assert row["status"] in STATUSES, rule
    assert len(row["comment"]) > 10, rule


# ---------------------------------------------------------- the manifest


def test_the_manifest_claims_no_tier_the_checklist_does_not_meet():
    tier = _manifest().get("quality_scale")
    if tier is None:
        return
    assert tier in TIERS, tier
    claimed = TIERS[: TIERS.index(tier) + 1]
    missing = [
        name for name, rule_tier in RULES.items()
        if rule_tier in claimed and SCALE[name]["status"] == "todo"
    ]
    assert not missing, f"{tier} kräver även {missing}"


# ------------------------------------------- the checks behind a rule marked done
#
# The first five are hassfest's own validators for these rules, read off
# script/hassfest/quality_scale_validation in Home Assistant 2026.9; the rest
# look at what the comment names. A rule on todo has no test here: the check
# runs the day the rule is marked done, and fails it if the code says otherwise.

PLATFORMS = ("binary_sensor", "button", "event", "number", "select", "sensor", "switch")


def _function(module: ast.Module, name: str) -> ast.AsyncFunctionDef | None:
    for node in module.body:
        if isinstance(node, ast.AsyncFunctionDef) and node.name == name:
            return node
    return None


def _calls(tree: ast.AST, name: str) -> bool:
    return any(
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == name
        for node in ast.walk(tree)
    )


def _source(name: str) -> str:
    return (COMPONENT / name).read_text(encoding="utf-8")


def _config_flow() -> None:
    assert _manifest().get("config_flow") is True
    assert (COMPONENT / "config_flow.py").is_file()


def _runtime_data() -> None:
    setup = _function(_module("__init__.py"), "async_setup_entry")
    assert setup is not None and len(setup.args.args) == 2
    entry = setup.args.args[1].arg
    assert any(
        isinstance(node, ast.Attribute)
        and isinstance(node.value, ast.Name)
        and node.value.id == entry
        and node.attr == "runtime_data"
        and isinstance(node.ctx, ast.Store)
        for node in ast.walk(setup)
    ), "async_setup_entry sätter inte entry.runtime_data"


def _raises_not_ready() -> bool:
    valid = {"ConfigEntryNotReady", "ConfigEntryAuthFailed", "ConfigEntryError"}
    for path in sorted(COMPONENT.glob("*.py")):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if (
                isinstance(node, ast.Raise)
                and isinstance(node.exc, ast.Call | ast.Name)
                and ast.unparse(node.exc).split("(")[0].split(".")[-1] in valid
            ):
                return True
    return False


def _test_before_setup() -> None:
    setup = _function(_module("__init__.py"), "async_setup_entry")
    assert setup is not None
    assert _calls(setup, "async_config_entry_first_refresh") or _raises_not_ready()


def _unique_config_entry() -> None:
    flow = _module("config_flow.py")
    assert _calls(flow, "_async_abort_entries_match") or (
        _calls(flow, "async_set_unique_id") and _calls(flow, "_abort_if_unique_id_configured")
    )


def _discovery() -> None:
    keys = {"bluetooth", "dhcp", "homekit", "mqtt", "ssdp", "usb", "zeroconf"}
    assert keys & set(_manifest()), "ingen upptäckt i manifestet"


def _reconfiguration_flow() -> None:
    assert "async def async_step_reconfigure(" in _source("config_flow.py")


def _parallel_updates() -> None:
    for platform in PLATFORMS:
        assert "\nPARALLEL_UPDATES = " in _source(f"{platform}.py"), platform


def _has_entity_name() -> None:
    for platform in PLATFORMS:
        for node in _module(f"{platform}.py").body:
            if isinstance(node, ast.ClassDef) and any(
                "Entity" in ast.unparse(base) for base in node.bases
            ):
                assert "_attr_has_entity_name = True" in ast.unparse(node), node.name


def _brands() -> None:
    assert (COMPONENT / "brand" / "icon.png").is_file()
    assert (COMPONENT / "brand" / "icon@2x.png").is_file()


def _action_setup() -> None:
    setup = _function(_module("__init__.py"), "async_setup")
    assert setup is not None and "async_register_services(hass)" in ast.unparse(setup)


def _diagnostics() -> None:
    assert (COMPONENT / "diagnostics.py").is_file()


def _integration_owner() -> None:
    assert _manifest().get("codeowners")


def _dependency_transparency() -> None:
    assert _manifest().get("requirements")


def _config_entry_unloading() -> None:
    assert _function(_module("__init__.py"), "async_unload_entry") is not None


def _repair_issues() -> None:
    assert "ir.async_create_issue(" in _source("__init__.py")


def _icon_translations() -> None:
    assert (COMPONENT / "icons.json").is_file()


def _entity_translations() -> None:
    # Every entity class but the display's rows, which the panel names, sets
    # a translation key and no name of its own; tests/test_entity_names.py
    # holds the names in the three files to the keys.
    names = json.loads((COMPONENT / "strings.json").read_text(encoding="utf-8"))["entity"]
    for platform in PLATFORMS:
        assert names.get(platform), platform
        for node in _module(f"{platform}.py").body:
            if not isinstance(node, ast.ClassDef) or not any(
                "Entity" in ast.unparse(base) for base in node.bases
            ):
                continue
            source = ast.unparse(node)
            if node.name == "CtcDisplaySensor":
                continue
            assert "_attr_translation_key" in source, node.name
            assert "_attr_name" not in source, node.name


CHECKS = {
    "config-flow": _config_flow,
    "runtime-data": _runtime_data,
    "test-before-setup": _test_before_setup,
    "unique-config-entry": _unique_config_entry,
    "discovery": _discovery,
    "reconfiguration-flow": _reconfiguration_flow,
    "parallel-updates": _parallel_updates,
    "has-entity-name": _has_entity_name,
    "brands": _brands,
    "action-setup": _action_setup,
    "diagnostics": _diagnostics,
    "integration-owner": _integration_owner,
    "dependency-transparency": _dependency_transparency,
    "config-entry-unloading": _config_entry_unloading,
    "repair-issues": _repair_issues,
    "icon-translations": _icon_translations,
    "entity-translations": _entity_translations,
}

#: The README section a documentation rule points at once it is done.
DOCS = {
    "docs-actions": "## Actions",
    "docs-high-level-description": "## What it reads",
    "docs-installation-instructions": "## Install",
    "docs-removal-instructions": "## Removing the integration",
    "docs-configuration-parameters": "## Options",
    "docs-known-limitations": "## Known limits",
    "docs-troubleshooting": "## Reporting a problem",
}


def test_the_checks_are_for_rules_of_the_scale():
    assert set(CHECKS) <= set(RULES)
    assert set(DOCS) <= set(RULES)


@pytest.mark.parametrize("rule", sorted(rule for rule in CHECKS if _done(rule)))
def test_a_rule_marked_done_passes_its_check(rule):
    CHECKS[rule]()


@pytest.mark.parametrize("rule", sorted(rule for rule in DOCS if _done(rule)))
def test_a_documentation_rule_marked_done_has_its_section_in_the_readme(rule):
    assert f"\n{DOCS[rule]}\n" in _readme(), rule


