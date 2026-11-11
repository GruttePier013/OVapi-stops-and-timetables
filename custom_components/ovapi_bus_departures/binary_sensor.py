"""Binary sensors per stop: 'Live departure' (a bus is really driving) and 'Paused' (refreshes stopped)."""
from __future__ import annotations

from typing import Any

from homeassistant.components.binary_sensor import BinarySensorDeviceClass, BinarySensorEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .coordinator import DutchBusConfigEntry, StopCoordinator
from .sensor import DutchBusEntity, MinuteTickerMixin


async def async_setup_entry(
    hass: HomeAssistant, entry: DutchBusConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinators = entry.runtime_data.coordinators.values()
    async_add_entities([*(LiveDeparture(c) for c in coordinators), *(Paused(c) for c in coordinators)])


class LiveDeparture(MinuteTickerMixin, DutchBusEntity, BinarySensorEntity):
    """On while at least one upcoming departure has a live vehicle (OVapi status DRIVING or ARRIVED)."""

    _attr_device_class = BinarySensorDeviceClass.RUNNING

    def __init__(self, coordinator: StopCoordinator) -> None:
        super().__init__(coordinator, "live_departure")

    @property
    def is_on(self) -> bool:
        return any(d["live"] for d in self.coordinator.view())

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        view = self.coordinator.view()
        return {"live_count": sum(1 for d in view if d["live"]), "departures": len(view)}


class Paused(DutchBusEntity, BinarySensorEntity):
    """On while this stop has paused automatic refreshes after too many consecutive errors."""

    _attr_device_class = BinarySensorDeviceClass.PROBLEM
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: StopCoordinator) -> None:
        super().__init__(coordinator, "paused")

    @property
    def available(self) -> bool:
        return True

    @property
    def is_on(self) -> bool:
        return self.coordinator.paused

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        c = self.coordinator
        return {
            "consecutive_errors": c.consecutive_failures,
            "max_errors": c.max_errors,
            "api_paused_for_s": round(c.api.blocked_for),
        }
