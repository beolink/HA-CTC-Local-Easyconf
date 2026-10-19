"""The switch for the voluntary debug report, beside the option of the same name.

Report all values to the developer, Rapportera alla värden till utvecklaren in
Swedish: the owner's one setting, which the CTC page shows as a tick box among
the controls (dashboard_views.py) and Configure as an option. The switch reads
the option and writes it, nothing else; the reporter (debug_report.py) hears
the write and starts or stops, and the reload listener leaves a write of this
option alone, so switching it reloads nothing. A reload closes Modbus, and a
set-up that meets the controller's one place still taken waits for it.
"""

from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import CtcConfigEntry
from .const import CONF_DEBUG_REPORT
from .debug_report import debug_report_on
from .entity import async_switch_on_new_defaults
from .keys import unique_id


async def async_setup_entry(
    hass: HomeAssistant,
    entry: CtcConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    runtime = entry.runtime_data
    entities = [CtcDebugReportSwitch(entry, runtime)]
    async_switch_on_new_defaults(hass, entry, "switch", entities)
    async_add_entities(entities)


class CtcDebugReportSwitch(SwitchEntity):
    """Whether the debug report goes to the developer: the option, as a switch.

    On while the entry's option says so and off otherwise, off where it says
    nothing. Its state follows the option when it is written under Configure
    too, from the entry's update listener of its own.
    """

    _attr_has_entity_name = True
    _attr_entity_category = EntityCategory.CONFIG
    _attr_icon = "mdi:bug-outline"
    #: Rapportera alla värden till utvecklaren in Swedish, Report all values
    #: to the developer in English. The entity's key is the option's.
    _attr_translation_key = "debug_report"

    def __init__(self, entry: Any, runtime: Any) -> None:
        self._entry = entry
        self._attr_unique_id = unique_id(runtime.device, "debug_report")
        self._attr_device_info = runtime.device

    @property
    def is_on(self) -> bool:
        return debug_report_on(self._entry.options)

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.async_on_remove(self._entry.add_update_listener(self._async_options_written))

    async def _async_options_written(self, _hass: Any, _entry: Any) -> None:
        """The options were written, here or under Configure: show what they say now."""
        self.async_write_ha_state()

    async def async_turn_on(self, **kwargs: Any) -> None:
        self._write(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        self._write(False)

    def _write(self, on: bool) -> None:
        """Write the option, and only it, unless it already says so."""
        if debug_report_on(self._entry.options) is on:
            return
        self.hass.config_entries.async_update_entry(
            self._entry, options={**self._entry.options, CONF_DEBUG_REPORT: on}
        )
        self.async_write_ha_state()
