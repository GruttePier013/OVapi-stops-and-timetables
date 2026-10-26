"""Vernieuwknop per halte."""
from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .coordinator import DutchBusConfigEntry
from .sensor import DutchBusEntity


async def async_setup_entry(
    hass: HomeAssistant, entry: DutchBusConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    async_add_entities(
        RefreshButton(c) for c in entry.runtime_data.coordinators.values()
    )


class RefreshButton(DutchBusEntity, ButtonEntity):
    """Haalt direct nieuwe vertrektijden op (handig als polling uit staat)."""

    def __init__(self, coordinator) -> None:
        super().__init__(coordinator, "refresh")

    @property
    def available(self) -> bool:
        return True  # ook bruikbaar om een mislukte halte opnieuw te proberen

    async def async_press(self) -> None:
        await self.coordinator.async_refresh()
