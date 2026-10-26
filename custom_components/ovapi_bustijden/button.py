"""Refresh buttons: one per stop and one that refreshes all stops."""
from __future__ import annotations

import asyncio

from homeassistant.components.button import ButtonEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import REFRESH_ALL_NAME
from .coordinator import DutchBusConfigEntry, StopCoordinator
from .sensor import DutchBusEntity


async def async_setup_entry(
    hass: HomeAssistant, entry: DutchBusConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    entities: list[ButtonEntity] = [RefreshButton(c) for c in entry.runtime_data.coordinators.values()]
    entities.append(RefreshAllButton(entry))
    async_add_entities(entities)


class RefreshButton(DutchBusEntity, ButtonEntity):
    """Fetches the departures right now (also resumes a stop that paused after errors)."""

    def __init__(self, coordinator: StopCoordinator) -> None:
        super().__init__(coordinator, "refresh")

    @property
    def available(self) -> bool:
        return True  # also usable to retry a failed or paused stop

    async def async_press(self) -> None:
        await self.coordinator.async_manual_refresh()


class RefreshAllButton(ButtonEntity):
    """Refreshes every stop (same as the ``ovapi_bustijden.refresh`` action without a target); no device of its own."""

    _attr_has_entity_name = True
    _attr_name = REFRESH_ALL_NAME
    _attr_icon = "mdi:refresh-circle"

    def __init__(self, entry: DutchBusConfigEntry) -> None:
        self._entry = entry
        self._attr_unique_id = f"{entry.entry_id}_refresh_all"

    async def async_press(self) -> None:
        await asyncio.gather(*(c.async_manual_refresh() for c in self._entry.runtime_data.coordinators.values()))
