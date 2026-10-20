"""Just enough of Home Assistant for the modules that import it to load.

The coordinator and the entity platforms import Home Assistant at module level,
and the suite runs without it. What they touch at import time is small: a
coordinator base class, its failure exception, the entity base classes and the
device class enum. These stand-ins cover exactly that, and only when Home
Assistant itself is not installed; where it is, the modules load against the
real thing and the tests that build on the stand-ins adapt rather than fail.

The interval timer is recorded instead of started, which is what lets a test
drive the control manager's keepalive by hand. The recorder is a module level
function so that ``record_timers`` can patch it in under a real Home Assistant
too, where the real timer would ask a stand-in ``hass`` for its event loop. The
one thing a real core cannot give a test without a running instance is the
coordinator base class: its constructor asks the frame helper, so the fixtures
that build a coordinator call ``skip_unless_stubbed`` and sit the run out.
"""

from __future__ import annotations

import enum
import sys
import types
from typing import Generic, TypeVar

import pytest

T = TypeVar("T")

#: Every (callback, interval) the control manager asked to have run.
tracked: list[tuple] = []

#: Why a fixture that needs the stand-ins sits out a run under a real core.
SKIP_REASON = (
    "needs the stubbed Home Assistant: a real core's coordinator asks the frame "
    "helper, which only a running instance sets up"
)

#: True once the stand-ins have been put in place in this process.
stubbed = False


def _module(name: str, package: bool = False) -> types.ModuleType:
    module = types.ModuleType(name)
    if package:
        module.__path__ = []
    sys.modules[name] = module
    return module


def async_call_later(hass, delay, action):
    """Nothing is scheduled under the stand-in; the cancel does nothing either."""
    return lambda: None


def async_track_time_interval(hass, action, interval):
    """Record the timer instead of starting it; the undo takes the record away."""
    entry = (action, interval)
    tracked.append(entry)

    def _unsub() -> None:
        if entry in tracked:
            tracked.remove(entry)

    return _unsub


def _name_the_config_entry_type() -> None:
    # The platforms do ``from . import CtcConfigEntry``, which would pull the
    # package's own __init__ and all of Home Assistant with it. The package is
    # a stub here (see conftest), so the name is simply given to it, under a
    # real core as well: the modules still load into the stub package there.
    package = sys.modules.get("ctc_ecozenith")
    if package is not None and not hasattr(package, "CtcConfigEntry"):
        package.CtcConfigEntry = object


