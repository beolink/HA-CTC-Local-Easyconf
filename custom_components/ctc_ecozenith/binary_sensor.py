"""Binary sensors derived from the Modbus status registers.

An enum register is judged by its code, never by its label. The label is a
Swedish string that may be reworded or translated one day, and a binary that
compared against it would then go quietly off for good; the code is what the
controller actually said. The coordinator keeps the codes beside the labels for
exactly this, so the binaries work whatever language Home Assistant speaks and
their names follow it.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.event import async_call_later
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import CtcConfigEntry
from .attribute_names import with_english
from .const import (
    HP_ALARM_CODE,
    HP_BLOCKED_CODE,
    HP_DEFROST_CODE,
    HP_RUNNING_CODES,
    SG_NORMAL_CODE,
)
from .entity import async_switch_on_new_defaults
from .harvest import utcnow
from .keys import unique_id


@dataclass(frozen=True)
class DerivedBinary:
    """A boolean worked out from one or more of the Modbus readings.

    With ``by_code`` the test is given the code behind the first source, an
    integer; otherwise it is given the readings of every source in order, with
    None for one that is absent.

    The key is the entity's translation key as well: the entity takes its name
    from the translations in Home Assistant's language (roadmap R23), and
    ``name`` is the Swedish one, which translations/sv.json carries word for
    word.
    """

    key: str
    name: str
    sources: tuple[str, ...]
    test: Callable[..., bool]
    device_class: BinarySensorDeviceClass | None = None
    icon: str | None = None
    by_code: bool = False
    #: What the entity shows as attributes: attribute name and reading key.
    attributes: tuple[tuple[str, str], ...] = ()
    #: Whether the entity carries the alarm log's episodes as an attribute:
    #: the last ten alarms the display showed, with code, start, end and the
    #: outdoor temperature, where the display is harvested at all.
    episodes: bool = False


def _powered(*readings: Any) -> bool:
    """True when any of the readings is a power above zero."""
    return any(isinstance(value, (int, float)) and value > 0 for value in readings)


DERIVED: tuple[DerivedBinary, ...] = (
    DerivedBinary(
        "compressor_running",
        "Kompressor i drift",
        ("hp1_status",),
        lambda code: code in HP_RUNNING_CODES,
        BinarySensorDeviceClass.RUNNING,
        "mdi:heat-pump",
        by_code=True,
    ),
    DerivedBinary(
        "defrosting",
        "Avfrostning",
        ("hp1_status",),
        lambda code: code == HP_DEFROST_CODE,
        None,
        "mdi:snowflake-melt",
        by_code=True,
    ),
    DerivedBinary(
        "alarm",
        "Larm",
        ("hp1_status",),
        lambda code: code == HP_ALARM_CODE,
        BinarySensorDeviceClass.PROBLEM,
        by_code=True,
        episodes=True,
    ),
    DerivedBinary(
        "blocked",
        "Blockerad",
        ("hp1_status",),
        lambda code: code == HP_BLOCKED_CODE,
        None,
        "mdi:cancel",
        by_code=True,
    ),
    # Both heaters: in the EcoZenith tanks the upper one sits in the hot water
    # part and does most of the topping up, so a binary that watched only the
    # lower one said "off" in the most common case.
    DerivedBinary(
        "immersion_active",
        "Elpatron aktiv",
        ("immersion_upper_kw", "immersion_lower_kw"),
        _powered,
        BinarySensorDeviceClass.RUNNING,
        "mdi:heating-coil",
        attributes=(
            ("elpatron övre", "immersion_upper_kw"),
            ("elpatron nedre", "immersion_lower_kw"),
        ),
    ),
    DerivedBinary(
        "smartgrid_active",
        "SmartGrid aktiv",
        ("sg_mode",),
        lambda code: code != SG_NORMAL_CODE,
        None,
        "mdi:transmission-tower",
        by_code=True,
    ),
)


#: The heat pump's own alarm status, read from its register: one of the two
#: ways the display alarm is on, the other being an alarm the panel shows.
_HEATPUMP_ALARM = next(item for item in DERIVED if item.key == "alarm")


def derive(item: DerivedBinary, data: dict[str, Any], codes: dict[str, int]) -> bool | None:
    """The binary's state from the coordinator's readings, or None without them.

    A code is only trusted for a reading that is in ``data``: the codes are a
    dictionary kept beside the readings, and the readings decide what is there.
    """
    if item.by_code:
        source = item.sources[0]
        if source not in data or source not in codes:
            return None
        return item.test(codes[source])
    readings = [data.get(key) for key in item.sources]
    if all(value is None for value in readings):
        return None
    return item.test(*readings)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: CtcConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    runtime = entry.runtime_data
    entities: list[BinarySensorEntity] = [CtcDerivedBinary(runtime, item) for item in DERIVED]
    # Only where the display is harvested: the alarm log is what it reads.
    if runtime.alarms is not None:
        entities.append(CtcDisplayAlarm(runtime))
    async_switch_on_new_defaults(hass, entry, "binary_sensor", entities)
    async_add_entities(entities)


class CtcDisplayAlarm(CoordinatorEntity, BinarySensorEntity):
    """An alarm the display shows, read from the panel's own texts, with its E-code.

    On while a round of the harvest saw the open episode on the panel within
    the confirmation window (alarms.confirm_window), and on while the heat
    pump's own alarm status reads on. The code, the text, when it began and
    whether it is confirmed come from the alarm log. The window is timed here,
    not left to the next round: an entity that nothing reads any more, with the
    Modbus poll switched off or the display gone quiet, still goes off when its
    confirmation runs out.
    """

    _attr_has_entity_name = True
    _attr_translation_key = "display_alarm"
    _attr_device_class = BinarySensorDeviceClass.PROBLEM
    _attr_icon = "mdi:monitor-alert"

    def __init__(self, runtime) -> None:
        super().__init__(runtime.modbus)
        self._runtime = runtime
        self._attr_unique_id = unique_id(runtime.device, "display_alarm")
        self._attr_device_info = runtime.device
        self._cancel_expiry: Callable[[], None] | None = None

    async def async_added_to_hass(self) -> None:
        """Follow the display's harvest as well, which is where a sighting comes from."""
        await super().async_added_to_hass()
        web = getattr(self._runtime, "web", None)
        if web is not None:
            self.async_on_remove(web.async_add_listener(self._handle_coordinator_update))
        self.async_on_remove(self._stop_expiry)
        self._schedule_expiry()

    def _confirmed(self) -> bool:
        return self._runtime.alarms.confirmed(utcnow())

    @property
    def is_on(self) -> bool:
        if self._confirmed():
            return True
        return bool(
            derive(
                _HEATPUMP_ALARM,
                self.coordinator.data or {},
                getattr(self.coordinator, "codes", None) or {},
            )
        )

    @property
    def available(self) -> bool:
        return bool(self.coordinator.last_update_success) or self._confirmed()

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """The open episode, and whether a round has seen it recently.

        Empty fields when no episode is open, so the attributes say the display
        shows nothing rather than keep an old alarm's code.
        """
        log = self._runtime.alarms
        latest = log.latest if log.active else None
        attributes: dict[str, Any] = {
            "kod": latest["code"] if latest else None,
            "text": latest["text"] if latest else None,
            "start": latest["start"] if latest else None,
            "visat": latest["shown"] if latest else None,
            "senast bekräftad": (
                log.last_confirmed.isoformat(timespec="seconds")
                if latest and log.last_confirmed is not None
                else None
            ),
            "bekräftad": self._confirmed(),
        }
        return with_english(attributes) or {}

    def _schedule_expiry(self) -> None:
        """Arrange a state write just after the confirmation runs out, if there is one."""
        self._stop_expiry()
        until = self._runtime.alarms.confirmed_until()
        if until is None or self.hass is None:
            return
        # A second past the window, so the write comes after the window has
        # closed rather than at its edge.
        delay = (until - utcnow()).total_seconds() + 1
        if delay > 0:
            self._cancel_expiry = async_call_later(self.hass, delay, self._expired)

    def _stop_expiry(self) -> None:
        if self._cancel_expiry is not None:
            self._cancel_expiry()
            self._cancel_expiry = None

    @callback
    def _expired(self, _now: Any) -> None:
        self._cancel_expiry = None
        self.async_write_ha_state()

    @callback
    def _handle_coordinator_update(self) -> None:
        self._schedule_expiry()
        super()._handle_coordinator_update()


