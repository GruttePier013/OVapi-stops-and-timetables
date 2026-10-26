"""Verversen bij nadering aan/uit."""
from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.const import STATE_OFF, EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity

from .coordinator import DutchBusConfigEntry
from .parser import distance_m
from .sensor import DutchBusEntity


async def async_setup_entry(
    hass: HomeAssistant, entry: DutchBusConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    async_add_entities(RefreshOnApproach(c) for c in entry.runtime_data.coordinators.values())


class RefreshOnApproach(DutchBusEntity, SwitchEntity, RestoreEntity):
    """Ververs zodra een persoon de zone-radius binnenkomt en daarna op een instelbaar interval zolang iemand in de zone is."""

    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator) -> None:
        super().__init__(coordinator, "refresh_on_approach")
        self._attr_is_on = True

    def _in_home_zone(self) -> bool:
        """Ligt de halte binnen de thuiszone van Home Assistant? Dan is 'vernieuwen als in zone' standaard uit."""
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
                "direct vernieuwd en daarna op het ingestelde interval (standaard 2 minuten), zolang iemand in de zone blijft. Staat de halte "
                "in de thuiszone van Home Assistant, dan staat dit standaard uit. Uit: alleen de "
                "standaard vernieuwingsfrequentie. Gevolgde personen stel je in via Configureren."
            ),
            "interval_minuten": round(self.coordinator._zone_interval() / 60, 2),
        }

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        last = await self.async_get_last_state()
        if last is not None:
            self._attr_is_on = last.state != STATE_OFF
        elif self._in_home_zone():
            self._attr_is_on = False  # iedereen die thuis is zou de halte steeds 'in de zone' maken
        self.coordinator.refresh_on_approach = self._attr_is_on

    async def async_turn_on(self, **kwargs: Any) -> None:
        self._attr_is_on = True
        self.coordinator.refresh_on_approach = True
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs: Any) -> None:
        self._attr_is_on = False
        self.coordinator.refresh_on_approach = False
        self.async_write_ha_state()
