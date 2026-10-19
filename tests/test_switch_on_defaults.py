"""What an earlier version switched off is switched on again by this one (R21).

Home Assistant reads ``entity_registry_enabled_default`` only when an entity is
first registered, so an installation from before 0.14.0 kept every entity that
version created switched off, and only the two houses had theirs switched on,
by hand. Each platform now switches on, before adding them, the entities whose
registry entry the integration switched off, and leaves alone what somebody
switched off. Here the rule runs against a registry stand-in, in both worlds,
and the call sites are held in the source: every platform at set-up, and only
there, both coordinators with the entry, the catch-up task holding the panel
until the reload that follows, and the identity read at every start. The
entry's option to switch off newly added entities is the owner's, and switches
nothing on (F4.1). The run under a real core is test_homeassistant_switch_on.py.
"""

from __future__ import annotations

import enum
import logging
import pathlib
from types import SimpleNamespace

import pytest

from conftest import load
import ha_stub

COMPONENT = pathlib.Path(__file__).resolve().parent.parent / "custom_components" / "ctc_ecozenith"
PLATFORMS = ("sensor", "binary_sensor", "number", "select", "button", "event", "switch")


class Disabler(enum.StrEnum):
    """Home Assistant's RegistryEntryDisabler, as far as this needs it."""

    INTEGRATION = "integration"
    USER = "user"
    CONFIG_ENTRY = "config_entry"


class FakeRegistry:
    def __init__(self, items) -> None:
        self.items = {item.entity_id: item for item in items}
        self.updates: list[tuple[str, object]] = []

    def async_update_entity(self, entity_id, *, disabled_by):
        self.updates.append((entity_id, disabled_by))
        self.items[entity_id].disabled_by = disabled_by


def _item(entity_id: str, unique_id: str, disabled_by=None, entry_id: str = "entry"):
    return SimpleNamespace(
        entity_id=entity_id,
        unique_id=unique_id,
        domain=entity_id.split(".")[0],
        disabled_by=disabled_by,
        config_entry_id=entry_id,
    )


def _entity(unique_id: str, enabled_default: bool = True):
    return SimpleNamespace(unique_id=unique_id, entity_registry_enabled_default=enabled_default)


@pytest.fixture()
def switch(monkeypatch):
    """The entity module with the registry it asks for replaced by a stand-in."""
    ha_stub.install()
    import homeassistant.helpers.entity_registry as er

    module = load("entity")
    holder = SimpleNamespace(registry=FakeRegistry([]))
    monkeypatch.setattr(er, "async_get", lambda hass: holder.registry)
    monkeypatch.setattr(
        er,
        "async_entries_for_config_entry",
        lambda registry, entry_id: [
            item for item in registry.items.values() if item.config_entry_id == entry_id
        ],
    )
    if not hasattr(er, "RegistryEntryDisabler"):
        monkeypatch.setattr(er, "RegistryEntryDisabler", Disabler, raising=False)
    disabler = er.RegistryEntryDisabler

    def run(items, platform, entities, entry=None):
        holder.registry = FakeRegistry(items)
        count = module.async_switch_on_new_defaults(
            None, entry or SimpleNamespace(entry_id="entry"), platform, entities
        )
        return count, holder.registry

    run.disabler = disabler
    return run


# ------------------------------------------------------------------ the rule


def test_what_the_integration_switched_off_is_switched_on_and_the_users_choice_stands(
    switch, caplog
):
    d = switch.disabler
    items = [
        _item("sensor.ctc_old_default", "ctc_ecozenith_pump_hp1_fan", d.INTEGRATION),
        _item("sensor.ctc_by_hand", "ctc_ecozenith_pump_hp1_rps", d.USER),
        _item("sensor.ctc_entry_off", "ctc_ecozenith_pump_hp1_in", d.CONFIG_ENTRY),
        _item("sensor.ctc_on", "ctc_ecozenith_pump_hp1_out"),
    ]
    entities = [
        _entity("ctc_ecozenith_pump_hp1_fan"),
        _entity("ctc_ecozenith_pump_hp1_rps"),
        _entity("ctc_ecozenith_pump_hp1_in"),
        _entity("ctc_ecozenith_pump_hp1_out"),
    ]
    with caplog.at_level(logging.WARNING):
        count, registry = switch(items, "sensor", entities)
    assert count == 1
    assert registry.updates == [("sensor.ctc_old_default", None)]
    assert registry.items["sensor.ctc_by_hand"].disabled_by == d.USER, "användarens val står"
    assert registry.items["sensor.ctc_entry_off"].disabled_by == d.CONFIG_ENTRY
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1, "en rad, inte en per entitet"
    assert "Switched on 1 sensor entities" in warnings[0].getMessage()
    assert "30 seconds" in warnings[0].getMessage(), "omladdningen som följer sägs i förväg"


