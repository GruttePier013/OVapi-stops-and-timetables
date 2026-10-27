"""Sensors per stop (same attributes as the YAML sensors, so existing cards keep working)."""
from __future__ import annotations

from datetime import datetime
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.const import MATCH_ALL, EntityCategory, UnitOfLength
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import dt as dt_util

from .const import (
    CONF_LINE_SENSORS,
    CONF_MAP_LOGO,
    CONF_MINUTE_UPDATES,
    DEFAULT_LINE_SENSORS,
    DOMAIN,
    OVAPI_BASE,
    STATIC_URL,
)
from .coordinator import DutchBusConfigEntry, StopCoordinator
from .parser import distance_m, next_departure_text, summarize


async def async_setup_entry(
    hass: HomeAssistant, entry: DutchBusConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    entities: list[DutchBusEntity] = []
    for c in entry.runtime_data.coordinators.values():
        entities += [
            NextDeparture(c),
            Departures(c),
            Lines(c),
            NearbyPersons(c),
            DistanceToPerson(c),
            FetchedCount(c),
            LastUpdate(c),
            TpcCodes(c),
            ErrorCount(c),
        ]
        if c.location is not None:
            entities.append(StopOnMap(c))
        if entry.options.get(CONF_LINE_SENSORS, DEFAULT_LINE_SENSORS):
            _setup_line_sensors(c, async_add_entities)
        if c.location is not None:
            entities.append(StopDistance(c))
        if c.stop.get("stop_code"):
            entities.append(GtfsStopCode(c))
    async_add_entities(entities)


def _setup_line_sensors(c: StopCoordinator, async_add_entities: AddEntitiesCallback) -> None:
    """One sensor per line serving the stop; new lines get a sensor automatically on a later refresh."""
    known: set[str] = set()

    @callback
    def add_new() -> None:
        new = [line for line in {*c.known_lines(), *c.lines} if line not in known]
        if new:
            known.update(new)
            async_add_entities([LineDeparture(c, line) for line in sorted(new)])

    add_new()
    c.entry.async_on_unload(c.async_add_listener(add_new))


class DutchBusEntity(CoordinatorEntity[StopCoordinator]):
    _attr_has_entity_name = True
    # Attributes are informational (departure lists, positions): keep them out of the recorder database.
    # The states themselves are still recorded and cards read the attributes from the state machine.
    _unrecorded_attributes = frozenset({MATCH_ALL})

    @property
    def available(self) -> bool:
        """After a failed refresh the last data stays visible (and becomes 'Data vernieuwen' after the timeout)."""
        return self.coordinator.data is not None or self.coordinator.last_update_success

    def __init__(self, coordinator: StopCoordinator, key: str) -> None:
        super().__init__(coordinator)
        self._attr_translation_key = key  # for the translated options of e.g. the select
        # unique_id-formaat ongewijzigd t.o.v. 0.1.x
        self._attr_unique_id = f"{coordinator.key}_{key}"
        loc = coordinator.location
        # 'Visit' on the device page opens the stop on a map
        url = (
            f"https://www.openstreetmap.org/?mlat={loc[0]:.6f}&mlon={loc[1]:.6f}"
            f"#map=18/{loc[0]:.6f}/{loc[1]:.6f}"
            if loc
            else f"{OVAPI_BASE}/tpc/{','.join(coordinator.tpc_codes)}"
        )
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, coordinator.key)},
            name=coordinator.stop["name"],
            manufacturer="OVapi en NDOV",
            model="OVapi bus departures",
            configuration_url=url,
        )


class MinuteTickerMixin:
    """Rewrite the state every minute (minutes-until, disappearance of departed buses).

    The YAML sensors did this via ``now()`` in the template; here it happens without a new
    OVapi request, even when polling is off. The stop coordinator has one shared timer for this.
    """

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()  # type: ignore[misc]
        self.async_on_remove(  # type: ignore[attr-defined]
            self.coordinator.async_add_tick_listener(self._handle_tick)  # type: ignore[attr-defined]
        )
        self._tick_signature = self._signature()

    _tick_signature: tuple[Any, ...] | None = None

    def _signature(self) -> tuple[Any, ...]:
        c = self.coordinator  # type: ignore[attr-defined]
        view = c.view()
        return (id(c.data), len(view), view[0]["exp"] if view else None)

    @callback
    def _handle_tick(self) -> None:
        if not self.coordinator.entry.options.get(CONF_MINUTE_UPDATES, True):  # type: ignore[attr-defined]
            # only write when something visible changes: the first bus, the number of buses or the data itself
            signature = self._signature()
            if signature == self._tick_signature:
                return
            self._tick_signature = signature
        self.async_write_ha_state()  # type: ignore[attr-defined]


