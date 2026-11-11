"""Persistent bookkeeping per stop: since when does OVapi return nothing at all for it?"""
from __future__ import annotations

from datetime import datetime

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .const import DOMAIN

STORE_VERSION = 1
SAVE_DELAY = 30  # seconds: changes are rare, so one delayed write is enough


class StopHealth:
    """Used for the 'stop without departures' repair issue; survives restarts."""

    def __init__(self, hass: HomeAssistant) -> None:
        self._store: Store[dict[str, str]] = Store(hass, STORE_VERSION, f"{DOMAIN}.stop_health")
        self._empty_since: dict[str, str] = {}

    async def async_load(self) -> None:
        data = await self._store.async_load()
        if isinstance(data, dict):
            self._empty_since = {k: v for k, v in data.items() if isinstance(k, str) and isinstance(v, str)}

    def empty_since(self, key: str) -> datetime | None:
        raw = self._empty_since.get(key)
        return dt_util.parse_datetime(raw) if raw else None

    def set_empty_since(self, key: str, when: datetime | None) -> None:
        if when is None:
            if self._empty_since.pop(key, None) is None:
                return
        elif key in self._empty_since:
            return  # keep the first moment
        else:
            self._empty_since[key] = when.isoformat()
        self._store.async_delay_save(lambda: dict(self._empty_since), SAVE_DELAY)


def get_health(hass: HomeAssistant) -> StopHealth:
    """One bookkeeping object per Home Assistant (shared by all entries and reloads)."""
    store = hass.data.setdefault(DOMAIN, {})
    if "health" not in store:
        store["health"] = StopHealth(hass)
    return store["health"]
