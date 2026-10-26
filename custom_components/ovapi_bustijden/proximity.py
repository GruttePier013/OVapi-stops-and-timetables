"""Nabijheid: automatisch 'zone enter' per halte op basis van HA person-entiteiten.

Er worden bewust geen Home Assistant-zones aangemaakt of gewijzigd; de integratie
vergelijkt de GPS-positie van personen met de halte en ververst één keer zodra
iemand de straal binnenkomt.
"""
from __future__ import annotations

import logging
import time
from typing import Any

from homeassistant.const import EVENT_STATE_CHANGED
from homeassistant.core import CALLBACK_TYPE, Event, HomeAssistant, callback

from .api import distance_m
from .const import MIN_APPROACH_REFRESH_GAP
from .coordinator import StopCoordinator

_LOGGER = logging.getLogger(__name__)


@callback
def _is_person_event(data: Any) -> bool:
    return str(data["entity_id"]).startswith("person.")


class ProximityTracker:
    def __init__(
        self,
        hass: HomeAssistant,
        coordinators: dict[str, StopCoordinator],
        persons: list[str],
    ) -> None:
        self.hass = hass
        self.coordinators = coordinators
        self.persons = set(persons)  # leeg = alle personen

    @callback
    def async_start(self) -> CALLBACK_TYPE:
        for c in self.coordinators.values():
            self.evaluate(c, trigger=False)
        return self.hass.bus.async_listen(
            EVENT_STATE_CHANGED,
            self._handle_state_change,
            event_filter=_is_person_event,
        )

    @callback
    def _handle_state_change(self, event: Event) -> None:
        for c in self.coordinators.values():
            self.evaluate(c, trigger=True)

    def _positions(self) -> dict[str, tuple[float, float]]:
        out: dict[str, tuple[float, float]] = {}
        for st in self.hass.states.async_all("person"):
            if self.persons and st.entity_id not in self.persons:
                continue
            lat, lon = st.attributes.get("latitude"), st.attributes.get("longitude")
            if lat is None or lon is None:
                continue
            try:
                out[st.entity_id] = (float(lat), float(lon))
            except (TypeError, ValueError):
                continue
        return out

    @callback
    def evaluate(self, coordinator: StopCoordinator, trigger: bool) -> None:
        loc = coordinator.location
        if loc is None:
            coordinator.set_proximity({}, None)
            return
        dists = {
            ent: round(distance_m(loc[0], loc[1], lat, lon))
            for ent, (lat, lon) in self._positions().items()
        }
        radius = coordinator.proximity_radius
        nearby = {e: d for e, d in dists.items() if d <= radius}
        entered = set(nearby) - set(coordinator.nearby)
        coordinator.set_proximity(nearby, min(dists.values()) if dists else None)
        if (
            trigger
            and entered
            and coordinator.refresh_on_approach
            and time.monotonic() - coordinator.last_fetch_monotonic >= MIN_APPROACH_REFRESH_GAP
        ):
            _LOGGER.debug("%s: %s binnen %s m, verversen", coordinator.stop["name"], entered, radius)
            self.hass.async_create_task(coordinator.async_refresh())
