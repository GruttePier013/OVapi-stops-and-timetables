"""Proximity radius per stop."""
from __future__ import annotations

from homeassistant.components.number import NumberMode, RestoreNumber
from homeassistant.const import EntityCategory, UnitOfLength
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import CONF_DEFAULT_RADIUS, DEFAULT_PROXIMITY_RADIUS, MAX_RADIUS, MIN_RADIUS
from .coordinator import DutchBusConfigEntry, StopCoordinator
from .defaults import get_defaults
from .sensor import DutchBusEntity


async def async_setup_entry(
    hass: HomeAssistant, entry: DutchBusConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    async_add_entities(ProximityRadius(c) for c in entry.runtime_data.coordinators.values())


class ProximityRadius(DutchBusEntity, RestoreNumber):
    _attr_entity_category = EntityCategory.CONFIG
    _attr_native_min_value = MIN_RADIUS
    _attr_native_max_value = MAX_RADIUS
    _attr_native_step = 50
    _attr_mode = NumberMode.BOX
    _attr_native_unit_of_measurement = UnitOfLength.METERS

    def __init__(self, coordinator: StopCoordinator) -> None:
        super().__init__(coordinator, "proximity_radius")
        self._attr_native_value = get_defaults(coordinator.entry.options)[CONF_DEFAULT_RADIUS]

    @property
    def available(self) -> bool:
        return True

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        last = await self.async_get_last_number_data()
        if last and last.native_value is not None:
            self._attr_native_value = int(last.native_value)
        self.coordinator.proximity_radius = int(self._attr_native_value or DEFAULT_PROXIMITY_RADIUS)

    async def async_set_native_value(self, value: float) -> None:
        self._attr_native_value = int(value)
        self.coordinator.proximity_radius = int(value)
        self.async_write_ha_state()
        tracker = self.coordinator.entry.runtime_data.tracker
        if tracker:
            tracker.evaluate(self.coordinator, trigger=False)
