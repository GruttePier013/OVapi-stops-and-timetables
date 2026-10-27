"""Default values for new stops and error protection (read from the options, with fallbacks)."""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from . import const
from .const import (
    CONF_API_MAX_ERRORS,
    CONF_API_SPACING,
    CONF_DEFAULT_APPROACH_ONLY,
    CONF_DEFAULT_RADIUS,
    CONF_DEFAULT_REFRESH_ON_ZONE,
    CONF_DEFAULT_SCHEDULE,
    CONF_DEFAULT_ZONE_INTERVAL,
    CONF_DEFAULT_ZONE_STOP_AFTER,
    CONF_MAX_ERRORS,
    DEFAULT_PROXIMITY_RADIUS,
    MAX_RADIUS,
    MIN_RADIUS,
)
from .schedules import (
    DEFAULT_SCHEDULE,
    DEFAULT_ZONE_INTERVAL,
    DEFAULT_ZONE_STOP_AFTER,
    SCHEDULES,
    ZONE_INTERVALS,
    ZONE_STOP_AFTER,
)


def get_defaults(options: Mapping[str, Any] | None) -> dict[str, Any]:
    """Always valid values, also when options are missing or invalid."""
    o = options or {}
    schedule = o.get(CONF_DEFAULT_SCHEDULE, DEFAULT_SCHEDULE)
    interval = o.get(CONF_DEFAULT_ZONE_INTERVAL, DEFAULT_ZONE_INTERVAL)
    stop_after = o.get(CONF_DEFAULT_ZONE_STOP_AFTER, DEFAULT_ZONE_STOP_AFTER)
    try:
        radius = int(o.get(CONF_DEFAULT_RADIUS, DEFAULT_PROXIMITY_RADIUS))
    except (TypeError, ValueError):
        radius = DEFAULT_PROXIMITY_RADIUS
    return {
        CONF_DEFAULT_SCHEDULE: schedule if schedule in SCHEDULES else DEFAULT_SCHEDULE,
        CONF_DEFAULT_RADIUS: min(max(radius, MIN_RADIUS), MAX_RADIUS),
        CONF_DEFAULT_REFRESH_ON_ZONE: bool(o.get(CONF_DEFAULT_REFRESH_ON_ZONE, True)),
        CONF_DEFAULT_ZONE_INTERVAL: interval if interval in ZONE_INTERVALS else DEFAULT_ZONE_INTERVAL,
        CONF_DEFAULT_ZONE_STOP_AFTER: stop_after if stop_after in ZONE_STOP_AFTER else DEFAULT_ZONE_STOP_AFTER,
        CONF_DEFAULT_APPROACH_ONLY: bool(o.get(CONF_DEFAULT_APPROACH_ONLY, True)),
    }


def get_protection(options: Mapping[str, Any] | None) -> dict[str, Any]:
    """Error protection settings (read from ``const`` at call time so tests can patch the defaults)."""
    o = options or {}

    def number(key: str, default: float, minimum: float) -> float:
        try:
            return max(float(o.get(key, default)), minimum)
        except (TypeError, ValueError):
            return default

    return {
        CONF_MAX_ERRORS: int(number(CONF_MAX_ERRORS, const.DEFAULT_MAX_ERRORS, 1)),
        CONF_API_MAX_ERRORS: int(number(CONF_API_MAX_ERRORS, const.DEFAULT_API_MAX_ERRORS, 1)),
        CONF_API_SPACING: number(CONF_API_SPACING, const.DEFAULT_API_SPACING, 0),
    }
