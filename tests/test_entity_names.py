"""The integration's own entities are named from the translations (R23).

Every entity the integration names itself takes its name from strings.json and
the two translations, under a translation key that is the entity's key, so the
name follows Home Assistant's language: English in strings.json and en.json,
and in sv.json the Swedish names the entities had up to 0.21.1, word for word,
since the entities were named in code until then. The display's own rows are
the exception: the panel names them, in the Swedish the menu is read in, and
they have no translation. Here, without Home Assistant: every key the code
names has a name in all three files and no file has a name nobody asks for,
sv.json is 0.21.1's names to the letter and agrees with the Swedish names the
tables keep beside their keys, the controls keep the prefix the page drops, no
entity class sets a name of its own but the display's rows, an explanation
that names another entity names it as the entity is named in its language, and
the forms carry no Swedish of their own. tests/test_homeassistant_language.py
shows the same under a real core: the names, and the entity ids, old and new.
"""

from __future__ import annotations

import ast
import json
import re

import pytest

from conftest import COMPONENT, load

#: Every name in sv.json as 0.21.1 gave it in code, by platform and key.
SWEDISH_AT_0_21_1 = {
    "binary_sensor": {
        "alarm": "Larm",
        "blocked": "Blockerad",
        "compressor_running": "Kompressor i drift",
        "defrosting": "Avfrostning",
        "immersion_active": "Elpatron aktiv",
        "smartgrid_active": "SmartGrid aktiv",
    },
    "button": {
        "release_control": "Släpp all styrning",
    },
    "event": {
        "events": "Händelser",
    },
    "number": {
        "ctl_dhw_setpoint": "Styr varmvattenbörvärde",
        "ctl_extra_dhw": "Styr extra varmvatten",
        "ctl_immersion_lower": "Styr max elpatron nedre",
        "ctl_immersion_upper": "Styr max elpatron övre",
        "ctl_max_rps": "Styr max varvtal",
        "ctl_room_setpoint_1": "Styr rumsbörvärde",
    },
    "select": {
        "ctl_dhw_mode": "Styr varmvattenläge",
        "ctl_price_mode": "Styr elprisläge",
        "ctl_zone_mode_1": "Styr driftläge VS1",
    },
    "sensor": {
        "active_overrides": "Aktiva överstyrningar",
        "bootloader": "Bootloaderversion",
        "compressor_hours": "Kompressordrifttid",
        "compressor_hours_24h": "Kompressordrift senaste dygnet",
        "compressor_kwh": "Kompressorenergi",
        "control_sw": "Programversion styrenhet",
        "control_sw_year": "Programversion styrenhet, år",
        "cop_day": "Dygnsvärmefaktor",
        "cop_first_year": "Värmefaktor, första året",
        "cop_lifetime": "Värmefaktor, hela livslängden",
        "cop_month": "Månadsvärmefaktor",
        "cop_week": "Veckovärmefaktor",
        "cop_year": "Årsvärmefaktor",
        "current_l1": "Ström L1",
        "current_l2": "Ström L2",
        "current_l3": "Ström L3",
        "defrosts_today": "Avfrostningar i dag",
        "degree_minutes": "Gradminuter",
        "dhw_capacity": "Varmvattenkapacitet",
        "dhw_circulation": "Varmvattencirkulation",
        "dhw_lower_temp": "Varmvatten nedre",
        "dhw_stop_temp": "Stopptemperatur varmvatten",
        "dhw_temp": "Varmvatten",
        "dhw_temp_raw": "Varmvattentemperatur (62003)",
        "display_fw": "Programversion display",
        "display_harvest": "Senaste displayskörd",
        "hp1_brine_in": "Köldbärare in",
        "hp1_brine_out": "Köldbärare ut",
        "hp1_brine_pump": "Brinepump",
        "hp1_charge_pump": "Laddpump",
        "hp1_defrost_timer": "Avfrostningstimer",
        "hp1_discharge": "Hetgas",
        "hp1_fan": "Fläkt",
        "hp1_high_pressure": "Högtryck",
        "hp1_in": "Värmepump in",
        "hp1_low_pressure": "Lågtryck",
        "hp1_out": "Värmepump ut",
        "hp1_outdoor_temp": "Utetemperatur vid värmepump",
        "hp1_power": "Tillförd effekt värmepump",
        "hp1_rps": "Kompressorvarvtal",
        "hp1_status": "Värmepump status",
        "hp1_suction": "Suggas",
        "hp_fw": "Programversion VP-styrkort",
        "hp_model": "Värmepumpsmodell",
        "hs1_flow": "Framledning VS1",
        "hs1_flow_setpoint": "Framledning börvärde VS1",
        "hs1_status": "Värmesystem status",
        "immersion_kwh": "Elpatron energi",
        "immersion_lower_kw": "Elpatron nedre",
        "immersion_upper_kw": "Elpatron övre",
        "last_alarm": "Senaste larm",
        "last_defrost": "Senaste avfrostning",
        "last_run": "Senaste körning",
        "last_start": "Senaste start",
        "made": "Tillverkad",
        "mean_run_24h": "Medelkörning senaste dygnet",
        "outdoor_temp": "Utetemperatur",
        "radiator_temp": "Radiatorvatten",
        "return_temp": "Returtemperatur",
        "room_temp_1": "Rumstemperatur",
        "room_temp_2": "Rumstemperatur 2",
        "serial": "Serienummer",
        "set_adjust_1": "Kurvjustering",
        "set_dhw_mode": "Inställt varmvattenläge",
        "set_extra_dhw": "Extra varmvatten kvar",
        "set_heating_mode_1": "Inställt värmeläge",
        "set_hp1_blocked": "Värmepump tillåten",
        "set_max_immersion_lower": "Max elpatron nedre",
        "set_max_immersion_upper": "Max elpatron övre",
        "set_max_rps_1": "Inställt max varvtal",
        "set_room_1": "Inställd rumstemperatur",
        "set_slope_1": "Kurvlutning",
        "sg_mode": "SmartGrid-läge",
        "starts_today": "Starter i dag",
        "system_status": "Systemstatus",
        "tank_lower_setpoint": "Nedre tank börvärde",
    },
}

