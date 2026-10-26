"""Nabijheidsstraal per halte."""
from __future__ import annotations

from typing import Any

from homeassistant.components.number import NumberMode, RestoreNumber
from homeassistant.const import EntityCategory, UnitOfLength, UnitOfTime
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DEFAULT_PROXIMITY_RADIUS, DEFAULT_ZONE_INTERVAL, DEFAULT_ZONE_STOP_AFTER
from .coordinator import DutchBusConfigEntry
from .sensor import DutchBusEntity


async def async_setup_entry(
    hass: HomeAssistant, entry: DutchBusConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    entities: list[DutchBusEntity] = []
    for c in entry.runtime_data.coordinators.values():
        entities += [ProximityRadius(c), ZoneInterval(c), ZoneStopAfter(c)]
    async_add_entities(entities)


class ProximityRadius(DutchBusEntity, RestoreNumber):
    _attr_entity_category = EntityCategory.CONFIG
    _attr_native_min_value = 50
    _attr_native_max_value = 5000
    _attr_native_step = 50
    _attr_mode = NumberMode.BOX
    _attr_native_unit_of_measurement = UnitOfLength.METERS

    def __init__(self, coordinator) -> None:
        super().__init__(coordinator, "proximity_radius")
        self._attr_native_value = DEFAULT_PROXIMITY_RADIUS

    @property
    def available(self) -> bool:
        return True

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {
            "uitleg": "Straal in meters rond de halte: komt een gevolgd persoon hierbinnen, dan geldt 'Vernieuwen in zone'.",
        }

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        last = await self.async_get_last_number_data()
        if last and last.native_value is not None:
            self._attr_native_value = last.native_value
        self.coordinator.proximity_radius = int(self._attr_native_value)

    async def async_set_native_value(self, value: float) -> None:
        self._attr_native_value = value
        self.coordinator.proximity_radius = int(value)
        self.async_write_ha_state()
        tracker = self.coordinator.config_entry.runtime_data.tracker
        if tracker:
            tracker.evaluate(self.coordinator, trigger=False)


class ZoneStopAfter(DutchBusEntity, RestoreNumber):
    """Na hoeveel minuten 'vernieuwen in zone' stopt, ook als de persoon in de zone blijft."""

    _attr_entity_category = EntityCategory.CONFIG
    _attr_native_min_value = 2
    _attr_native_max_value = 240
    _attr_native_step = 1
    _attr_mode = NumberMode.BOX
    _attr_native_unit_of_measurement = UnitOfTime.MINUTES

    def __init__(self, coordinator) -> None:
        super().__init__(coordinator, "zone_stop_after")
        self._attr_native_value = DEFAULT_ZONE_STOP_AFTER

    @property
    def available(self) -> bool:
        return True

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {
            "uitleg": (
                "Na dit aantal minuten stopt het vernieuwen in de zone, ook als iemand in de zone blijft. "
                "Het begint opnieuw zodra de zone is verlaten en weer binnengekomen."
            ),
        }

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        last = await self.async_get_last_number_data()
        if last and last.native_value is not None:
            self._attr_native_value = last.native_value
        self.coordinator.set_zone_stop_after(int(self._attr_native_value))

    async def async_set_native_value(self, value: float) -> None:
        self._attr_native_value = value
        self.coordinator.set_zone_stop_after(int(value))
        self.async_write_ha_state()


class ZoneInterval(DutchBusEntity, RestoreNumber):
    """Hoe vaak (in minuten) er ververst wordt zolang iemand in de zone is."""

    _attr_entity_category = EntityCategory.CONFIG
    _attr_native_min_value = 1
    _attr_native_max_value = 30
    _attr_native_step = 1
    _attr_mode = NumberMode.BOX
    _attr_native_unit_of_measurement = UnitOfTime.MINUTES

    def __init__(self, coordinator) -> None:
        super().__init__(coordinator, "zone_interval")
        self._attr_native_value = DEFAULT_ZONE_INTERVAL

    @property
    def available(self) -> bool:
        return True

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {"uitleg": "Interval tussen vernieuwingen zolang een gevolgd persoon in de zone is (standaard 2 minuten)."}

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        last = await self.async_get_last_number_data()
        if last and last.native_value is not None:
            self._attr_native_value = last.native_value
        self.coordinator.set_zone_interval(float(self._attr_native_value))

    async def async_set_native_value(self, value: float) -> None:
        self._attr_native_value = value
        self.coordinator.set_zone_interval(float(value))
        self.async_write_ha_state()
