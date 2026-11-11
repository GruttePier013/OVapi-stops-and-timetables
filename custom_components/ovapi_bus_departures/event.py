"""Event entity per stop: a bus is cancelled, gets a delay or starts driving (for automations)."""
from __future__ import annotations

from typing import Any

from homeassistant.components.event import EventEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .coordinator import DutchBusConfigEntry, StopCoordinator
from .sensor import DutchBusEntity

EVENT_TYPES = ["cancelled", "delayed", "live"]


async def async_setup_entry(
    hass: HomeAssistant, entry: DutchBusConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    async_add_entities(DepartureEvents(c) for c in entry.runtime_data.coordinators.values())


class DepartureEvents(DutchBusEntity, EventEntity):
    """Fires ``cancelled``, ``delayed`` and ``live`` with the line, destination, times and delay as attributes."""

    _attr_event_types = EVENT_TYPES

    def __init__(self, coordinator: StopCoordinator) -> None:
        super().__init__(coordinator, "departure_events")

    @property
    def available(self) -> bool:
        return True

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.async_on_remove(self.coordinator.async_add_event_listener(self._handle_event))

    @callback
    def _handle_event(self, event_type: str, attributes: dict[str, Any]) -> None:
        self._trigger_event(event_type, attributes)
        self.async_write_ha_state()