FILES = ("strings.json", "translations/en.json", "translations/sv.json")
PLATFORMS = ("binary_sensor", "button", "event", "number", "select", "sensor", "switch")


def _entity(name: str) -> dict[str, dict[str, dict]]:
    return json.loads((COMPONENT / name).read_text(encoding="utf-8"))["entity"]


def _names(name: str) -> dict[str, dict[str, str]]:
    return {
        platform: {key: item["name"] for key, item in items.items()}
        for platform, items in _entity(name).items()
    }


def _tree(name: str) -> ast.Module:
    return ast.parse((COMPONENT / name).read_text(encoding="utf-8"))


def _constant_args(tree: ast.AST, callee: str) -> list[tuple[str, str]]:
    """The first two string arguments of every call to ``callee``: key and name."""
    found = []
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == callee
            and len(node.args) >= 2
            and all(isinstance(arg, ast.Constant) for arg in node.args[:2])
        ):
            found.append((node.args[0].value, node.args[1].value))
    return found


def _assigned(tree: ast.Module, name: str) -> ast.expr:
    """The expression assigned to a module level name."""
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == name for target in node.targets
        ):
            return node.value
    raise AssertionError(name)


def _own_translation_keys(tree) -> set[str]:
    """The translation keys a module sets on an entity class of its own, as a literal."""
    return {
        node.value.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Assign)
        and any(
            isinstance(target, ast.Name | ast.Attribute)
            and getattr(target, "id", getattr(target, "attr", "")) == "_attr_translation_key"
            for target in node.targets
        )
        and isinstance(node.value, ast.Constant)
    }