class NextDeparture(MinuteTickerMixin, DutchBusEntity, SensorEntity):
    _attr_device_class = SensorDeviceClass.TIMESTAMP

    def __init__(self, coordinator: StopCoordinator) -> None:
        super().__init__(coordinator, "next_departure")

    @property
    def native_value(self) -> datetime | None:
        view = self.coordinator.view()
        return dt_util.utc_from_timestamp(view[0]["exp"]) if view else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        view = self.coordinator.view()
        if not view:
            return {}
        d = view[0]
        return {
            "line_name": d["line_name"],
            "direction": d["direction"],
            "line": d["line"],
            "destination": d["destination"],
            "planned": d["planned"],
            "minutes_until": d["minutes_until"],
            "delay_min": d["delay_min"],
            "status": d["status"],
            "tpc": d["tpc"],
        }


class Lines(MinuteTickerMixin, DutchBusEntity, SensorEntity):
    """Lines currently in the data: '34, 73, 350'. Handy to see that a line is not running."""

    def __init__(self, coordinator: StopCoordinator) -> None:
        super().__init__(coordinator, "lines")

    @property
    def icon(self) -> str:
        return "mdi:sync-off" if self.coordinator.is_stale() else "mdi:bus-multiple"

    @property
    def native_value(self) -> str | None:
        if not self.coordinator.data:
            return None
        if self.coordinator.is_stale():
            return self.coordinator.t("refresh_data")
        lines = self.coordinator.known_lines()
        return ", ".join(lines) if lines else self.coordinator.t("none")

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        lines = self.coordinator.known_lines()
        return {"lines": lines, "count": len(lines)}  # lijst voor code/automatiseringen


class NearbyPersons(DutchBusEntity, SensorEntity):
    """'Geen' or the names of the tracked persons within the zone radius of the stop."""


    def __init__(self, coordinator: StopCoordinator) -> None:
        super().__init__(coordinator, "nearby_count")

    @property
    def available(self) -> bool:
        return True  # los van of OVapi antwoordt

    def _names(self) -> list[str]:
        names = []
        for entity_id in self.coordinator.nearby:
            state = self.hass.states.get(entity_id)
            names.append(str(state.name) if state else entity_id)
        return sorted(names, key=str.casefold)

    @property
    def native_value(self) -> str:
        names = self._names()
        return ", ".join(names) if names else self.coordinator.t("none")

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        c = self.coordinator
        return {
            "count": len(c.nearby),
            "persons": self._names(),
            "person_entities": sorted(c.nearby),
            "nearest_m": c.nearest_m,
            "radius_m": c.proximity_radius,
        }


class DistanceToPerson(DutchBusEntity, SensorEntity):
    """Distance from the stop to the nearest followed person (rounded), also when nobody is inside the zone."""

    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_device_class = SensorDeviceClass.DISTANCE
    _attr_native_unit_of_measurement = UnitOfLength.METERS

    def __init__(self, coordinator: StopCoordinator) -> None:
        super().__init__(coordinator, "distance_to_person")

    @property
    def available(self) -> bool:
        return True

    @property
    def native_value(self) -> int | None:
        return self.coordinator.nearest_m

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        c = self.coordinator
        entity_id = c.nearest_entity
        state = self.hass.states.get(entity_id) if entity_id else None
        return {
            "person": (state.name if state else entity_id),
            "in_zone": bool(c.nearby),
            "approaching": c.approaching,
        }


class Departures(MinuteTickerMixin, DutchBusEntity, SensorEntity):
    """State '14:05 (Lijn 74)' / 'Geen bussen' + attribuut ``vertrekken`` (zoals de yaml-sensor)."""

    def __init__(self, coordinator: StopCoordinator) -> None:
        super().__init__(coordinator, "departures")

    @property
    def icon(self) -> str:
        return "mdi:sync-off" if self.coordinator.is_stale() else "mdi:bus-clock"

    @property
    def native_value(self) -> str | None:
        if not self.coordinator.data:
            return None
        if self.coordinator.is_stale():
            return self.coordinator.t("refresh_data")
        return summarize(self.coordinator.view(), self.coordinator.t)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        c = self.coordinator
        fetched = c.data["fetched"] if c.data else None
        return {
            "updated": dt_util.as_local(fetched).replace(microsecond=0).isoformat()
            if fetched
            else None,
            "data_age_min": c.age_minutes(),
            "departures": c.view(),
            "messages": c.data["messages"] if c.data else [],
            "sources": c.tpc_codes,
            "line_filter": c.lines,
            "partial": bool(c.data and c.data["partial"]),
            "missing_codes": c.data.get("missing", []) if c.data else [],
            "failed_attempts": c.consecutive_failures,
            "last_error": c.last_error,
        }


