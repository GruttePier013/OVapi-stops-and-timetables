"""Selects per stop: refresh schedule, zone refresh frequency, zone stop-after (restored after a restart)."""
from __future__ import annotations

from typing import Any

from homeassistant.components.select import SelectEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity

from .const import (
    CONF_DEFAULT_SCHEDULE,
    CONF_DEFAULT_ZONE_INTERVAL,
    CONF_DEFAULT_ZONE_STOP_AFTER,
    DOMAIN,
)
from .coordinator import DutchBusConfigEntry, StopCoordinator
from .defaults import get_defaults
from .schedules import (
    DEFAULT_SCHEDULE,
    DEFAULT_ZONE_INTERVAL,
    DEFAULT_ZONE_STOP_AFTER,
    SCHEDULES,
    ZONE_INTERVALS,
    ZONE_STOP_AFTER,
    nearest_option,
)
from .sensor import DutchBusEntity


async def async_setup_entry(
    hass: HomeAssistant, entry: DutchBusConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    for c in entry.runtime_data.coordinators.values():
        async_add_entities([RefreshSchedule(c), ZoneRefreshFrequency(c), ZoneStopAfter(c)])


class RefreshSchedule(DutchBusEntity, SelectEntity, RestoreEntity):
    """How often this stop refreshes itself (besides the Refresh button)."""

    _attr_entity_category = EntityCategory.CONFIG
    _attr_options = list(SCHEDULES)

    def __init__(self, coordinator: StopCoordinator) -> None:
        super().__init__(coordinator, "refresh_schedule")
        self._attr_current_option = get_defaults(coordinator.entry.options)[CONF_DEFAULT_SCHEDULE]

    @property
    def available(self) -> bool:
        return True  # the schedule can be set even when OVapi does not answer

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        last = await self.async_get_last_state()
        if last and last.state in SCHEDULES:
            self._attr_current_option = last.state
        self.coordinator.set_schedule(self._attr_current_option or DEFAULT_SCHEDULE)

    async def async_select_option(self, option: str) -> None:
        self._attr_current_option = option
        self.coordinator.set_schedule(option)
        self.async_write_ha_state()


class _ZoneSelect(DutchBusEntity, SelectEntity, RestoreEntity):
    """Base of the two zone selects: restore, migrate from the old number entity, apply to the coordinator."""

    _attr_entity_category = EntityCategory.CONFIG
    _options_map: dict[str, Any]
    _migrate_name: str  # key in the migrated values of the old number entity

    @property
    def available(self) -> bool:
        return True

    def _migrated_option(self) -> str | None:
        """Closest option for the value of the old number entity (minutes), if there was one."""
        value = self.hass.data.get(DOMAIN, {}).get("migrated_zone_settings", {}).get(self.coordinator.key, {}).get(
            self._migrate_name
        )
        return nearest_option(self._options_map, value)

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        last = await self.async_get_last_state()
        if last and last.state in self._options_map:
            self._attr_current_option = last.state
        elif (migrated := self._migrated_option()) is not None:
            self._attr_current_option = migrated
        self._apply()

    def _apply(self) -> None:  # pragma: no cover - overridden
        raise NotImplementedError

    async def async_select_option(self, option: str) -> None:
        self._attr_current_option = option
        self._apply()
        self.async_write_ha_state()


class ZoneRefreshFrequency(_ZoneSelect):
    """How often the stop refreshes while someone is in the zone (only when 'refresh in zone' is on).

    'on_entry' refreshes only at the moment someone enters the zone.
    """

    _attr_options = list(ZONE_INTERVALS)
    _options_map = ZONE_INTERVALS
    _migrate_name = "interval"

    def __init__(self, coordinator: StopCoordinator) -> None:
        super().__init__(coordinator, "zone_refresh")
        self._attr_current_option = get_defaults(coordinator.entry.options)[CONF_DEFAULT_ZONE_INTERVAL]

    def _apply(self) -> None:
        self.coordinator.zone_interval_min = ZONE_INTERVALS[self._attr_current_option or DEFAULT_ZONE_INTERVAL]
        self.coordinator.apply_zone_settings()


class ZoneStopAfter(_ZoneSelect):
    """After how many minutes per visit refreshing in the zone stops (even if you are still there)."""

    _attr_options = list(ZONE_STOP_AFTER)
    _options_map = ZONE_STOP_AFTER
    _migrate_name = "stop_after"

    def __init__(self, coordinator: StopCoordinator) -> None:
        super().__init__(coordinator, "zone_stop_after")
        self._attr_current_option = get_defaults(coordinator.entry.options)[CONF_DEFAULT_ZONE_STOP_AFTER]

    def _apply(self) -> None:
        self.coordinator.zone_stop_after_min = ZONE_STOP_AFTER[self._attr_current_option or DEFAULT_ZONE_STOP_AFTER]
        self.coordinator.apply_zone_settings()
