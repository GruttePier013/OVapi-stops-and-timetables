"""Actions: ``refresh`` (update stops) and ``get_departures`` (answer with the upcoming departures)."""
from __future__ import annotations

import asyncio
from typing import Any, cast

import voluptuous as vol
from homeassistant.const import ATTR_ENTITY_ID, ENTITY_MATCH_ALL, ENTITY_MATCH_NONE
from homeassistant.core import HomeAssistant, ServiceCall, ServiceResponse, SupportsResponse
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.target import TargetSelection, async_extract_referenced_entity_ids
from homeassistant.util import dt as dt_util

from .const import DOMAIN, SERVICE_GET_DEPARTURES, SERVICE_REFRESH
from .coordinator import StopCoordinator
from .parser import distance_m

ATTR_ZONE = "zone"
ATTR_LINE = "line"
ATTR_LIMIT = "limit"
ATTR_LIVE_ONLY = "live_only"
MAX_LIMIT = 100


def _lines(value: list[str]) -> list[str]:
    """Accept 'line: 74', 'line: "74, 34"' and 'line: [74, 34]'."""
    return [part.strip() for item in value for part in item.split(",") if part.strip()]


REFRESH_SCHEMA = vol.Schema(cv.TARGET_SERVICE_FIELDS)
GET_DEPARTURES_SCHEMA = vol.Schema(
    {
        **cv.TARGET_SERVICE_FIELDS,
        vol.Optional(ATTR_ZONE): cv.entity_id,
        vol.Optional(ATTR_LINE): vol.All(cv.ensure_list, [cv.string], _lines),
        vol.Optional(ATTR_LIMIT, default=10): vol.All(vol.Coerce(int), vol.Range(min=1, max=MAX_LIMIT)),
        vol.Optional(ATTR_LIVE_ONLY, default=False): cv.boolean,
    }
)


def _all_coordinators(hass: HomeAssistant) -> dict[str, StopCoordinator]:
    coordinators: dict[str, StopCoordinator] = {}
    for entry in hass.config_entries.async_loaded_entries(DOMAIN):
        coordinators.update(entry.runtime_data.coordinators)
    return coordinators


def _targeted(hass: HomeAssistant, call: ServiceCall, coordinators: dict[str, StopCoordinator]) -> tuple[
    dict[str, StopCoordinator], bool
]:
    """The stops the call targets (devices, entities, areas, ``entity_id: all``), and whether it targets anything."""
    entity_ids = cv.ensure_list(call.data.get(ATTR_ENTITY_ID) or [])
    if ENTITY_MATCH_ALL in entity_ids:
        return coordinators, True
    if ENTITY_MATCH_NONE in entity_ids:
        return {}, True
    selected = async_extract_referenced_entity_ids(hass, TargetSelection(call.data))
    if not (selected.referenced or selected.indirectly_referenced or selected.referenced_devices):
        return {}, False
    dev_reg = dr.async_get(hass)
    ent_reg = er.async_get(hass)
    device_ids = set(selected.referenced_devices)
    for entity_id in selected.referenced | selected.indirectly_referenced:
        if (reg_entry := ent_reg.async_get(entity_id)) and reg_entry.device_id:
            device_ids.add(reg_entry.device_id)
    keys = {
        ident[1]
        for device_id in device_ids
        if (device := dev_reg.async_get(device_id))
        for ident in device.identifiers
        if ident[0] == DOMAIN
    }
    return {k: c for k, c in coordinators.items() if k in keys}, True


def _stops_in_zone(hass: HomeAssistant, zone_id: str, coordinators: dict[str, StopCoordinator]) -> dict[str, StopCoordinator]:
    zone = hass.states.get(zone_id)
    lat = zone.attributes.get("latitude") if zone else None
    lon = zone.attributes.get("longitude") if zone else None
    if zone is None or zone.domain != "zone" or lat is None or lon is None:
        raise ServiceValidationError(
            translation_domain=DOMAIN, translation_key="unknown_zone", translation_placeholders={"zone": zone_id}
        )
    radius = float(zone.attributes.get("radius") or 0)
    return {
        key: c
        for key, c in coordinators.items()
        if (loc := c.marker_location) and distance_m(float(lat), float(lon), loc[0], loc[1]) <= radius
    }


def _shape(d: dict[str, Any], stop: str) -> dict[str, Any]:
    return {
        "stop": stop,
        "line": d["line"],
        "destination": d["destination"],
        "time": d["time"],
        "planned": d["planned"],
        "expected": dt_util.utc_from_timestamp(d["exp"]).isoformat(),
        "minutes_until": d["minutes_until"],
        "delay_min": d["delay_min"],
        "status": d["status"],
        "live": d["live"],
    }


def async_register_services(hass: HomeAssistant) -> None:
    async def _refresh(call: ServiceCall) -> None:
        """No target = all stops, otherwise only the targeted stops (devices, entities, areas, ``entity_id: all``)."""
        coordinators = _all_coordinators(hass)
        selected, targeted = _targeted(hass, call, coordinators)
        if targeted:
            coordinators = selected
        await asyncio.gather(*(c.async_manual_refresh() for c in coordinators.values()), return_exceptions=True)

    async def _get_departures(call: ServiceCall) -> ServiceResponse:
        """Upcoming departures of the targeted stops and/or the stops inside a zone; no request to OVapi."""
        coordinators = _all_coordinators(hass)
        stops, targeted = _targeted(hass, call, coordinators)
        if zone_id := call.data.get(ATTR_ZONE):
            stops = {**stops, **_stops_in_zone(hass, zone_id, coordinators)}
            targeted = True
        if not targeted:
            raise ServiceValidationError(translation_domain=DOMAIN, translation_key="no_stop_selected")
        wanted = {line.casefold() for line in call.data.get(ATTR_LINE, [])}
        limit: int = call.data[ATTR_LIMIT]
        per_stop: list[dict[str, Any]] = []
        combined: list[dict[str, Any]] = []
        for c in sorted(stops.values(), key=lambda x: x.stop["name"].casefold()):
            view = [] if c.is_stale() else c.view()
            if call.data[ATTR_LIVE_ONLY]:
                view = [d for d in view if d["live"]]
            if wanted:
                view = [d for d in view if d["line"].casefold() in wanted]
            items = [_shape(d, c.stop["name"]) for d in view[:limit]]
            combined.extend(items)
            per_stop.append(
                {
                    "stop": c.stop["name"],
                    "stop_key": c.key,
                    "fetched": c.data["fetched"].isoformat() if c.data else None,
                    "stale": c.is_stale(),
                    "departures": items,
                }
            )
        combined.sort(key=lambda x: x["expected"])
        return {"departures": cast(Any, combined[:limit]), "stops": cast(Any, per_stop)}

    hass.services.async_register(DOMAIN, SERVICE_REFRESH, _refresh, schema=REFRESH_SCHEMA)
    hass.services.async_register(
        DOMAIN,
        SERVICE_GET_DEPARTURES,
        _get_departures,
        schema=GET_DEPARTURES_SCHEMA,
        supports_response=SupportsResponse.ONLY,
    )
