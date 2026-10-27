"""Refresh schedules per stop (pure logic, no Home Assistant imports).

Each schedule is a list of (hours, minutes) combinations at which to refresh.
``None`` means: never refresh automatically.
"""
from __future__ import annotations

ALL_HOURS = list(range(24))
EVERY_5 = list(range(0, 60, 5))
EVERY_30 = [0, 30]
MORNING = [6, 7, 8]   # 06:00 t/m 09:00 (09:00 zelf staat apart)
EVENING = [16, 17, 18]  # 16:00 t/m 19:00

Spec = list[tuple[list[int], list[int]]]


def _window(hours: list[int], minutes: list[int]) -> Spec:
    """Venster inclusief het eindpunt (bijv. 09:00)."""
    return [(hours, minutes), ([hours[-1] + 1], [0])]


SCHEDULES: dict[str, Spec | None] = {
    "never": None,
    "daily": [([6], [0])],
    "hourly": [(ALL_HOURS, [0])],
    "every_30_min_06_09": _window(MORNING, EVERY_30),
    "every_30_min_16_19": _window(EVENING, EVERY_30),
    "every_5_min": [(ALL_HOURS, EVERY_5)],
    "every_5_min_rush": _window(MORNING, EVERY_5) + _window(EVENING, EVERY_5),
}
DEFAULT_SCHEDULE = "never"  # alleen Vernieuwen-knop / nabijheid / eigen automatisering


# Refresh interval while someone is in the zone (minutes); None = on entry only
ZONE_INTERVALS: dict[str, float | None] = {
    "on_entry": None,
    "every_2_min": 2,
    "every_5_min": 5,
    "every_10_min": 10,
    "every_15_min": 15,
}
DEFAULT_ZONE_INTERVAL = "every_5_min"

# How long one visit keeps refreshing (minutes); None = unlimited
ZONE_STOP_AFTER: dict[str, int | None] = {
    "10_min": 10,
    "30_min": 30,
    "60_min": 60,
    "120_min": 120,
    "unlimited": None,
}
DEFAULT_ZONE_STOP_AFTER = "30_min"


def nearest_option(options: dict[str, int | float | None], minutes: float | None) -> str | None:
    """Closest option key for a number of minutes (used to migrate the old number entities)."""
    if minutes is None:
        return None
    numeric = {k: v for k, v in options.items() if v is not None}
    return min(numeric, key=lambda k: abs(numeric[k] - minutes)) if numeric else None


def fires_at(key: str, hour: int, minute: int) -> bool:
    spec = SCHEDULES.get(key)
    if not spec:
        return False
    return any(hour in h and minute in m for h, m in spec)
