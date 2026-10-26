"""Verversschema's per halte (pure logica, geen Home Assistant-imports).

Elk schema is een lijst van (uren, minuten)-combinaties waarop ververst wordt.
``None`` betekent: nooit automatisch verversen.
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


def fires_at(key: str, hour: int, minute: int) -> bool:
    spec = SCHEDULES.get(key)
    return bool(spec) and any(hour in h and minute in m for h, m in spec)