def _keys_in_code(const, transitions) -> dict[str, set[str]]:
    """Every translation key the code names, by platform: the entities' keys."""
    sensor = _tree("sensor.py")
    identity = _assigned(sensor, "IDENTITY_ROWS")
    spans = next(
        node.iter for node in ast.walk(sensor)
        if isinstance(node, ast.For) and isinstance(node.target, ast.Name)
        and node.target.id == "span"
    )
    own_keys = _own_translation_keys(sensor)
    return {
        "sensor": (
            {d.key for d in const.MODBUS_SENSORS + const.MODBUS_SETTINGS}
            | {row.elts[0].value for row in identity.elts}
            | {f"cop_{span}" for span in ast.literal_eval(spans)}
            | {f"aux_share_{span}" for span in load("cop").SHARE_SPANS}
            | {
                f"integrated_{side}"
                for side in ast.literal_eval(_assigned(sensor, "INTEGRATED_SIDES"))
            }
            | {item.key for item in transitions.TRANSITION_SENSORS}
            | {transitions.MEAN_RUN_KEY}
            | own_keys
        ),
        "binary_sensor": {
            key for key, _name in _constant_args(_tree("binary_sensor.py"), "DerivedBinary")
        } | _own_translation_keys(_tree("binary_sensor.py")),
        "number": {r.key for r in const.CONTROL_NUMBERS},
        "select": {r.key for r in const.CONTROL_SELECTS},
        "button": {"release_control"},
        "event": {"events"},
        "switch": {"debug_report"},
    }


# ------------------------------------------------------------- the files


def test_sv_json_holds_the_names_of_0_21_1_word_for_word():
    # Every entity 0.21.1 had keeps its Swedish name to the letter; an entity
    # added since has a name of its own beside them.
    swedish = _names("translations/sv.json")
    for platform, items in SWEDISH_AT_0_21_1.items():
        for key, name in items.items():
            assert swedish[platform].get(key) == name, (platform, key)


@pytest.mark.parametrize("name", FILES)
def test_every_key_the_code_names_has_a_name_and_no_name_is_left_over(name, const):
    names = _names(name)
    assert set(names) == set(PLATFORMS)
    for platform, keys in _keys_in_code(const, load("transitions")).items():
        assert set(names[platform]) == keys, (name, platform)
        assert all(names[platform].values()), (name, platform)


def test_the_english_is_the_same_in_strings_json_and_en_json_and_is_english():
    english = _names("strings.json")
    assert english == _names("translations/en.json")
    swedish = _names("translations/sv.json")
    for platform, items in english.items():
        for key, name in items.items():
            assert not re.search(r"[åäöÅÄÖ]", name), (platform, key, name)
            assert name != swedish[platform][key], (platform, key)


@pytest.mark.parametrize("name", FILES)
def test_no_two_entities_of_a_platform_share_a_name(name):
    # One name would give a new installation's two entities ids that differ
    # only by a number Home Assistant adds, and the owner no way to tell them
    # apart in a list.
    for platform, items in _names(name).items():
        names = list(items.values())
        assert len(set(names)) == len(names), (name, platform)


@pytest.mark.parametrize("name", FILES)
def test_the_names_use_no_dash_as_punctuation(name):
    for platform, items in _names(name).items():
        for key, text in items.items():
            assert "–" not in text and "—" not in text and " - " not in text, (platform, key)


def test_the_translation_keys_are_what_home_assistant_accepts():
    for platform, items in _names("strings.json").items():
        for key in items:
            assert re.fullmatch(r"[a-z0-9_-]+", key), (platform, key)


def test_the_event_keeps_its_event_types_beside_its_name():
    for name in FILES:
        events = _entity(name)["event"]["events"]
        states = events["state_attributes"]["event_type"]["state"]
        assert set(states) >= {"kompressor_start", "larm", "systemstatus_andrad"}, name


# ------------------------------------------- the Swedish names in the code


