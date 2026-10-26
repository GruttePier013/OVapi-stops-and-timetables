"""Diagnostische gegevens voor bugmeldingen (Instellingen → Apparaten & services → Diagnostische gegevens downloaden)."""
from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.core import HomeAssistant

from .coordinator import DutchBusConfigEntry

TO_REDACT = {"lat", "lon", "latitude", "longitude", "person_entiteiten", "personen"}


async def async_get_config_entry_diagnostics(hass: HomeAssistant, entry: DutchBusConfigEntry) -> dict[str, Any]:
    data = entry.runtime_data
    stops: dict[str, Any] = {}
    for key, c in data.coordinators.items():
        view = c.view()
        stops[key] = {
            "stop": c.stop,
            "last_update_success": c.last_update_success,
            "consecutive_failures": c.consecutive_failures,
            "last_error": c.last_error,
            "age_minutes": c.age_minutes(),
            "stale": c.is_stale(),
            "sources_ok": c.data["sources_ok"] if c.data else None,
            "raw_passes": c.data["raw_passes"] if c.data else None,
            "departures_in_view": len(view),
            "first_departures": view[:3],
            "schedule": c.schedule,
            "zone": {
                "radius_m": c.proximity_radius,
                "refresh_in_zone": c.refresh_on_approach,
                "stop_after_min": c.zone_stop_after,
                "interval_s": c._zone_interval(),
                "nearby_count": len(c.nearby),
            },
        }
    return async_redact_data(
        {
            "options": dict(entry.options),
            "ndov_file": await data.api.async_chb_info(),
            "stops": stops,
        },
        TO_REDACT,
    )