def test_an_entity_this_version_does_not_add_stays_switched_off(switch):
    """An orphan switched on would only stand in the registry unavailable."""
    d = switch.disabler
    items = [_item("sensor.ctc_gone", "ctc_ecozenith_pump_p30_gone", d.INTEGRATION)]
    count, registry = switch(items, "sensor", [_entity("ctc_ecozenith_pump_hp1_fan")])
    assert count == 0
    assert registry.updates == []


def test_an_entity_whose_default_is_still_off_is_left_off(switch):
    d = switch.disabler
    items = [_item("sensor.ctc_quiet", "ctc_ecozenith_pump_quiet", d.INTEGRATION)]
    count, registry = switch(items, "sensor",
                             [_entity("ctc_ecozenith_pump_quiet", enabled_default=False)])
    assert count == 0
    assert registry.updates == []


def test_each_platform_switches_on_its_own_and_nothing_else(switch):
    d = switch.disabler
    items = [
        _item("number.ctc_room", "ctc_ecozenith_pump_ctl_room_setpoint_1", d.INTEGRATION),
        _item("select.ctc_dhw", "ctc_ecozenith_pump_ctl_dhw_mode", d.INTEGRATION),
    ]
    entities = [_entity("ctc_ecozenith_pump_ctl_room_setpoint_1"),
                _entity("ctc_ecozenith_pump_ctl_dhw_mode")]
    count, registry = switch(items, "number", entities)
    assert count == 1
    assert registry.updates == [("number.ctc_room", None)]
    assert registry.items["select.ctc_dhw"].disabled_by == d.INTEGRATION


def test_another_entrys_entities_are_not_looked_at(switch):
    d = switch.disabler
    items = [_item("sensor.other_pump", "ctc_ecozenith_pump_hp1_fan", d.INTEGRATION,
                   entry_id="other")]
    count, registry = switch(items, "sensor", [_entity("ctc_ecozenith_pump_hp1_fan")])
    assert count == 0
    assert registry.updates == []


def test_the_owners_option_to_switch_off_new_entities_switches_nothing_on(switch, caplog):
    """Home Assistant registers every new entity as switched off by the integration
    while the option is off, so the registry cannot tell it from an old default."""
    d = switch.disabler
    items = [
        _item("sensor.ctc_new", "ctc_ecozenith_pump_hp1_fan", d.INTEGRATION),
        _item("number.ctc_room", "ctc_ecozenith_pump_ctl_room_setpoint_1", d.INTEGRATION),
    ]
    runtime = SimpleNamespace(switched_on=0)
    entry = SimpleNamespace(entry_id="entry", pref_disable_new_entities=True, runtime_data=runtime)
    with caplog.at_level(logging.WARNING):
        count, registry = switch(items, "sensor", [_entity("ctc_ecozenith_pump_hp1_fan")], entry)
    assert count == 0
    assert registry.updates == [], "ägarens systemval står"
    assert runtime.switched_on == 0
    assert not [r for r in caplog.records if "Switched on" in r.getMessage()]


def test_what_is_switched_on_is_counted_on_the_runtime(switch):
    """The catch-up task reads the count to hold the panel until the reload."""
    d = switch.disabler
    runtime = SimpleNamespace(switched_on=0)
    entry = SimpleNamespace(entry_id="entry", pref_disable_new_entities=False, runtime_data=runtime)
    items = [
        _item("sensor.ctc_a", "ctc_ecozenith_pump_hp1_fan", d.INTEGRATION),
        _item("number.ctc_room", "ctc_ecozenith_pump_ctl_room_setpoint_1", d.INTEGRATION),
    ]
    switch(items, "sensor", [_entity("ctc_ecozenith_pump_hp1_fan")], entry)
    assert runtime.switched_on == 1
    switch(items, "number", [_entity("ctc_ecozenith_pump_ctl_room_setpoint_1")], entry)
    assert runtime.switched_on == 2, "plattformarna lägger ihop"
    switch([], "select", [], entry)
    assert runtime.switched_on == 2


