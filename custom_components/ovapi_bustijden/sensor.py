"""Sensoren per halte (zelfde attributen als de yaml-sensoren, zodat bestaande kaarten blijven werken)."""
from __future__ import annotations

from datetime import datetime
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.const import EntityCategory, UnitOfLength
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import dt as dt_util

from .const import CONF_LINE_SENSORS, CONF_MAP_LOGO, DEFAULT_LINE_SENSORS, DOMAIN, NAMES_NL, OVAPI_BASE, STATIC_URL
from .coordinator import DutchBusConfigEntry, StopCoordinator
from .parser import STALE_TEXT, distance_m, next_departure_text, summarize


async def async_setup_entry(
    hass: HomeAssistant, entry: DutchBusConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    entities: list[DutchBusEntity] = []
    for c in entry.runtime_data.coordinators.values():
        entities += [NextDeparture(c), Departures(c), Lines(c), NearbyPersons(c), FetchedCount(c), LastUpdate(c), TpcCodes(c)]
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
    """Per lijn die op de halte rijdt een sensor; nieuwe lijnen krijgen vanzelf een sensor bij een volgende verversing."""
    known: set[str] = set()

    @callback
    def add_new() -> None:
        new = [line for line in {*c.known_lines(), *c.lines} if line not in known]
        if new:
            known.update(new)
            async_add_entities([LineDeparture(c, line) for line in sorted(new)])

    add_new()
    c.config_entry.async_on_unload(c.async_add_listener(add_new))


class DutchBusEntity(CoordinatorEntity[StopCoordinator]):
    _attr_has_entity_name = True

    @property
    def available(self) -> bool:
        """Na een mislukte verversing blijft de laatste data zichtbaar (en wordt na de timeout 'Data vernieuwen')."""
        return self.coordinator.data is not None or self.coordinator.last_update_success

    def __init__(self, coordinator: StopCoordinator, key: str) -> None:
        super().__init__(coordinator)
        self._attr_translation_key = key  # voor de vertaalde opties van o.a. de select
        self._attr_name = NAMES_NL.get(key)  # namen (en entity_id's) altijd Nederlands
        # unique_id-formaat ongewijzigd t.o.v. 0.1.x
        self._attr_unique_id = f"{coordinator.key}_{key}"
        loc = coordinator.location
        # 'Bezoeken' op de apparaatpagina opent de halte op een kaart
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
            model="OVapi bussen en halteinformatie",
            configuration_url=url,
        )


class MinuteTickerMixin:
    """Schrijf elke minuut de status opnieuw (over_min, verdwijnen van vertrokken bussen).

    De yaml-sensoren deden dit via ``now()`` in de template; hier gebeurt het zonder nieuwe
    OVapi-aanvraag, ook als polling uit staat. De halte-coordinator heeft daarvoor één gedeelde timer.
    """

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()  # type: ignore[misc]
        self.async_on_remove(  # type: ignore[attr-defined]
            self.coordinator.async_add_tick_listener(self._handle_tick)  # type: ignore[attr-defined]
        )

    @callback
    def _handle_tick(self) -> None:
        self.async_write_ha_state()  # type: ignore[attr-defined]


class NextDeparture(MinuteTickerMixin, DutchBusEntity, SensorEntity):
    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _unrecorded_attributes = frozenset({"lijnnaam", "bestemming"})

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
            "lijnnaam": d["lijnnaam"],
            "richting": d["richting"],
            "lijn": d["lijn"],
            "bestemming": d["bestemming"],
            "gepland": d["gepland"],
            "over_min": d["over_min"],
            "vertraging_min": d["vertraging_min"],
            "status": d["status"],
            "tpc": d["tpc"],
        }


class Lines(MinuteTickerMixin, DutchBusEntity, SensorEntity):
    """Lijnen die nu in de gegevens voorkomen: '34, 73, 350'. Handig om te zien dat een lijn niet rijdt."""

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
            return STALE_TEXT
        lines = self.coordinator.known_lines()
        return ", ".join(lines) if lines else "Geen"

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        lines = self.coordinator.known_lines()
        return {"lijnen": lines, "aantal": len(lines)}  # lijst voor code/automatiseringen


class NearbyPersons(DutchBusEntity, SensorEntity):
    """'Geen' of de namen van de gevolgde personen binnen de zone-radius van de halte."""


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
        return ", ".join(names) if names else "Geen"

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        c = self.coordinator
        return {
            "aantal": len(c.nearby),
            "personen": self._names(),
            "person_entiteiten": sorted(c.nearby),
            "dichtstbij_m": c.nearest_m,
            "radius_m": c.proximity_radius,
        }