def test_the_swedish_names_the_tables_keep_are_sv_json_s(const):
    swedish = _names("translations/sv.json")
    for description in const.MODBUS_SENSORS + const.MODBUS_SETTINGS:
        assert swedish["sensor"][description.key] == description.name, description.key
    for register in const.CONTROL_NUMBERS:
        assert swedish["number"][register.key] == register.name, register.key
    for register in const.CONTROL_SELECTS:
        assert swedish["select"][register.key] == register.name, register.key
    for key, name in _constant_args(_tree("binary_sensor.py"), "DerivedBinary"):
        assert swedish["binary_sensor"][key] == name, key
    transitions = load("transitions")
    for item in transitions.TRANSITION_SENSORS:
        assert swedish["sensor"][item.key] == item.name, item.key
    assert swedish["sensor"][transitions.MEAN_RUN_KEY] == transitions.MEAN_RUN_NAME
    sensor = _tree("sensor.py")
    for row in _assigned(sensor, "IDENTITY_ROWS").elts:
        key, name = row.elts[0].value, row.elts[1].value
        assert swedish["sensor"][key] == name, key
    for cls, prefix in (("CtcCopSensor", "cop_"), ("CtcShareSensor", "aux_share_")):
        body = next(
            node for node in sensor.body if isinstance(node, ast.ClassDef) and node.name == cls
        )
        names = next(
            node.value for node in ast.walk(body)
            if isinstance(node, ast.Assign)
            and any(isinstance(t, ast.Name) and t.id == "NAMES" for t in node.targets)
        )
        for span, name in ast.literal_eval(names).items():
            assert swedish["sensor"][f"{prefix}{span}"] == name, (cls, span)


def test_every_control_name_starts_with_a_prefix_the_page_drops(dashboard_views):
    for name, prefix in (
        ("translations/sv.json", dashboard_views._CONTROL_PREFIX),
        ("translations/en.json", dashboard_views._CONTROL_PREFIX_EN),
        ("strings.json", dashboard_views._CONTROL_PREFIX_EN),
    ):
        names = _names(name)
        for platform in ("number", "select"):
            for key, text in names[platform].items():
                assert text.startswith(prefix), (name, key)
    assert set(dashboard_views._CONTROL_PREFIXES) == {"Styr ", "Control "}


def test_the_page_drops_the_prefix_in_either_language(dashboard_views):
    trim = dashboard_views._Builder._under_a_control_heading
    assert trim("Styr max elpatron nedre") == "Max elpatron nedre"
    assert trim("Control max lower immersion heater") == "Max lower immersion heater"
    assert trim("Släpp all styrning") == "Släpp all styrning"
    assert trim("Release all control") == "Release all control"


# --------------------------------------------------------- the entity classes


def _entity_classes(name: str) -> list[ast.ClassDef]:
    return [
        node for node in _tree(name).body
        if isinstance(node, ast.ClassDef) and any("Entity" in ast.unparse(b) for b in node.bases)
    ]


def _sets(node: ast.AST, attribute: str) -> bool:
    for child in ast.walk(node):
        if isinstance(child, ast.Assign | ast.AnnAssign):
            targets = child.targets if isinstance(child, ast.Assign) else [child.target]
            for target in targets:
                if attribute in (getattr(target, "id", None), getattr(target, "attr", None)):
                    return True
    return False


@pytest.mark.parametrize("platform", PLATFORMS)
def test_every_entity_class_is_named_by_its_translation_key_but_the_display_rows(platform):
    classes = _entity_classes(f"{platform}.py")
    assert classes
    for cls in classes:
        if cls.name == "CtcDisplaySensor":
            # The panel names its rows; the integration has no word for them.
            assert _sets(cls, "_attr_name") and not _sets(cls, "_attr_translation_key")
            continue
        assert _sets(cls, "_attr_translation_key"), cls.name
        assert not _sets(cls, "_attr_name"), f"{cls.name} names itself in code"
        assert "_attr_has_entity_name = True" in ast.unparse(cls), cls.name


# ------------------------------------------- the names the explanations give