def test_nothing_to_switch_on_says_nothing(switch, caplog):
    items = [_item("sensor.ctc_on", "ctc_ecozenith_pump_hp1_out")]
    with caplog.at_level(logging.DEBUG):
        count, registry = switch(items, "sensor", [_entity("ctc_ecozenith_pump_hp1_out")])
    assert count == 0
    assert registry.updates == []
    assert not [r for r in caplog.records if r.name.endswith(".entity")]


# ------------------------------------------------------------------ in code


def _setup_of(platform: str) -> str:
    source = (COMPONENT / f"{platform}.py").read_text(encoding="utf-8")
    return source.split("async def async_setup_entry")[1].split("\nclass ")[0]


@pytest.mark.parametrize("platform", PLATFORMS)
def test_every_platform_switches_on_before_it_adds(platform):
    setup = _setup_of(platform)
    call = f'async_switch_on_new_defaults(hass, entry, "{platform}", '
    assert call in setup, f"{platform} slår inte på det som stängdes av"
    # Before the entities are added, so they come up in this very set-up.
    assert setup.index(call) < setup.index("async_add_entities(entities)")


def test_only_the_sensor_platforms_set_up_batch_goes_through_the_switch():
    """Not the rows that turn up later, nor the identity that fills in later (F4.2).

    Switched on there, each would restart Home Assistant's half minute before
    its reload, at a moment the catch-up task no longer holds the panel for; an
    entity such a batch adds switched off is switched on at the next start.
    """
    setup = _setup_of("sensor")
    assert setup.count("async_switch_on_new_defaults(") == 1
    call = setup.index('async_switch_on_new_defaults(hass, entry, "sensor", entities)')
    assert call < setup.index("async_add_entities(entities)")
    later = setup.split("def add_later(")[1].split("\n\n")[0]
    assert "async_switch_on_new_defaults(" not in later
    assert "async_add_entities(new)" in later
    for listener in ("_add_rows_that_left_a_number", "_identity_filled_in"):
        body = setup.split(f"def {listener}(")[1].split("entry.async_on_unload(")[0]
        assert "add_later(" in body, listener
        assert "async_switch_on_new_defaults(" not in body, listener
    assert setup.count("async_add_entities(") == 2, "set-up och add_later, inget annat"


def test_the_catch_up_task_holds_the_panel_before_it_counts_or_walks_anything():
    """The reload that switching on brings cancels the entry's tasks (F4.2)."""
    source = (COMPONENT / "__init__.py").read_text(encoding="utf-8")
    code = source.split("async def _async_catch_up(")[1].split("\ndef ")[0].split('"""')[2]
    hold = code.index("async with client.panel:")
    assert "switched_on" in code[:hold], "bara efter en uppsättning som slog på något"
    assert "has_display(entry.data)" in code[:hold], "en post utan display har ingen panel"
    assert code.index("asyncio.sleep(SWITCH_ON_HOLD.total_seconds())") > hold
    assert hold < code.index("_async_check_release(")
    assert hold < code.index("_MENU_TRIES[entry.entry_id] =")
    assert hold < code.index("_WALKED.add(")


def test_both_coordinators_are_given_the_entry():
    source = (COMPONENT / "__init__.py").read_text(encoding="utf-8")
    setup = source.split("async def async_setup_entry")[1].split("\nasync def ")[0]
    for name in ("CtcModbusCoordinator(", "CtcWebCoordinator("):
        call = setup.split(name)[1].split("\n    )")[0]
        assert "config_entry=entry" in call, name


def test_a_coordinator_built_without_an_entry_leaves_the_base_class_its_default():
    coordinator = load("coordinator") if ha_stub.install() else None
    if coordinator is None:
        pytest.skip(ha_stub.SKIP_REASON)
    assert coordinator._entry_kwargs(None) == {}
    entry = object()
    assert coordinator._entry_kwargs(entry) == {"config_entry": entry}