def install() -> bool:
    """Put the stand-ins in place, unless Home Assistant is really there.

    Returns True when the stand-ins are what ``homeassistant`` resolves to, now
    or from an earlier call, and False when the real package is importable, so
    a fixture can tell which world it is in.
    """
    global stubbed
    _name_the_config_entry_type()
    if stubbed:
        return True
    if "homeassistant" in sys.modules:
        return False
    try:
        import homeassistant  # noqa: F401

        return False
    except ImportError:
        pass

    stubbed = True
    _module("homeassistant", package=True)
    core = _module("homeassistant.core")
    core.HomeAssistant = object
    core.callback = lambda func: func

    _module("homeassistant.helpers", package=True)
    coordinator = _module("homeassistant.helpers.update_coordinator")

    class DataUpdateCoordinator(Generic[T]):
        def __init__(self, hass, logger, *, name="", update_interval=None, **_kwargs):
            self.hass = hass
            self.logger = logger
            self.name = name
            self.update_interval = update_interval
            self.data = None
            self.last_update_success = True

    class UpdateFailed(Exception):
        pass

    class CoordinatorEntity:
        #: An entity is given its hass when it is added; under the stand-in
        #: nothing adds one.
        hass = None

        def __init__(self, coordinator):
            self.coordinator = coordinator

    coordinator.DataUpdateCoordinator = DataUpdateCoordinator
    coordinator.UpdateFailed = UpdateFailed
    coordinator.CoordinatorEntity = CoordinatorEntity

    event = _module("homeassistant.helpers.event")
    event.async_track_time_interval = async_track_time_interval
    event.async_call_later = async_call_later

    # The identity sensors listen for a dispatcher signal; under the stand-in
    # nothing sends one, so connecting is a no-op that hands back an unsubscribe.
    # The sensor platform tidies the entity registry of rows that vanished;
    # under the stand-in the registry is empty and removing is a no-op.
    registry = _module("homeassistant.helpers.entity_registry")

    class _Registry:
        entities: dict = {}

        def async_remove(self, entity_id):
            return None

    registry.async_get = lambda hass: _Registry()
    registry.async_entries_for_config_entry = lambda reg, entry_id: []

    dispatcher = _module("homeassistant.helpers.dispatcher")
    dispatcher.async_dispatcher_connect = lambda hass, signal, target: (lambda: None)
    dispatcher.async_dispatcher_send = lambda hass, signal, *args: None

    platform = _module("homeassistant.helpers.entity_platform")
    platform.AddEntitiesCallback = object

    _module("homeassistant.components", package=True)
    binary_sensor = _module("homeassistant.components.binary_sensor")

    class BinarySensorDeviceClass(enum.Enum):
        RUNNING = "running"
        PROBLEM = "problem"

    class BinarySensorEntity:
        pass

    binary_sensor.BinarySensorDeviceClass = BinarySensorDeviceClass
    binary_sensor.BinarySensorEntity = BinarySensorEntity

    number = _module("homeassistant.components.number")

    class NumberMode(enum.Enum):
        BOX = "box"
        SLIDER = "slider"

    class NumberEntity:
        pass

    number.NumberMode = NumberMode
    number.NumberEntity = NumberEntity

    select = _module("homeassistant.components.select")

    class SelectEntity:
        pass

    select.SelectEntity = SelectEntity

    # The debug report's switch (switch.py) reads its entry and writes it; the
    # entity's own machinery is what a test gives the instance, in both worlds.
    switch = _module("homeassistant.components.switch")

    class SwitchEntity:
        async def async_added_to_hass(self) -> None:
            return None

    switch.SwitchEntity = SwitchEntity

    sensor = _module("homeassistant.components.sensor")

    class SensorDeviceClass(enum.Enum):
        TEMPERATURE = "temperature"
        POWER = "power"
        ENERGY = "energy"
        CURRENT = "current"
        VOLTAGE = "voltage"
        PRESSURE = "pressure"
        DURATION = "duration"
        ENUM = "enum"
        TIMESTAMP = "timestamp"

    class SensorStateClass(enum.Enum):
        MEASUREMENT = "measurement"
        TOTAL = "total"
        TOTAL_INCREASING = "total_increasing"

    class SensorEntity:
        pass

    sensor.SensorDeviceClass = SensorDeviceClass
    sensor.SensorStateClass = SensorStateClass
    sensor.SensorEntity = SensorEntity

    const = _module("homeassistant.const")

    class EntityCategory(enum.Enum):
        CONFIG = "config"
        DIAGNOSTIC = "diagnostic"

    class UnitOfPower(enum.Enum):
        KILO_WATT = "kW"

    class UnitOfTemperature(enum.Enum):
        CELSIUS = "°C"

    class UnitOfTime(enum.Enum):
        HOURS = "h"
        MINUTES = "min"

    class UnitOfElectricCurrent(enum.Enum):
        AMPERE = "A"

    class UnitOfElectricPotential(enum.Enum):
        VOLT = "V"

    class UnitOfEnergy(enum.Enum):
        KILO_WATT_HOUR = "kWh"

    class UnitOfPressure(enum.Enum):
        BAR = "bar"

    class UnitOfVolumeFlowRate(enum.Enum):
        LITERS_PER_MINUTE = "L/min"

    const.EntityCategory = EntityCategory
    const.UnitOfPower = UnitOfPower
    const.UnitOfTemperature = UnitOfTemperature
    const.UnitOfTime = UnitOfTime
    const.UnitOfElectricCurrent = UnitOfElectricCurrent
    const.UnitOfElectricPotential = UnitOfElectricPotential
    const.UnitOfEnergy = UnitOfEnergy
    const.UnitOfPressure = UnitOfPressure
    const.UnitOfVolumeFlowRate = UnitOfVolumeFlowRate
    return True


def skip_unless_stubbed() -> None:
    """Install the stand-ins, or skip the test when a real core is in the way."""
    if not install():
        pytest.skip(SKIP_REASON)


def record_timers(monkeypatch: pytest.MonkeyPatch) -> None:
    """Have the control manager's timer recorded in ``tracked`` for this test.

    The manager imports ``async_track_time_interval`` from the event helper at
    each call, so patching the module attribute is enough whichever module it
    is: the stand-in, where this is already the function, or the real one,
    whose timer would want ``hass.loop``. The record starts empty.
    """
    install()
    import homeassistant.helpers.event as event

    monkeypatch.setattr(event, "async_track_time_interval", async_track_time_interval)
    tracked.clear()