class CtcDerivedBinary(CoordinatorEntity, BinarySensorEntity):
    """A boolean read off one of the status registers."""

    _attr_has_entity_name = True

    def __init__(self, runtime, item: DerivedBinary) -> None:
        super().__init__(runtime.modbus)
        self._item = item
        self._runtime = runtime
        self._attr_unique_id = unique_id(runtime.device, item.key)
        self._attr_translation_key = item.key
        self._attr_device_info = runtime.device
        if item.device_class:
            self._attr_device_class = item.device_class
        if item.icon:
            self._attr_icon = item.icon

    async def async_added_to_hass(self) -> None:
        """Follow the display's harvest as well where the episodes come from it.

        The state is Modbus's and arrives every poll; the episodes are
        concluded once per harvest, and without this they would wait up to a
        poll to show, which is where a closed episode looked open.
        """
        await super().async_added_to_hass()
        web = getattr(self._runtime, "web", None)
        if self._item.episodes and web is not None:
            self.async_on_remove(web.async_add_listener(self._handle_coordinator_update))

    @property
    def is_on(self) -> bool | None:
        return derive(
            self._item,
            self.coordinator.data or {},
            getattr(self.coordinator, "codes", None) or {},
        )

    @property
    def available(self) -> bool:
        if not self.coordinator.last_update_success:
            return False
        data = self.coordinator.data or {}
        if self._item.by_code:
            return self._item.sources[0] in data
        return any(key in data for key in self._item.sources)

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """The readings the binary was worked out from, and for the alarm the log.

        The episodes come from the display's harvest, not from the register
        this binary reads: a sensor alarm such as E017 leaves the heat pump
        running, so the binary can be off while the log holds an open episode.
        The list is there whenever the display is harvested, empty until the
        panel has shown an alarm.
        """
        attributes: dict[str, Any] = {}
        if self._item.attributes:
            data = self.coordinator.data or {}
            attributes.update({name: data.get(key) for name, key in self._item.attributes})
        log = getattr(self._runtime, "alarms", None)
        if self._item.episodes and log is not None:
            attributes["episoder"] = log.attributes()
            # The English twin with English keys inside, not a copy.
            attributes["episodes"] = log.attributes(english=True)
        return with_english(attributes) or None
