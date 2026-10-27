"""Diagnostics download: configuration, health state and the raw OVapi responses (locations are redacted)."""
from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceEntry

from .const import DOMAIN
from .coordinator import DutchBusConfigEntry, StopCoordinator

TO_REDACT = {"lat", "lon", "latitude", "longitude", "Latitude", "Longitude", "person_entities", "persons"}


async def _raw_response(c: StopCoordinator) -> dict[str, Any]:
    """Fetch the raw OVapi answer now (not kept in memory between refreshes)."""
    try:
        results = await c.api.async_fetch_group(c.tpc_codes)
    except Exception as err:  # noqa: BLE001
        return {"error": str(err)}
    return {
        code: (res[code] if isinstance(res, dict) and code in res else str(res) if isinstance(res, BaseException) else None)
        for code, res in results.items()
    }


async def _stop_diagnostics(c: StopCoordinator) -> dict[str, Any]:
    view = c.view()
    return {
        "config": c.stop,
        "health": {
            "last_update_success": c.last_update_success,
            "consecutive_errors": c.consecutive_failures,
            "max_errors": c.max_errors,
            "paused": c.paused,
            "last_error": c.last_error,
            "data_age_min": c.age_minutes(),
            "stale": c.is_stale(),
            "sources_ok": c.data["sources_ok"] if c.data else None,
            "raw_passes": c.data["raw_passes"] if c.data else None,
        },
        "settings": {
            "schedule": c.schedule,
            "refresh_on_approach": c.refresh_on_approach,
            "approach_only": c.approach_only,
            "zone_interval_min": c.zone_interval_min,
            "zone_stop_after_min": c.zone_stop_after_min,
            "zone_cooldown_min": c.zone_cooldown_min,
            "proximity_radius": c.proximity_radius,
        },
        "proximity": {
            "nearby": len(c.nearby),
            "nearest_m": c.nearest_m,
            "approaching": c.approaching,
        },
        "departures_in_view": len(view),
        "first_departures": view[:3],
        "raw_response": await _raw_response(c),
    }


async def async_get_config_entry_diagnostics(hass: HomeAssistant, entry: DutchBusConfigEntry) -> dict[str, Any]:
    data = entry.runtime_data
    return async_redact_data(
        {
            "options": dict(entry.options),
            "api": {
                "consecutive_errors": data.api.consecutive_errors,
                "outage": data.api.outage,
                "paused_for_s": round(data.api.blocked_for),
                "spacing_s": data.api.spacing,
            },
            "ndov_file": await data.api.async_chb_info(),
            "stops": {key: await _stop_diagnostics(c) for key, c in data.coordinators.items()},
        },
        TO_REDACT,
    )


async def async_get_device_diagnostics(
    hass: HomeAssistant, entry: DutchBusConfigEntry, device: DeviceEntry
) -> dict[str, Any]:
    coordinators = entry.runtime_data.coordinators
    for domain, key in device.identifiers:
        if domain == DOMAIN and key in coordinators:
            return async_redact_data(await _stop_diagnostics(coordinators[key]), TO_REDACT)
    return {}
