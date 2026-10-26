"""Verversschema per halte (select-entiteit, onthoudt de keuze na herstart)."""
from __future__ import annotations

from homeassistant.components.select import SelectEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity

from .coordinator import DutchBusConfigEntry, StopCoordinator
from .schedules import DEFAULT_SCHEDULE, SCHEDULES
from .sensor import DutchBusEntity


async def async_setup_entry(
    hass: HomeAssistant, entry: DutchBusConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    async_add_entities(
        RefreshSchedule(c) for c in entry.runtime_data.coordinators.values()
    )


class RefreshSchedule(DutchBusEntity, SelectEntity, RestoreEntity):
    """Hoe vaak deze halte zichzelf ververst (naast de Vernieuwen-knop)."""

    _attr_entity_category = EntityCategory.CONFIG
    _attr_options = list(SCHEDULES)

    def __init__(self, coordinator: StopCoordinator) -> None:
        super().__init__(coordinator, "refresh_schedule")
        self._attr_current_option = DEFAULT_SCHEDULE

    @property
    def available(self) -> bool:
        return True  # het schema is ook in te stellen als OVapi even niet antwoordt

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        last = await self.async_get_last_state()
        if last and last.state in SCHEDULES:
            self._attr_current_option = last.state
        self.coordinator.set_schedule(self._attr_current_option)

    async def async_select_option(self, option: str) -> None:
        self._attr_current_option = option
        self.coordinator.set_schedule(option)
        self.async_write_ha_state()
