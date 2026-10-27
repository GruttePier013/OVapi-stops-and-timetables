"""Stop configuration helpers (no Home Assistant imports)."""
from __future__ import annotations

from typing import Any

CONF_STOPS = "stops"


def normalize_stop(stop: dict[str, Any]) -> dict[str, Any] | None:
    """Accept both the new format (tpc_codes) and the 0.1.x format (tpc_code)."""
    codes = stop.get("tpc_codes")
    if not codes and stop.get("tpc_code"):
        codes = [stop["tpc_code"]]
    codes = [str(c) for c in (codes or []) if str(c)]
    if not codes:
        return None
    name = stop.get("name") or stop.get("stop_name") or stop.get("ovapi_name") or "Bushalte"
    lines = [str(line) for line in (stop.get("lines") or [])]
    destinations = [str(x) for x in (stop.get("destinations") or []) if str(x).strip()]
    return {**stop, "name": name, "tpc_codes": codes, "lines": lines, "destinations": destinations}


def stop_key(stop: dict[str, Any]) -> str:
    """Stable id of a stop = first TPC code (same as the device id in 0.1.x)."""
    return stop["tpc_codes"][0]


def get_stops(entry: Any) -> list[dict[str, Any]]:
    raw = entry.options.get(CONF_STOPS, entry.data.get(CONF_STOPS, []))
    stops = [normalize_stop(s) for s in raw if isinstance(s, dict)]
    return [s for s in stops if s]