class LineDeparture(MinuteTickerMixin, DutchBusEntity, SensorEntity):
    """Next departure of one line at this stop (optional, enable under Configure → Settings)."""

    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_icon = "mdi:bus"

    def __init__(self, coordinator: StopCoordinator, line: str) -> None:
        super().__init__(coordinator, f"line_{line}")
        self._line = line
        self._attr_translation_key = "line"
        self._attr_translation_placeholders = {"line": line}

    def _items(self) -> list[dict[str, Any]]:
        if self.coordinator.is_stale():
            return []
        return [d for d in self.coordinator.view_full() if d["line"] == self._line]

    @property
    def native_value(self) -> datetime | None:
        items = self._items()
        return dt_util.utc_from_timestamp(items[0]["exp"]) if items else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        items = self._items()
        if not items:
            return {"line": self._line, "status": self.coordinator.t("no_buses")}
        d = items[0]
        return {
            "line": self._line,
            "destination": d["destination"],
            "planned": d["planned"],
            "minutes_until": d["minutes_until"],
            "delay_min": d["delay_min"],
            "status": d["status"],
            "upcoming": [
                {
                    "time": x["time"],
                    "planned": x["planned"],
                    "minutes_until": x["minutes_until"],
                    "delay_min": x["delay_min"],
                    "destination": x["destination"],
                    "status": x["status"],
                }
                for x in items[1:]
            ],
        }


class StopOnMap(MinuteTickerMixin, DutchBusEntity, SensorEntity):
    """Stop on the map: the latitude/longitude attributes draw the pin; the state is the next bus."""

    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: StopCoordinator) -> None:
        super().__init__(coordinator, "location")
        self._attr_name = None  # name = device name (the stop name the user chose)

    @property
    def available(self) -> bool:
        return self.coordinator.location is not None

    @property
    def native_value(self) -> str:
        if self.coordinator.needs_refresh():
            return self.coordinator.t("refresh_data")
        return next_departure_text(self.coordinator.view(), self.coordinator.t)

    @property
    def icon(self) -> str:
        return "mdi:map-marker-off" if self.coordinator.needs_refresh() else "mdi:bus-stop"

    @property
    def entity_picture(self) -> str | None:
        """Stop marker on the map (can be disabled in the options); with outdated data the struck-through icon is used."""
        if self.coordinator.needs_refresh():
            return f"{STATIC_URL}/stop_unknown.png"  # no, outdated or empty bus data: grey stop with question mark
        if self.coordinator.entry.options.get(CONF_MAP_LOGO, True):
            return f"{STATIC_URL}/stop_marker.png"
        return None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        loc = self.coordinator.marker_location
        return {"latitude": loc[0], "longitude": loc[1]} if loc else {}


class FetchedCount(MinuteTickerMixin, DutchBusEntity, SensorEntity):
    """Number of departure times still in the future; counts down every minute and at 0 the stop has no data left."""

    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, coordinator: StopCoordinator) -> None:
        super().__init__(coordinator, "fetched_departures")

    @property
    def native_value(self) -> int | None:
        return len(self.coordinator.view_full()) if self.coordinator.data else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        d = self.coordinator.data
        if not d:
            return {}
        cancelled = sum(1 for x in d["departures"] if x["status"] == "CANCEL")
        return {
            "fetched": d.get("fetched_count", len(d["departures"])),  # aantal vertrekken in de laatste API-aanvraag
            "passes_in_response": d["raw_passes"],
            "cancelled": cancelled,
            "sources_ok": d["sources_ok"],
            "sources_total": len(self.coordinator.tpc_codes),
        }


class LastUpdate(DutchBusEntity, SensorEntity):
    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: StopCoordinator) -> None:
        super().__init__(coordinator, "last_update")

    @property
    def native_value(self) -> datetime | None:
        return self.coordinator.data["fetched"] if self.coordinator.data else None


class ErrorCount(DutchBusEntity, SensorEntity):
    """Consecutive errors of this stop; the stop pauses automatic refreshes at the maximum."""

    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_entity_registry_enabled_default = False
    _attr_icon = "mdi:alert-circle-outline"

    def __init__(self, coordinator: StopCoordinator) -> None:
        super().__init__(coordinator, "error_count")

    @property
    def available(self) -> bool:
        return True

    @property
    def native_value(self) -> int:
        return self.coordinator.consecutive_failures

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        c = self.coordinator
        return {
            "paused": c.paused,
            "max_errors": c.max_errors,
            "last_error": c.last_error,
            "api_consecutive_errors": c.api.consecutive_errors,
            "api_paused_for_s": round(c.api.blocked_for),
        }


class _Metadata(DutchBusEntity, SensorEntity):
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_entity_registry_enabled_default = False


class TpcCodes(_Metadata):
    def __init__(self, coordinator: StopCoordinator) -> None:
        super().__init__(coordinator, "tpc_code")

    @property
    def native_value(self) -> str:
        return ", ".join(self.coordinator.tpc_codes)


class GtfsStopCode(_Metadata):
    def __init__(self, coordinator: StopCoordinator) -> None:
        super().__init__(coordinator, "stop_code")

    @property
    def native_value(self) -> str | None:
        return self.coordinator.stop.get("stop_code")


class StopDistance(_Metadata):
    _attr_native_unit_of_measurement = UnitOfLength.METERS
    _attr_device_class = SensorDeviceClass.DISTANCE

    def __init__(self, coordinator: StopCoordinator) -> None:
        super().__init__(coordinator, "distance_m")

    @property
    def native_value(self) -> int | None:
        loc = self.coordinator.location
        if loc is None:
            return None
        return round(distance_m(loc[0], loc[1], self.hass.config.latitude, self.hass.config.longitude))