class Departures(MinuteTickerMixin, DutchBusEntity, SensorEntity):
    """State '14:05 (Lijn 74)' / 'Geen bussen' + attribuut ``vertrekken`` (zoals de yaml-sensor)."""

    # niet in de recorder-database bewaren (OVapi-voorwaarden: realtime data niet langer opslaan)
    _unrecorded_attributes = frozenset({"vertrekken", "berichten"})

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
            return STALE_TEXT
        return summarize(self.coordinator.view())

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        c = self.coordinator
        fetched = c.data["fetched"] if c.data else None
        return {
            "bijgewerkt": dt_util.as_local(fetched).replace(microsecond=0).isoformat()
            if fetched
            else None,
            "data_leeftijd_min": c.age_minutes(),
            "vertrekken": c.view(),
            "berichten": c.data["messages"] if c.data else [],
            "bronnen": c.tpc_codes,
            "lijnfilter": c.lines,
            "gedeeltelijk": bool(c.data and c.data["partial"]),
            "mislukte_pogingen": c.consecutive_failures,
            "laatste_fout": c.last_error,
        }


class LineDeparture(MinuteTickerMixin, DutchBusEntity, SensorEntity):
    """Volgende vertrek van één lijn op deze halte (optioneel, aan te zetten onder Configureren → Instellingen)."""

    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_icon = "mdi:bus"
    _unrecorded_attributes = frozenset({"volgende", "bestemming"})

    def __init__(self, coordinator: StopCoordinator, line: str) -> None:
        super().__init__(coordinator, f"line_{line}")
        self._line = line
        self._attr_translation_key = None
        self._attr_name = f"Lijn {line}"

    def _items(self) -> list[dict[str, Any]]:
        if self.coordinator.is_stale():
            return []
        return [d for d in self.coordinator.view_full() if d["lijn"] == self._line]

    @property
    def native_value(self) -> datetime | None:
        items = self._items()
        return dt_util.utc_from_timestamp(items[0]["exp"]) if items else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        items = self._items()
        if not items:
            return {"lijn": self._line, "status": "Geen bussen"}
        d = items[0]
        return {
            "lijn": self._line,
            "bestemming": d["bestemming"],
            "gepland": d["gepland"],
            "over_min": d["over_min"],
            "vertraging_min": d["vertraging_min"],
            "status": d["status"],
            "volgende": [{"tijd": x["tijd"], "over_min": x["over_min"], "bestemming": x["bestemming"]} for x in items[1:4]],
        }


class StopOnMap(MinuteTickerMixin, DutchBusEntity, SensorEntity):
    """Halte op de kaart: de latitude/longitude-attributen tekenen de pin; de status is de eerstvolgende bus."""

    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _unrecorded_attributes = frozenset({"latitude", "longitude"})

    def __init__(self, coordinator: StopCoordinator) -> None:
        super().__init__(coordinator, "location")
        self._attr_name = None  # naam = apparaatnaam (de haltenaam die de gebruiker koos)

    @property
    def available(self) -> bool:
        return self.coordinator.location is not None

    @property
    def native_value(self) -> str:
        if self.coordinator.needs_refresh():
            return STALE_TEXT
        return next_departure_text(self.coordinator.view())

    @property
    def icon(self) -> str:
        return "mdi:map-marker-off" if self.coordinator.needs_refresh() else "mdi:bus-stop"

    @property
    def entity_picture(self) -> str | None:
        """Halte-marker op de kaart (uit te zetten in de opties); bij verouderde data valt het doorgestreepte icoon terug."""
        if self.coordinator.needs_refresh():
            return f"{STATIC_URL}/halte_onbekend.png"  # geen, verouderde of lege busgegevens: grijze halte met vraagteken
        if self.coordinator.config_entry.options.get(CONF_MAP_LOGO, True):
            return f"{STATIC_URL}/halte_marker.png"
        return None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        loc = self.coordinator.location
        return {"latitude": loc[0], "longitude": loc[1]} if loc else {}


class FetchedCount(MinuteTickerMixin, DutchBusEntity, SensorEntity):
    """Aantal vertrektijden dat nog in de toekomst ligt; loopt elke minuut terug en bij 0 heeft de halte geen data meer."""

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
            "opgehaald": len(d["departures"]),  # aantal vertrekken in de laatste API-aanvraag
            "passes_in_antwoord": d["raw_passes"],
            "geannuleerd": cancelled,
            "bronnen_ok": d["sources_ok"],
            "bronnen_totaal": len(self.coordinator.tpc_codes),
        }


class LastUpdate(DutchBusEntity, SensorEntity):
    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: StopCoordinator) -> None:
        super().__init__(coordinator, "last_update")

    @property
    def native_value(self) -> datetime | None:
        return self.coordinator.data["fetched"] if self.coordinator.data else None


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
