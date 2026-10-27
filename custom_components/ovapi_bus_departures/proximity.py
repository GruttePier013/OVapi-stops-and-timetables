"""Proximity: automatic "zone enter" per stop based on Home Assistant person entities.

No Home Assistant zones are created or changed. The integration compares the GPS position of
persons with the stop, starts a refresh visit when someone enters the radius and keeps track
of who is getting closer (used for "approach only" refreshing and the distance sensor).
"""
from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any

from homeassistant.const import EVENT_STATE_CHANGED
from homeassistant.core import CALLBACK_TYPE, Event, EventStateChangedData, HomeAssistant, callback
from homeassistant.util import dt as dt_util

from .api import distance_m
from .const import APPROACH_HOLD_MIN, APPROACH_MIN_DELTA_M
from .coordinator import StopCoordinator

_LOGGER = logging.getLogger(__name__)


@callback
def _is_person_event(data: EventStateChangedData) -> bool:
    return str(data["entity_id"]).startswith("person.")


def _moved_less_than(old: Any, new: Any, meters: float) -> bool:
    try:
        return distance_m(
            float(old.attributes["latitude"]),
            float(old.attributes["longitude"]),
            float(new.attributes["latitude"]),
            float(new.attributes["longitude"]),
        ) < meters
    except (KeyError, TypeError, ValueError):
        return False


def coarse_distance(meters: int) -> int:
    """Round for the distance sensor so a moving person does not cause a state write per GPS fix."""
    if meters < 1000:
        return round(meters / 10) * 10
    if meters < 10_000:
        return round(meters / 100) * 100
    return round(meters / 1000) * 1000


class ProximityTracker:
    def __init__(
        self,
        hass: HomeAssistant,
        coordinators: dict[str, StopCoordinator],
        persons: list[str],
    ) -> None:
        self.hass = hass
        self.coordinators = coordinators
        self.persons = set(persons)  # empty = all persons
        # reference distance per (stop, person) used to detect approaching
        self._reference: dict[tuple[str, str], int] = {}

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
    def _handle_state_change(self, event: Event[EventStateChangedData]) -> None:
        if self.persons and event.data["entity_id"] not in self.persons:
            return
        old, new = event.data.get("old_state"), event.data.get("new_state")
        if old is not None and new is not None and old.state == new.state and _moved_less_than(old, new, 5):
            return  # e.g. only battery or accuracy changed
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
    def _update_approach(self, coordinator: StopCoordinator, dists: dict[str, int]) -> None:
        """Mark persons whose distance shrank noticeably as approaching (for a limited time)."""
        now = dt_util.utcnow()
        for key in [k for k in self._reference if k[0] == coordinator.key and k[1] not in dists]:
            del self._reference[key]  # person gone or no position any more
        for entity, distance in dists.items():
            key = (coordinator.key, entity)
            reference = self._reference.get(key)
            if reference is None:
                self._reference[key] = distance
            elif distance <= reference - APPROACH_MIN_DELTA_M:
                coordinator.approach_until[entity] = now + timedelta(minutes=APPROACH_HOLD_MIN)
                coordinator.approach_pending = True
                self._reference[key] = distance
            elif distance >= reference + APPROACH_MIN_DELTA_M:
                self._reference[key] = distance

    @callback
    def evaluate(self, coordinator: StopCoordinator, trigger: bool) -> None:
        loc = coordinator.location
        if loc is None:
            coordinator.set_proximity({}, None)
            return
        dists = {
            entity: round(distance_m(loc[0], loc[1], lat, lon)) for entity, (lat, lon) in self._positions().items()
        }
        self._update_approach(coordinator, dists)
        radius = coordinator.proximity_radius
        nearby = {e: d for e, d in dists.items() if d <= radius}
        nearest_entity = min(dists, key=dists.__getitem__) if dists else None
        nearest = coarse_distance(dists[nearest_entity]) if nearest_entity else None
        coordinator.set_proximity(nearby, nearest, trigger=trigger, nearest_entity=nearest_entity)
