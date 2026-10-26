"""Per-halte coordinator."""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.helpers.event import async_track_time_change, async_track_time_interval
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .api import DutchBusApi
from .const import (
    CONF_STALE_HOURS,
    DEFAULT_PROXIMITY_RADIUS,
    DEFAULT_STALE_HOURS,
    DEFAULT_ZONE_STOP_AFTER,
    DOMAIN,
    GRACE_SECONDS,
    ZONE_REFRESH_INTERVAL,
)
from .parser import build_view, count_passes, describe_tpc, parse_messages, parse_passes, sorted_lines
from .schedules import DEFAULT_SCHEDULE, SCHEDULES
from .stops import stop_key

_LOGGER = logging.getLogger(__name__)


@dataclass
class DutchBusData:
    api: DutchBusApi
    coordinators: dict[str, StopCoordinator]
    tracker: Any = None  # ProximityTracker


type DutchBusConfigEntry = ConfigEntry[DutchBusData]


class StopCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Haalt de passes van één of meer TPC-codes op (zoals een gecombineerde stationssensor)."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: DutchBusConfigEntry,
        api: DutchBusApi,
        stop: dict[str, Any],
        max_departures: int,
    ) -> None:
        self.api = api
        self.stop = stop
        self.key = stop_key(stop)
        self.tpc_codes: list[str] = stop["tpc_codes"]
        self.lines: list[str] = stop["lines"]
        self.max_departures = max_departures
        self.schedule = DEFAULT_SCHEDULE
        self._unsub_schedule: list[CALLBACK_TYPE] = []
        self._live_location: tuple[float, float] | None = None
        # nabijheid (door tracker en entiteiten ingesteld)
        self.proximity_radius: int = DEFAULT_PROXIMITY_RADIUS
        self._refresh_in_zone = True
        self._unsub_zone: CALLBACK_TYPE | None = None
        self.zone_stop_after: int = DEFAULT_ZONE_STOP_AFTER  # minuten
        self._zone_since: datetime | None = None  # moment waarop de tweeminutenverversing begon
        self._zone_expired = False  # venster verlopen: pas weer verversen na het verlaten en opnieuw betreden
        self.zone_interval_s: float | None = None  # None = standaard (ZONE_REFRESH_INTERVAL)
        self.destinations: list[str] = [str(d).casefold() for d in stop.get("destinations", [])]
        self.consecutive_failures = 0
        self.last_error: str | None = None
        self._tick_listeners: set[CALLBACK_TYPE] = set()
        self._unsub_tick: CALLBACK_TYPE | None = None
        self.nearby: dict[str, int] = {}  # persoon-entity -> afstand in m (binnen radius)
        self.nearest_m: int | None = None
        self.last_fetch_monotonic: float = 0.0
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=f"{DOMAIN}_{self.key}",
            update_interval=None,  # verversen gaat via het schema (select-entiteit)
        )

    @property
    def refresh_on_approach(self) -> bool:
        """'Vernieuwen in zone': zolang iemand binnen de zone-radius is, elke 2 minuten verversen."""
        return self._refresh_in_zone

    @refresh_on_approach.setter
    def refresh_on_approach(self, value: bool) -> None:
        self._refresh_in_zone = value
        self._update_zone_timer()

    @callback
    def _update_zone_timer(self) -> None:
        """Timer van 2 minuten alleen actief als de schakelaar aan staat, er iemand in de zone is en het venster loopt."""
        if not self.nearby:
            self._zone_expired = False  # iedereen weg: bij de volgende binnenkomst begint een nieuw venster
        want = self._refresh_in_zone and bool(self.nearby) and not self._zone_expired
        if want and self._unsub_zone is None:
            self._zone_since = dt_util.utcnow()
            self._unsub_zone = async_track_time_interval(
                self.hass, self._async_zone_refresh, timedelta(seconds=self._zone_interval())
            )
        elif not want and self._unsub_zone is not None:
            self._unsub_zone()
            self._unsub_zone = None
            self._zone_since = None

    async def _async_zone_refresh(self, _now: datetime) -> None:
        # Stoppen na ``zone_stop_after`` minuten (instelbaar); daarna wacht de halte op de standaardfrequentie
        if self._zone_since is not None and dt_util.utcnow() - self._zone_since >= timedelta(minutes=self.zone_stop_after):
            self._zone_expired = True
            self._update_zone_timer()
            return
        await self.async_refresh()

    def _zone_interval(self) -> float:
        return self.zone_interval_s if self.zone_interval_s is not None else ZONE_REFRESH_INTERVAL

    @callback
    def set_zone_interval(self, minutes: float) -> None:
        """Interval van 'vernieuwen in zone' wijzigen; een lopende timer start opnieuw met het nieuwe interval."""
        self.zone_interval_s = minutes * 60
        if self._unsub_zone is not None:
            self._unsub_zone()
            self._unsub_zone = None
            self._update_zone_timer()

    @callback
    def async_add_tick_listener(self, cb: CALLBACK_TYPE) -> CALLBACK_TYPE:
        """Eén minuuttimer per halte (i.p.v. één per entiteit): ``cb`` wordt elke hele minuut aangeroepen."""
        self._tick_listeners.add(cb)
        if self._unsub_tick is None:
            self._unsub_tick = async_track_time_change(self.hass, self._handle_tick, second=0)

        @callback
        def remove() -> None:
            self._tick_listeners.discard(cb)
            if not self._tick_listeners and self._unsub_tick is not None:
                self._unsub_tick()
                self._unsub_tick = None

        return remove

    @callback
    def _handle_tick(self, _now: datetime) -> None:
        for cb in list(self._tick_listeners):
            cb()

    @callback
    def set_zone_stop_after(self, minutes: int) -> None:
        self.zone_stop_after = minutes

    # ---- verversschema (door de select-entiteit ingesteld)
    def set_schedule(self, key: str) -> None:
        """Vervang de tijdstippen waarop automatisch ververst wordt."""
        self._cancel_schedule()
        self.schedule = key if key in SCHEDULES else DEFAULT_SCHEDULE
        for hours, minutes in SCHEDULES[self.schedule] or []:
            self._unsub_schedule.append(
                async_track_time_change(
                    self.hass, self._async_scheduled_refresh, hour=hours, minute=minutes, second=0
                )
            )

    def _cancel_schedule(self) -> None:
        for unsub in self._unsub_schedule:
            unsub()
        self._unsub_schedule = []

    async def _async_scheduled_refresh(self, _now: datetime) -> None:
        await self.async_refresh()

    async def async_shutdown(self) -> None:
        self._cancel_schedule()
        if self._unsub_zone is not None:
            self._unsub_zone()
            self._unsub_zone = None
        if self._unsub_tick is not None:
            self._unsub_tick()
            self._unsub_tick = None
        await super().async_shutdown()

    async def _async_update_data(self) -> dict[str, Any]:
        try:
            data = await self._fetch()
        except UpdateFailed as err:
            self.consecutive_failures += 1
            self.last_error = str(err)
            raise
        self.consecutive_failures = 0
        self.last_error = None
        return data

    async def _fetch(self) -> dict[str, Any]:
        self.last_fetch_monotonic = time.monotonic()
        results = await self.api.async_fetch_group(self.tpc_codes)
        departures: list[dict[str, Any]] = []
        raw_passes = 0
        messages: list[str] = []
        coords: list[tuple[float, float]] = []
        errors: list[str] = []
        seen: set[tuple[str, str]] = set()
        for code, res in results.items():
            if isinstance(res, BaseException):
                errors.append(f"{code}: {res}")
                continue
            raw_passes += count_passes(res, code)
            for msg in parse_messages(res, code):
                if msg not in messages:
                    messages.append(msg)
            info = describe_tpc(code, res)
            if info and info["lat"] is not None:
                coords.append((info["lat"], info["lon"]))
            for dep in parse_passes(res, code, dt_util.DEFAULT_TIME_ZONE):
                ident = (dep["tpc"], dep["journey_id"])
                if ident not in seen:
                    seen.add(ident)
                    departures.append(dep)
        # Zoals de yaml-availability: beschikbaar zodra minstens één bron antwoordt
        if len(errors) == len(self.tpc_codes):
            raise UpdateFailed(f"OVapi vertrektijden ophalen mislukt ({'; '.join(errors)})")
        if errors:
            _LOGGER.warning("%s: deels mislukt: %s", self.stop["name"], "; ".join(errors))
        if coords:
            self._live_location = (
                sum(c[0] for c in coords) / len(coords),
                sum(c[1] for c in coords) / len(coords),
            )
        return {
            "departures": departures,
            "raw_passes": raw_passes,
            "messages": messages[:5],
            "sources_ok": len(self.tpc_codes) - len(errors),
            "fetched": dt_util.utcnow(),
            "partial": bool(errors),
        }

    # ---- afgeleide gegevens (tijdsafhankelijk, door entiteiten elke minuut opnieuw berekend)
    def view(self, now: datetime | None = None) -> list[dict[str, Any]]:
        if not self.data:
            return []
        return build_view(
            self.data["departures"],
            now or dt_util.now(),
            self.lines,
            self.max_departures,
            GRACE_SECONDS,
            self.destinations,
        )

    def is_stale(self, now: datetime | None = None) -> bool:
        """Zijn de gegevens ouder dan 12 uur? (bijv. met vernieuwingsfrequentie 'nooit'.)"""
        if not self.data:
            return False
        hours = float(self.config_entry.options.get(CONF_STALE_HOURS, DEFAULT_STALE_HOURS))
        return ((now or dt_util.utcnow()) - self.data["fetched"]).total_seconds() > hours * 3600

    def needs_refresh(self, now: datetime | None = None) -> bool:
        """Geen of verouderde gegevens, of geen enkele bus meer op de halte: de halte toont 'Data vernieuwen'."""
        return not self.data or self.is_stale(now) or not self.view(now)

    def view_full(self, now: datetime | None = None) -> list[dict[str, Any]]:
        """Alle komende vertrekken (zonder maximum), voor de sensoren per lijn."""
        if not self.data:
            return []
        return build_view(
            self.data["departures"], now or dt_util.now(), self.lines, 999, GRACE_SECONDS, self.destinations
        )

    def known_lines(self, now: datetime | None = None) -> list[str]:
        """Alle lijnen in de huidige OVapi-gegevens (na het lijnfilter), gesorteerd: ['34', '73', '350']."""
        if not self.data:
            return []
        view = build_view(self.data["departures"], now or dt_util.now(), self.lines, 999, GRACE_SECONDS, self.destinations)
        return sorted_lines(d["lijn"] for d in view)

    @property
    def location(self) -> tuple[float, float] | None:
        """Positie van de halte: uit de configuratie, anders uit het live OVapi-antwoord."""
        lat, lon = self.stop.get("lat"), self.stop.get("lon")
        if lat is not None and lon is not None:
            return float(lat), float(lon)
        return self._live_location

    @callback
    def set_proximity(self, nearby: dict[str, int], nearest: int | None) -> None:
        if nearby == self.nearby and nearest == self.nearest_m:
            return
        self.nearby = nearby
        self.nearest_m = nearest
        self._update_zone_timer()
        self.async_update_listeners()

    def age_minutes(self) -> int | None:
        if not self.data:
            return None
        return round((dt_util.utcnow() - self.data["fetched"]).total_seconds() / 60)