#: Where an explanation names another entity, by its name: the text's key, and
#: the platform and key of the entity it names. The Swedish text has the Swedish
#: name and the English text the English one, as sv.json and en.json have them.
NAMED = (
    ("dhw_temp_raw", "sensor", "dhw_temp"),
    ("hp1_out", "sensor", "hp1_in"),
    ("last_start", "sensor", "hp1_status"),
    ("starts_today", "sensor", "hp1_status"),
    ("defrosts_today", "sensor", "hp1_status"),
    ("alarm", "sensor", "last_alarm"),
    ("active_overrides", "button", "release_control"),
    ("ctl_zone_mode_1", "sensor", "set_heating_mode_1"),
    ("ctl_dhw_mode", "sensor", "set_dhw_mode"),
    ("ctl_dhw_setpoint", "sensor", "dhw_stop_temp"),
    ("ctl_extra_dhw", "sensor", "set_extra_dhw"),
    ("set_extra_dhw", "number", "ctl_extra_dhw"),
)


@pytest.mark.parametrize(("key", "platform", "named"), NAMED)
def test_an_explanation_names_another_entity_by_its_name_in_the_language(
    explanations, key, platform, named
):
    swedish = _names("translations/sv.json")[platform][named]
    english = _names("translations/en.json")[platform][named]
    assert swedish.casefold() in explanations.explain(key).casefold(), (key, swedish)
    assert english.casefold() in explanations.explain(key, "en").casefold(), (key, english)


def test_the_sources_name_the_entities_they_are_worked_out_from_in_either_language(
    explanations,
):
    for key, named in (
        ("compressor_running", ("hp1_status",)),
        ("events", ("hp1_status", "system_status", "sg_mode")),
        ("immersion_active", ("immersion_upper_kw", "immersion_lower_kw")),
        ("smartgrid_active", ("sg_mode",)),
    ):
        for name, lang in (("translations/sv.json", "sv"), ("translations/en.json", "en")):
            source = explanations.source(key, lang=lang)
            for item in named:
                assert _names(name)["sensor"][item] in source, (key, item, lang)


# ---------------------------------------------------------------- the forms


#: The modules whose strings reach a person through Home Assistant's own forms,
#: notices and errors: every word they show is in strings.json.
FORM_MODULES = ("config_flow.py", "__init__.py", "services.py", "websocket.py", "diagnostics.py")


def _docstrings(tree: ast.Module) -> set[int]:
    found = set()
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef)
            and node.body
            and isinstance(node.body[0], ast.Expr)
            and isinstance(node.body[0].value, ast.Constant)
        ):
            found.add(id(node.body[0].value))
    return found


@pytest.mark.parametrize("name", FORM_MODULES)
def test_no_form_text_is_written_in_swedish_in_the_code(name):
    tree = _tree(name)
    docstrings = _docstrings(tree)
    swedish = [
        node.value for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
        and id(node) not in docstrings and re.search(r"[åäöÅÄÖ]", node.value)
    ]
    assert swedish == [], name


def test_every_form_step_error_and_abort_the_flow_names_has_a_text():
    flow = (COMPONENT / "config_flow.py").read_text(encoding="utf-8")
    # The set-up's words are under config, Configure's under options.
    setup, configure = flow.split("\nclass CtcOptionsFlow", 1)
    for name in FILES:
        texts = json.loads((COMPONENT / name).read_text(encoding="utf-8"))
        config, options = texts["config"], texts["options"]
        for step in ("user", "scan", "manual", "modbus_failed", "modbus_busy", "slow", "confirm"):
            assert step in config["step"], (name, step)
        errors = set(re.findall(r'"base": "([a-z_]+)"', setup))
        for error in errors | {"only_configured", "nothing_found"}:
            assert error in config["error"], (name, error)
        for reason in set(re.findall(r'reason="([a-z_]+)"', setup)):
            assert reason in config["abort"], (name, reason)
        for reason in set(re.findall(r'reason="([a-z_]+)"', configure)):
            assert reason in options["abort"], (name, reason)
        assert "panel_busy" in options["abort"], name
        for step in ("init", "rescan"):
            assert step in options["step"], (name, step)
        assert config["flow_title"] == "{name}"
