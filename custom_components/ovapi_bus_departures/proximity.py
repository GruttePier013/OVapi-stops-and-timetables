"""Proximity: automatic "zone enter" per stop based on Home Assistant person entities.

No Home Assistant zones are created or changed. The integration compares the GPS position of
persons with the stop, starts a refresh visit when someone enters the radius and keeps track
of who is getting closer (used for "approach only" refreshing and the distance sensor).
"""
from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any

from homeassistant.const import (
    ATTR_FRIENDLY_NAME,
    EVENT_STATE_CHANGED,
    STATE_HOME,
    STATE_UNAVAILABLE,
    STATE_UNKNOWN,
)
from homeassistant.core import CALLBACK_TYPE, Event, EventStateChangedData, HomeAssistant, State, callback
from homeassistant.util import dt as dt_util

from .api import distance_m
from .const import APPROACH_HOLD_MIN, APPROACH_MIN_DELTA_M
from .coordinator import StopCoordinator

_LOGGER = logging.getLogger(__name__)


def _coordinates(state: State) -> tuple[float, float] | None:
    try:
        return float(state.attributes["latitude"]), float(state.attributes["longitude"])
    except (KeyError, TypeError, ValueError):
        return None


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
        self.persons = set(persons)  # empty = all persons; explicit entries may also be device trackers
        # reference distance per (stop, person) used to detect approaching
        self._reference: dict[tuple[str, str], int] = {}

    @callback
    def async_start(self) -> CALLBACK_TYPE:
        for c in self.coordinators.values():
            self.evaluate(c, trigger=False)
        return self.hass.bus.async_listen(
            EVENT_STATE_CHANGED,
            self._handle_state_change,
            event_filter=self._is_followed_event,
        )

    @callback
    def _is_followed_event(self, data: EventStateChangedData) -> bool:
        entity_id = str(data["entity_id"])
        return entity_id in self.persons if self.persons else entity_id.startswith("person.")

    @callback
    def _handle_state_change(self, event: Event[EventStateChangedData]) -> None:
        old, new = event.data.get("old_state"), event.data.get("new_state")
        if old is not None and new is not None and old.state == new.state and _moved_less_than(old, new, 5):
            return  # e.g. only battery or accuracy changed
        for c in self.coordinators.values():
            self.evaluate(c, trigger=True)

    def _zone_position(self, state: State) -> tuple[float, float] | None:
        """Position of a person without GPS fix: the zone they are in (``home`` or the zone's name)."""
        if state.state == STATE_HOME:
            home = self.hass.states.get("zone.home")
            return (_coordinates(home) if home else None) or (self.hass.config.latitude, self.hass.config.longitude)
        wanted = state.state.casefold()
        for zone in self.hass.states.async_all("zone"):
            if str(zone.attributes.get(ATTR_FRIENDLY_NAME, "")).casefold() == wanted:
                return _coordinates(zone)
        return None

    def _positions(self) -> dict[str, tuple[float, float]]:
        """Where the followed persons/devices are.

        - ``unavailable`` / ``unknown``: not present (an old position must not count as being nearby).
        - GPS coordinates are used when there are any.
        - ``home`` or the name of a zone without coordinates (for example presence by router or Bluetooth):
          the position of that zone.
        """
        if self.persons:
            states = [st for entity_id in self.persons if (st := self.hass.states.get(entity_id)) is not None]
        else:
            states = self.hass.states.async_all("person")
        out: dict[str, tuple[float, float]] = {}
        for st in states:
            if st.state in (STATE_UNAVAILABLE, STATE_UNKNOWN):
                continue
            position = _coordinates(st) or self._zone_position(st)
            if position is not None:
                out[st.entity_id] = position
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
