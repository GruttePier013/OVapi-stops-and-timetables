"""Switches per stop: refresh in zone (main switch) and approach-only refreshing."""
from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.const import STATE_OFF, EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity

from .const import APPROACH_MIN_DELTA_M, CONF_DEFAULT_APPROACH_ONLY, CONF_DEFAULT_REFRESH_ON_ZONE
from .coordinator import DutchBusConfigEntry, StopCoordinator
from .defaults import get_defaults
from .parser import distance_m
from .sensor import DutchBusEntity


async def async_setup_entry(
    hass: HomeAssistant, entry: DutchBusConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    for c in entry.runtime_data.coordinators.values():
        async_add_entities([RefreshOnApproach(c), ApproachOnly(c)])


class RefreshOnApproach(DutchBusEntity, SwitchEntity, RestoreEntity):
    """Main switch for refreshing in the zone: on entry and (optionally) every X minutes while someone is in the zone."""

    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator: StopCoordinator) -> None:
        super().__init__(coordinator, "refresh_on_approach")
        self._attr_is_on = get_defaults(coordinator.entry.options)[CONF_DEFAULT_REFRESH_ON_ZONE]

    def _in_home_zone(self) -> bool:
        """Is the stop inside the Home Assistant home zone? Then 'refresh in zone' is off by default."""
        loc = self.coordinator.location
        if loc is None:
            return False
        home = self.hass.states.get("zone.home")
        attrs = home.attributes if home else {}
        lat = attrs.get("latitude", self.hass.config.latitude)
        lon = attrs.get("longitude", self.hass.config.longitude)
        radius = attrs.get("radius", self.hass.config.radius)
        try:
            return distance_m(loc[0], loc[1], float(lat), float(lon)) <= float(radius)
        except (TypeError, ValueError):
            return False

    @property
    def available(self) -> bool:
        return True

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {
            "uitleg": (
                "Aan: zodra een gevolgd persoon binnen de zone-radius van deze halte komt, worden de vertrektijden "
                "direct vernieuwd (daarna volgens de vernieuwingsfrequentie in zone). Staat de halte in de "
                "thuiszone van Home Assistant, dan staat dit standaard uit. Gevolgde personen stel je in via "
                "Configureren."
            ),
        }

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        last = await self.async_get_last_state()
        if last is not None:
            self._attr_is_on = last.state != STATE_OFF
        elif self._in_home_zone():
            self._attr_is_on = False  # everyone at home would make the stop 'in the zone' all the time
        self.coordinator.refresh_on_approach = bool(self._attr_is_on)
        self.coordinator.apply_zone_settings()

    async def async_turn_on(self, **kwargs: Any) -> None:
        self._attr_is_on = True
        self.coordinator.refresh_on_approach = True
        self.coordinator.apply_zone_settings()
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs: Any) -> None:
        self._attr_is_on = False
        self.coordinator.refresh_on_approach = False
        self.coordinator.apply_zone_settings()
        self.async_write_ha_state()


class ApproachOnly(DutchBusEntity, SwitchEntity, RestoreEntity):
    """Interval refreshes in the zone only happen while someone is getting closer to the stop."""

    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator: StopCoordinator) -> None:
        super().__init__(coordinator, "approach_only")
        self._attr_is_on = get_defaults(coordinator.entry.options)[CONF_DEFAULT_APPROACH_ONLY]

    @property
    def available(self) -> bool:
        return True

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {
            "uitleg": (
                "Aan: de vernieuwingen in de zone (na de eerste bij binnenkomst) gebeuren alleen als iemand "
                f"minstens {APPROACH_MIN_DELTA_M} m dichterbij is gekomen sinds de vorige vernieuwing."
            ),
        }

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        last = await self.async_get_last_state()
        if last is not None:
            self._attr_is_on = last.state != STATE_OFF
        self.coordinator.approach_only = bool(self._attr_is_on)

    async def async_turn_on(self, **kwargs: Any) -> None:
        self._attr_is_on = True
        self.coordinator.approach_only = True
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs: Any) -> None:
        self._attr_is_on = False
        self.coordinator.approach_only = False
        self.async_write_ha_state()
