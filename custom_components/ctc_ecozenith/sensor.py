"""Sensors from both transports."""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.const import (
    EntityCategory,
    UnitOfElectricCurrent,
    UnitOfElectricPotential,
    UnitOfEnergy,
    UnitOfPower,
    UnitOfPressure,
    UnitOfTemperature,
    UnitOfTime,
    UnitOfVolumeFlowRate,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import CtcConfigEntry
from .attribute_names import override_in_english, with_english
from .catalogue import display_state_class, pages_from_storage
from .const import (
    CONF_MENU,
    STATUS_UNKNOWN,
    ModbusSensor,
    SlowValue,
    device_class_for,
    identity_signal,
)
from .cop import (
    POWER_FIRST_READ,
    POWER_SILENT,
    SHARE_SPANS,
    WINDOWS,
    compressor_hours,
    counter_rows,
    current_totals,
    display_silence,
    modbus_consumption_answered,
    power_now,
    reason_for,
    share_for,
    stale_rows,
    totals_source,
)
from .cop_estimate import (
    BASIS_CARNOT,
    CARNOT_UNCERTAINTY,
    ESTIMATE_SPANS,
    METHOD,
    METHOD_CARNOT,
    estimate_for,
)
from .entity import async_switch_on_new_defaults
from .explanations import ESTIMATE_REASONS
from .keys import prefix_of, previous_keys, unique_id
from .overrides import held
from .rows import due_rows, split_rows, vanished_display_keys
from .transitions import (
    MEAN_RUN_KEY,
    MINUTES_24H_KEY,
    TRANSITION_SENSORS,
    TransitionSensor,
    mean_run_minutes,
)

#: What the unit says about itself, one sensor each: the entity key, which is
#: its translation key, the Swedish name that translations/sv.json carries,
#: where on the identity the value is read, and the icon.
IDENTITY_ROWS = (
    ("hp_model", "Värmepumpsmodell", lambda i: i.heatpump_model, "mdi:heat-pump-outline"),
    ("display_fw", "Programversion display", lambda i: i.display_firmware, "mdi:chip"),
    ("hp_fw", "Programversion VP-styrkort", lambda i: i.heatpump_firmware, "mdi:chip"),
    ("bootloader", "Bootloaderversion", lambda i: i.bootloader, "mdi:chip"),
    ("serial", "Serienummer", lambda i: i.serial, "mdi:identifier"),
    ("made", "Tillverkad", lambda i: i.manufactured, "mdi:factory"),
)

_LOGGER = logging.getLogger(__name__)

# By the names const.py uses, so the choice of class can be tested without
# Home Assistant installed.
DEVICE_CLASSES = {
    "temperature": SensorDeviceClass.TEMPERATURE,
    "power": SensorDeviceClass.POWER,
    "energy": SensorDeviceClass.ENERGY,
    "current": SensorDeviceClass.CURRENT,
    "voltage": SensorDeviceClass.VOLTAGE,
    "pressure": SensorDeviceClass.PRESSURE,
    "duration": SensorDeviceClass.DURATION,
}

UNITS = {
    "°C": UnitOfTemperature.CELSIUS,
    "kW": UnitOfPower.KILO_WATT,
    "kWh": UnitOfEnergy.KILO_WATT_HOUR,
    "A": UnitOfElectricCurrent.AMPERE,
    "V": UnitOfElectricPotential.VOLT,
    "bar": UnitOfPressure.BAR,
    "h": UnitOfTime.HOURS,
    "min": UnitOfTime.MINUTES,
    "l/min": UnitOfVolumeFlowRate.LITERS_PER_MINUTE,
    "%": "%",
    "rps": "rps",
    "ppm": "ppm",
}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: CtcConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Create every sensor for this heat pump."""
    runtime = entry.runtime_data

    def add_later(new: list[SensorEntity]) -> None:
        # What turns up after set-up is added as the registry has it. An entity
        # an earlier version created switched off is switched on at the next
        # start instead (entity.py): switched on now, it would bring Home
        # Assistant's reload at a moment nothing here holds the panel for.
        async_add_entities(new)

    entities: list[SensorEntity] = [
        CtcModbusSensor(runtime, description)
        for description in runtime.modbus.descriptions
    ]
    if runtime.web is not None:
        _async_remove_vanished_rows(hass, entry, runtime)
        # A row that reads CTC's marker for a sensor that is not fitted, and
        # never has read as a number, waits for its entity until it does; see
        # rows.py. The first harvest that gives it a number adds it here.
        numeric = runtime.seen.numeric if runtime.seen is not None else set()
        now, pending = split_rows(runtime.pages, runtime.web.data, numeric)
        entities.extend(CtcDisplaySensor(runtime, page.title, value) for page, value in now)
        if pending:
            web = runtime.web
            _LOGGER.info(
                "%d display rows have never read as a number, so their entities wait "
                "until they do: %s",
                len(pending),
                ", ".join(sorted(pending)),
            )

            @callback
            def _add_rows_that_left_a_number() -> None:
                due = due_rows(pending, web.data)
                if not due:
                    return
                new = [pending.pop(key) for key in due]
                _LOGGER.info(
                    "Display rows that read as a number for the first time get their "
                    "entities: %s",
                    ", ".join(due),
                )
                add_later([CtcDisplaySensor(runtime, page.title, value) for page, value in new])

            entry.async_on_unload(web.async_add_listener(_add_rows_that_left_a_number))
        entities.append(CtcHarvestSensor(runtime))

    # What the unit is: static once read, but read late where the system
    # information page had never been shown on the panel. A row gets its
    # sensor when its value is there, at set-up or when the background walk
    # fills the identity in afterwards; a row that has no value yet has no
    # sensor, as before.
    signal = identity_signal(entry.entry_id)
    offered: set[str] = set()

    def identity_sensors() -> list[SensorEntity]:
        new: list[SensorEntity] = []
        for key, _name, read, icon in IDENTITY_ROWS:
            if key in offered or not read(runtime.identity):
                continue
            offered.add(key)
            new.append(CtcIdentitySensor(runtime, key, read, icon, signal))
        return new

    entities.extend(identity_sensors())

    if runtime.cop is not None:
        for span in ("day", "week", "month", "year", "first_year", "lifetime"):
            entities.append(CtcCopSensor(runtime, span))
        # The immersion heater's share of the electricity over the same spans
        # but the first year (R28), from the same samples.
        entities.extend(CtcShareSensor(runtime, span) for span in SHARE_SPANS)
    # The estimate from the display's two powers (cop_estimate.py), beside the
    # counters' figures and never in place of them. Wherever the display is
    # harvested: the reason it gives is what says which power is missing.
    if runtime.web is not None and getattr(runtime, "heat_estimate", None) is not None:
        entities.extend(CtcCopEstimateSensor(runtime, span) for span in ESTIMATE_SPANS)

    # The sums the integration makes of the two power rows, where the machine's
    # own counters never move (integrate.py). Shown whether or not they are the
    # ones a figure rests on: that is how an owner sees what the machine gives.
    if getattr(runtime, "integrator", None) is not None:
        entities.extend(CtcIntegratedEnergySensor(runtime, side) for side in INTEGRATED_SIDES)

    # What the status codes said from one poll to the next: starts, the last
    # run, and where the history page is harvested the mean run over a day.
    if runtime.transitions is not None:
        entities.extend(CtcTransitionSensor(runtime, item) for item in TRANSITION_SENSORS)
        if runtime.web is not None and runtime.starts_per_day is not None:
            entities.append(CtcMeanRunSensor(runtime))
    if runtime.alarms is not None:
        entities.append(CtcLastAlarmSensor(runtime))
    # What Home Assistant holds in the controller right now (R49), wherever
    # it may hold anything, like the button that lets go of it all. Read with
    # a default, so a runtime from before the sensor existed still sets up.
    if getattr(runtime, "control_enabled", False):
        entities.append(CtcActiveOverridesSensor(runtime))

    # At set-up, and only here: an entity an earlier version created switched
    # off is switched on before it is added, so it comes up in this set-up.
    async_switch_on_new_defaults(hass, entry, "sensor", entities)
    async_add_entities(entities)

    @callback
    def _identity_filled_in() -> None:
        if new := identity_sensors():
            add_later(new)

    entry.async_on_unload(async_dispatcher_connect(hass, signal, _identity_filled_in))


def _async_remove_vanished_rows(hass: HomeAssistant, entry: CtcConfigEntry, runtime) -> None:
    """Take the registry entries away for rows no longer on a page the menu still has.

    The rule is rows.vanished_display_keys: a page the menu does not know, one
    the sweep did not reach, keeps every entry it has, and a row that is still
    on its page is never removed however long it has read the marker. What
    goes is a row the parser folded away or renamed, the i255's two halves of
    the clock row for instance, which otherwise sit in the registry
    unavailable for good. One line in the log per entry, so the owner can see
    what went and why.
    """
    from homeassistant.helpers import entity_registry as er

    menu = pages_from_storage(entry.options.get(CONF_MENU)) + list(runtime.pages)
    prefix = prefix_of(runtime.device)
    registry = er.async_get(hass)
    entries = {
        item.unique_id[len(prefix):]: item
        for item in er.async_entries_for_config_entry(registry, entry.entry_id)
        if item.domain == "sensor" and item.unique_id and item.unique_id.startswith(prefix)
    }
    # A row that has moved to the key of its place (L2) was carried over by
    # set-up before this ran; one that could not be is left, not removed.
    for key in sorted(vanished_display_keys(menu, entries, moved=previous_keys(menu))):
        item = entries[key]
        _LOGGER.info(
            "The display row %s is no longer on its page in the display's menu, so its "
            "entity %s is removed from the registry",
            key,
            item.entity_id,
        )
        registry.async_remove(item.entity_id)


class CtcModbusSensor(CoordinatorEntity, SensorEntity):
    """One documented Modbus register."""

    _attr_has_entity_name = True

    def __init__(self, runtime, description: ModbusSensor) -> None:
        super().__init__(runtime.modbus)
        self._description = description
        self._attr_unique_id = unique_id(runtime.device, description.key)
        # Named in Home Assistant's language from the translations under the
        # register's key, Swedish (the table's own name, word for word) or
        # English (roadmap R23). An enum register's state stays the table's
        # Swedish label in every language: automations and the history rest
        # on it.
        self._attr_translation_key = description.key
        self._attr_device_info = runtime.device
        self._attr_entity_registry_enabled_default = description.enabled_default
        if description.icon:
            self._attr_icon = description.icon
        if description.diagnostic:
            self._attr_entity_category = EntityCategory.DIAGNOSTIC
        if description.enum is None:
            self._attr_native_unit_of_measurement = UNITS.get(
                description.unit or "", description.unit
            )
            self._attr_device_class = DEVICE_CLASSES.get(
                device_class_for(description.unit, description.device_class) or ""
            )
            if description.state_class == "total_increasing":
                self._attr_state_class = SensorStateClass.TOTAL_INCREASING
            elif description.state_class == "measurement":
                self._attr_state_class = SensorStateClass.MEASUREMENT
        else:
            self._attr_device_class = SensorDeviceClass.ENUM
            # The reading for a code with no label is an option like any other,
            # because Home Assistant rejects a state that is not among them.
            self._attr_options = [*description.enum.values(), STATUS_UNKNOWN]

    @property
    def native_value(self):
        return (self.coordinator.data or {}).get(self._description.key)

    @property
    def available(self) -> bool:
        return (
            self.coordinator.last_update_success
            and self._description.key in (self.coordinator.data or {})
        )

    @property
    def extra_state_attributes(self) -> dict[str, int] | None:
        """The code behind a reading the table has no label for, and nothing else.

        Without it the number the controller actually answered with would be lost
        to the log, and that number is what a label is eventually written from.
        Under ``kod`` and its English twin ``code`` (attribute_names.py).
        """
        code = getattr(self.coordinator, "unknown_codes", {}).get(self._description.key)
        return with_english({"kod": code}) if code is not None else None


class CtcDisplaySensor(CoordinatorEntity, SensorEntity):
    """A reading harvested from the display's own web interface.

    These come from a page the integration has to navigate to, so they update on
    the slow interval rather than continuously. Named by the panel itself, its
    page title and its row, in the language the menu is read in, which is
    Swedish whatever Home Assistant speaks: the integration has no word of
    its own for a row a display shows, so it has no translation either.
    """

    _attr_has_entity_name = True

    def __init__(self, runtime, page_title: str, value: SlowValue) -> None:
        super().__init__(runtime.web)
        self._value = value
        self._attr_unique_id = unique_id(runtime.device, value.key)
        self._attr_name = f"{page_title}: {value.label}" if page_title else value.label
        self._attr_device_info = runtime.device
        unit = value.unit
        self._attr_native_unit_of_measurement = UNITS.get(unit or "", unit)
        device_class = DEVICE_CLASSES.get(device_class_for(unit) or "")
        if device_class is not None:
            self._attr_device_class = device_class
        state_class = display_state_class(unit, value.label)
        if state_class == "total_increasing":
            self._attr_state_class = SensorStateClass.TOTAL_INCREASING
        elif state_class == "measurement":
            self._attr_state_class = SensorStateClass.MEASUREMENT

    @property
    def native_value(self):
        return (self.coordinator.data or {}).get(self._value.key)

    @property
    def available(self) -> bool:
        """Available while the reading is fresh, page by page.

        The data carries a value across a page that could not be reached, so
        being in the data is not enough: the value has to have been read off
        the panel recently, within as many intervals as a whole harvest may
        fail in a row. A page that has stopped answering thus goes unavailable
        after the same patience as a display that has, instead of showing
        yesterday's delivered heat as today's for days.
        """
        return self.coordinator.last_update_success and self.coordinator.is_fresh(
            self._value.key
        )

    @property
    def extra_state_attributes(self) -> dict[str, str]:
        attributes = {
            "källa": "displayens webbgränssnitt",
            "sida": str(self._value.page),
            "skärm": str(self._value.screen),
        }
        # When the value was actually read, so a reading carried from an
        # earlier harvest is seen for what it is.
        read_at = self.coordinator.last_read(self._value.key)
        if read_at is not None:
            attributes["senast läst"] = read_at.isoformat(timespec="seconds")
        # Each beside its English twin: source, page, screen, read_at.
        return with_english(attributes)


class CtcHarvestSensor(CoordinatorEntity, SensorEntity):
    """When the display was last read, and how the reading of it is going.

    The harvest walks the physical panel, so it runs rarely, gives way to
    whoever stands at the panel and keeps trying quietly when the display is
    slow. All of that was in the log and nowhere else: a value twenty minutes
    old looked like one two days old, and a panel somebody had left on the
    wrong page skipped every harvest without a word on the device page. This
    is the device's own account of it: the moment of the last harvest that
    read a page, and in the attributes how many harvests in a row were skipped
    or failed, when the next attempt is, and why the last one gave nothing.

    Available whatever the display does: a harvest that fails is exactly what
    it is there to show, and an unavailable entity shows no attributes.
    """

    _attr_has_entity_name = True
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_icon = "mdi:clock-check-outline"
    _attr_translation_key = "display_harvest"

    def __init__(self, runtime) -> None:
        super().__init__(runtime.web)
        self._attr_unique_id = unique_id(runtime.device, "display_harvest")
        self._attr_device_info = runtime.device

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        # A live read that reads nothing leaves the data, and so the
        # coordinator's listeners, alone; the attributes below still change.
        add = getattr(self.coordinator, "async_add_live_status_listener", None)
        if add is not None:
            self.async_on_remove(add(self.async_write_ha_state))

    @property
    def native_value(self):
        return getattr(self.coordinator, "last_harvest", None)

    @property
    def available(self) -> bool:
        return True

    @property
    def extra_state_attributes(self) -> dict[str, object]:
        """How the harvesting is going, in words that stand on their own.

        The counters are read with defaults: the skip rule and its tally live
        on the coordinator and have grown in steps, and the account here must
        not break because one of them is not there yet.
        """
        coordinator = self.coordinator
        patience = getattr(coordinator, "patience", None)
        next_attempt = getattr(coordinator, "next_attempt", None)
        reason = getattr(coordinator, "last_skip_reason", None) or getattr(
            coordinator, "last_failure", None
        )
        attributes: dict[str, object] = {
            "hoppade över i rad": int(getattr(coordinator, "skips_in_a_row", 0) or 0),
            "misslyckade i rad": int(getattr(patience, "failures", 0) or 0),
            "nästa försök": (
                next_attempt.isoformat(timespec="seconds") if next_attempt is not None else None
            ),
            "senaste skäl": reason,
            "sidor lästa": list(getattr(coordinator, "pages_read", []) or []),
            "sidor missade": list(getattr(coordinator, "pages_missed", []) or []),
        }
        live_page = getattr(coordinator, "live_page", None)
        if live_page is not None:
            # The live page's own account beside the walk's: when it was last
            # read off the panel without a press, which page the panel showed
            # at the last try and why a try read nothing, so the device page
            # says whether the panel rests on the page and the reads go
            # through, and the owner can set that beside what the panel's
            # backlight does.
            live_read = getattr(coordinator, "live_read_at", None)
            skipped = int(getattr(coordinator, "live_skips", 0) or 0)
            attributes.update(
                {
                    "direktläst sida": int(live_page),
                    "direktläsning senast": (
                        live_read.isoformat(timespec="seconds") if live_read is not None else None
                    ),
                    "direktläsning panelen visade": getattr(coordinator, "live_panel_page", None),
                    "direktläsning uteblivna i rad": skipped,
                    "direktläsning skäl": getattr(coordinator, "live_reason", None),
                }
            )
        return attributes


class CtcLastAlarmSensor(CoordinatorEntity, SensorEntity):
    """The alarm the display showed last, with its E-code, as the panel prints it.

    Read off the pages the harvest visits anyway, so it updates on the slow
    interval and never costs the panel a step. Empty until the panel has
    shown an alarm; afterwards it keeps the latest one, and the attributes say
    whether it is still showing.
    """

    _attr_has_entity_name = True
    _attr_icon = "mdi:alert-circle-outline"
    _attr_translation_key = "last_alarm"

    def __init__(self, runtime) -> None:
        super().__init__(runtime.web)
        self._runtime = runtime
        self._attr_unique_id = unique_id(runtime.device, "last_alarm")
        self._attr_device_info = runtime.device

    @property
    def native_value(self) -> str | None:
        latest = self._runtime.alarms.latest
        return latest["shown"] if latest else None

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        latest = self._runtime.alarms.latest
        if latest is None:
            return None
        return with_english(
            {
                "kod": latest["code"],
                "text": latest["text"],
                "start": latest["start"],
                "slut": latest["end"],
                "utetemperatur vid start": latest["outdoor"],
                "pågår": "ja" if latest["end"] is None else "nej",
            }
        )

    @property
    def available(self) -> bool:
        # Available while empty and while the display is quiet, like the
        # harvest sensor and the stored spans of the coefficient of
        # performance: the state and the attributes come out of the alarm
        # log's own store, not out of the harvest's data, and are as true
        # after three missed harvests as before them. Going unavailable then
        # would show an automation a change of state that no alarm made, and
        # take the code, the start and the outdoor temperature out of the
        # history for as long as the display stays quiet. That the display is
        # quiet is already said by Senaste displayskörd and by the reason
        # under the coefficient of performance.
        return True


class CtcActiveOverridesSensor(CoordinatorEntity, SensorEntity):
    """How many control registers Home Assistant holds in the controller right now (R49).

    The keepalive writes each held register every minute, and the controller
    lets go of one five minutes after the last write that reached it, so a
    keepalive that stops lets go of everything. This says on the overview and
    in the history what is being held: the count as the state, and under
    ``styrningar`` and its English twin ``overrides`` each register's name,
    number, value and when that value was set, ``satt`` and ``set_at``.

    Written whenever the control manager changes what it holds, a write, a
    release or an override given up, and with every Modbus round besides.
    The keepalive's rewrites of the same value change nothing here: the list
    carries when the value was set or last changed, not when a write last
    reached the controller, so a value held for hours is one state in the
    recorder rather than a new row every minute (F6.2). Available whatever
    Modbus does: holding is Home Assistant's own state, and a lost round is
    exactly when it is worth reading, with the button beside it on the page.
    """

    _attr_has_entity_name = True
    _attr_icon = "mdi:hand-back-left-outline"
    _attr_translation_key = "active_overrides"

    def __init__(self, runtime) -> None:
        super().__init__(runtime.modbus)
        self._runtime = runtime
        self._attr_unique_id = unique_id(runtime.device, "active_overrides")
        self._attr_device_info = runtime.device

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.async_on_remove(
            self._runtime.control.async_add_listener(self.async_write_ha_state)
        )

    @property
    def native_value(self) -> int:
        return len(self._runtime.control.active)

    @property
    def available(self) -> bool:
        return True

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        shown = held(self._runtime.control)
        # The English twin with English keys inside, as the alarm episodes have.
        return with_english(
            {"styrningar": shown, "overrides": [override_in_english(item) for item in shown]}
        )


class CtcIdentitySensor(SensorEntity):
    """Something the unit says about itself and then never changes.

    Read from the display and stored with the entry, so it costs nothing to
    keep and survives the display being unreachable. Read live off the
    runtime's identity, and written again on the identity signal: a serial
    number or a version that the background walk finds after set-up shows up
    here without a reload of the entry.
    """

    _attr_has_entity_name = True
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_should_poll = False

    def __init__(self, runtime, key: str, read, icon: str, signal: str) -> None:
        self._attr_unique_id = unique_id(runtime.device, key)
        self._attr_translation_key = key
        self._attr_icon = icon
        self._attr_device_info = runtime.device
        self._runtime = runtime
        self._read = read
        self._signal = signal

    @property
    def native_value(self):
        return self._read(self._runtime.identity)

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(
            async_dispatcher_connect(self.hass, self._signal, self.async_write_ha_state)
        )


class CtcTransitionSensor(CoordinatorEntity, SensorEntity):
    """Something the heat pump's transitions tell: a start, a count, a run.

    Read off the watch in transitions.py after every Modbus round. The watch is
    fed before the platforms are set up, so what the sensor reads here is the
    round the coordinator has just finished.
    """

    _attr_has_entity_name = True

    def __init__(self, runtime, item: TransitionSensor) -> None:
        super().__init__(runtime.modbus)
        self._watch = runtime.transitions
        self._item = item
        self._attr_unique_id = unique_id(runtime.device, item.key)
        self._attr_translation_key = item.key
        self._attr_device_info = runtime.device
        self._attr_icon = item.icon
        if item.kind == "timestamp":
            self._attr_device_class = SensorDeviceClass.TIMESTAMP
        elif item.kind == "minutes":
            self._attr_native_unit_of_measurement = UnitOfTime.MINUTES
            self._attr_device_class = SensorDeviceClass.DURATION
            self._attr_state_class = SensorStateClass.MEASUREMENT
        else:
            # A count since midnight: a total with a reset, so the long term
            # statistics sum it over the day rather than average it.
            self._attr_state_class = SensorStateClass.TOTAL

    @property
    def native_value(self):
        return self._item.value(self._watch)

    @property
    def last_reset(self):
        if self._item.kind != "count":
            return None
        return self._watch.counting_since

    @property
    def extra_state_attributes(self) -> dict[str, object]:
        return self._item.attributes(self._watch)


class CtcMeanRunSensor(CoordinatorEntity, SensorEntity):
    """The day's compressor minutes divided by the display's starts per day.

    Modbus 62234 is fresh every round; the display's "Antal starter /24 h" is
    harvested on the slow interval, so the figure can lag by up to that. Both
    numbers stand as attributes, with the moment the starts were read. Only
    where the history page is harvested, and only while that row is fresh by
    the coordinator's rule (R5): the data carries the row across a page that
    is not reached and seeds it from the store at a restart, so without the
    rule today's minutes would be divided by a count from days ago and shown
    as the day's figure. The row's own sensor goes unavailable at that point,
    and this figure goes empty with it.
    """

    _attr_has_entity_name = True
    _attr_icon = "mdi:av-timer"
    _attr_native_unit_of_measurement = UnitOfTime.MINUTES
    _attr_device_class = SensorDeviceClass.DURATION
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, runtime) -> None:
        super().__init__(runtime.modbus)
        self._runtime = runtime
        self._attr_unique_id = unique_id(runtime.device, MEAN_RUN_KEY)
        self._attr_translation_key = MEAN_RUN_KEY
        self._attr_device_info = runtime.device

    def _parts(self) -> tuple[object, object]:
        minutes = (self.coordinator.data or {}).get(MINUTES_24H_KEY)
        web = self._runtime.web
        key = self._runtime.starts_per_day.key
        starts = None
        if web is not None and web.last_update_success and web.is_fresh(key):
            starts = (web.data or {}).get(key)
        return minutes, starts

    @property
    def native_value(self) -> float | None:
        return mean_run_minutes(*self._parts())

    @property
    def extra_state_attributes(self) -> dict[str, object]:
        minutes, starts = self._parts()
        web = self._runtime.web
        read_at = web.last_read(self._runtime.starts_per_day.key) if web is not None else None
        return {
            "kompressordrift senaste dygnet": minutes,
            "antal starter /24 h": starts,
            "antal starter senast läst": (
                read_at.isoformat(timespec="seconds") if read_at is not None else None
            ),
        }


#: The two sums the integration makes of the display's powers, each named in the
#: translations under the key integrated_<side>. A table rather than a literal in
#: the loop, so the guard over the names can read the keys (test_entity_names).
INTEGRATED_SIDES = ("out", "in")


class CtcIntegratedEnergySensor(CoordinatorEntity, SensorEntity):
    """One of the two sums the integration adds up itself from the display's powers.

    Rough on purpose, and said so in the attributes: a power read every half hour
    cannot know what the compressor did in between. The pair is still worth
    dividing, because both sides are read in the same instant and the error
    largely cancels in a quotient. See integrate.py.
    """

    _attr_has_entity_name = True
    _attr_device_class = SensorDeviceClass.ENERGY
    _attr_native_unit_of_measurement = "kWh"
    _attr_state_class = SensorStateClass.TOTAL_INCREASING

    def __init__(self, runtime, side: str) -> None:
        super().__init__(runtime.web)
        self._runtime = runtime
        self._side = side
        self._attr_unique_id = unique_id(runtime.device, f"integrated_{side}")
        self._attr_translation_key = f"integrated_{side}"
        self._attr_device_info = runtime.device

    @property
    def native_value(self) -> float | None:
        totals = self._runtime.integrator.totals
        return totals[0] if self._side == "out" else totals[1]

    @property
    def extra_state_attributes(self) -> dict[str, object]:
        row = self._runtime.power_out if self._side == "out" else self._runtime.power_in
        attributes: dict[str, object] = {
            "källa": "egen summering av displayens momentana effekt",
            "rad": getattr(row, "label", None),
            "timmar i underlaget": self._runtime.integrator.hours,
        }
        if self.native_value is None:
            # An empty sum says which of the two it is, as the figures do.
            attributes["skäl"] = (
                POWER_SILENT if power_now(self._runtime) is None else POWER_FIRST_READ
            )
        return attributes


class CtcCopSensor(CoordinatorEntity, SensorEntity):
    """Delivered heat against supplied energy.

    Modbus knows what went in but never what came out, so this exists only
    where the display's history page is harvested.
    """

    _attr_has_entity_name = True
    _attr_icon = "mdi:gauge"
    _attr_suggested_display_precision = 2
    # A figure to be looked at over a month, which needs Home Assistant to keep
    # long term statistics for it. Without this the page's graph of the daily
    # coefficient of performance has nothing to draw.
    _attr_state_class = SensorStateClass.MEASUREMENT

    #: The Swedish name of each span, which translations/sv.json carries word
    #: for word under the translation key cop_<span>.
    NAMES = {
        "day": "Dygnsvärmefaktor",
        "week": "Veckovärmefaktor",
        "month": "Månadsvärmefaktor",
        "year": "Årsvärmefaktor",
        "first_year": "Värmefaktor, första året",
        "lifetime": "Värmefaktor, hela livslängden",
    }

    def __init__(self, runtime, span: str) -> None:
        super().__init__(runtime.web)
        self._runtime = runtime
        self._span = span
        self._attr_unique_id = unique_id(runtime.device, f"cop_{span}")
        self._attr_translation_key = f"cop_{span}"
        self._attr_device_info = runtime.device

    def _result(self):
        """The tracker's answer for this span, and nothing but this span."""
        tracker = self._runtime.cop
        out, consumed = current_totals(self._runtime)
        if self._span == "day":
            return tracker.result_day(out, consumed)
        if self._span == "first_year":
            return tracker.result_first_year()
        if self._span == "lifetime":
            return tracker.result_lifetime(out, consumed)
        if self._span in WINDOWS:
            return tracker.result_window(out, consumed, self._span)
        return tracker.result_year(out, consumed)

    def _silence(self) -> str | None:
        """What a quiet display does to this figure: the notice, or None.

        The first year is stored once and for all, so a display that has gone
        quiet cannot age it. Every other span is a difference against counters
        that are no longer being read, and shows nothing but the notice. The
        counters' own rows go in too: since R5 a page that is not reached is
        no failure of the harvest, so the display answers while the counters
        age, and the notice has to come from the rows themselves.
        """
        if self._span == "first_year":
            return None
        return display_silence(self._runtime.web, *counter_rows(self._runtime))

    @property
    def native_value(self) -> float | None:
        if self._silence() is not None:
            return None
        result = self._result()
        # Reporting one span's figure under another span's name would be a
        # different number wearing the wrong label.
        return result.value if result.basis == self._span else None

    @property
    def extra_state_attributes(self) -> dict[str, object]:
        result = self._result()
        attributes = result.as_attributes()
        out_source, in_source = totals_source(self._runtime)
        attributes["avgiven värme ur"] = out_source
        attributes["tillförd energi ur"] = in_source
        # An empty figure that says nothing is the thing people ask about.
        out, consumed = current_totals(self._runtime)
        reason = reason_for(
            result,
            out,
            consumed,
            # The compressor's hours, not the unit's: see cop.counter_fault.
            compressor_hours(self._runtime),
            # Only the Modbus route can be waiting on a register; the
            # display's own counter is a row that was recognised.
            modbus_answered=self._runtime.energy_in is not None
            or modbus_consumption_answered(self._runtime.modbus.answered),
            display=self._silence(),
        )
        if reason:
            attributes["skäl"] = reason
        # underlag and skäl beside their English twins, basis and reason.
        return with_english(attributes)

    @property
    def available(self) -> bool:
        # Available without a figure, on purpose: an unavailable entity shows no
        # attributes, and the attributes are where the reason for the empty
        # figure is written. The day is the exception: it is a difference
        # against the counters as they stand now, so a display that has gone
        # quiet takes it with it, as it takes the display's own sensors, and
        # so does a counter row that was read once and has gone stale since,
        # which is when that row's own sensor goes unavailable (R5). A row
        # that has never been read leaves the day standing, with the reason
        # that the counters have not been read. The other spans rest on
        # stored samples and say that the display is quiet.
        if self._span == "day":
            return self.coordinator.last_update_success and not stale_rows(
                self.coordinator, counter_rows(self._runtime)
            )
        return True


class CtcCopEstimateSensor(CoordinatorEntity, SensorEntity):
    """The coefficient of performance estimated from the display's two powers (cop_estimate.py).

    Best effort, and marked as one: the figure is a quotient of two integrated
    powers, not of the heat pump's counters, and it has no figure at all where
    the heat is zero all the time or where the quotient is outside the bounds,
    which the attribute reason says. The single reading, span "now", integrates
    nothing and is noisy. Nothing here is written to the heat pump.
    """

    _attr_has_entity_name = True
    _attr_icon = "mdi:gauge"
    _attr_suggested_display_precision = 2
    _attr_state_class = SensorStateClass.MEASUREMENT

    #: The Swedish name of each span, which translations/sv.json carries word for
    #: word under the translation key cop_estimate_<span>.
    NAMES = {
        "day": "Uppskattad dygnsvärmefaktor",
        "lifetime": "Uppskattad värmefaktor, hela livslängden",
        "now": "Uppskattad värmefaktor, just nu",
    }

    def __init__(self, runtime, span: str) -> None:
        super().__init__(runtime.web)
        self._runtime = runtime
        self._span = span
        self._attr_unique_id = unique_id(runtime.device, f"cop_estimate_{span}")
        self._attr_translation_key = f"cop_estimate_{span}"
        self._attr_device_info = runtime.device

    @property
    def native_value(self) -> float | None:
        return estimate_for(self._runtime, self._span).value

    @property
    def extra_state_attributes(self) -> dict[str, object]:
        estimate = estimate_for(self._runtime, self._span)
        carnot = estimate.basis == BASIS_CARNOT
        attributes: dict[str, object] = {
            "method": METHOD_CARNOT if carnot else METHOD,
            "heat_hours": estimate.heat_hours,
            "electric_hours": estimate.electric_hours,
        }
        if carnot:
            # Worked out from the refrigerant circuit, calibrated on another model.
            attributes["uncertainty"] = CARNOT_UNCERTAINTY
        if self._span == "now" and estimate.read_at is not None:
            attributes["read_at"] = estimate.read_at.isoformat(timespec="seconds")
        if estimate.reason is not None:
            # The reason in both languages: skäl in Swedish, reason in English.
            text = ESTIMATE_REASONS[estimate.reason]
            attributes["skäl"] = str(text)
            attributes["reason"] = text.en
        return attributes


class CtcShareSensor(CoordinatorEntity, SensorEntity):
    """The immersion heater's share of the electricity, in per cent (roadmap R28).

    The immersion heater's counter, Modbus 62191, against the sum of it and
    the compressor's counter, over the coefficient of performance's spans and
    from its samples, so it exists exactly where the coefficient of
    performance does. A hundred per cent only where the compressor's counter
    stood still while the immersion heater's moved; where there is no figure
    the attribute skäl says why, as on the coefficient of performance.
    """

    _attr_has_entity_name = True
    _attr_icon = "mdi:heating-coil"
    _attr_native_unit_of_measurement = "%"
    _attr_suggested_display_precision = 1
    # Kept as long term statistics, like the coefficient of performance, so a
    # month of it can be drawn.
    _attr_state_class = SensorStateClass.MEASUREMENT

    #: The Swedish name of each span, which translations/sv.json carries word
    #: for word under the translation key aux_share_<span>.
    NAMES = {
        "day": "Spetsvärmeandel, dygn",
        "week": "Spetsvärmeandel, vecka",
        "month": "Spetsvärmeandel, månad",
        "year": "Spetsvärmeandel, rullande år",
        "lifetime": "Spetsvärmeandel, hela livslängden",
    }

    def __init__(self, runtime, span: str) -> None:
        super().__init__(runtime.web)
        self._runtime = runtime
        self._span = span
        self._attr_unique_id = unique_id(runtime.device, f"aux_share_{span}")
        self._attr_translation_key = f"aux_share_{span}"
        self._attr_device_info = runtime.device

    @property
    def native_value(self) -> float | None:
        result, _reason = share_for(self._runtime, self._span)
        # One span's share under another span's name would be another number.
        return result.value if result.basis == self._span else None

    @property
    def extra_state_attributes(self) -> dict[str, object]:
        result, reason = share_for(self._runtime, self._span)
        attributes = result.as_attributes()
        if reason:
            attributes["skäl"] = reason
        return with_english(attributes)

    @property
    def available(self) -> bool:
        # As the coefficient of performance: available without a figure, so
        # the reason can be read, except the day, which goes with a quiet
        # display or a counter row gone stale.
        if self._span == "day":
            return self.coordinator.last_update_success and not stale_rows(
                self.coordinator, counter_rows(self._runtime)
            )
        return True
