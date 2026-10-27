"""Per-halte coordinator."""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.event import async_call_later, async_track_time_change, async_track_time_interval
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .api import DutchBusApi, OvapiBlocked
from .const import (
    AUTO_MIN_GAP,
    CONF_MAX_ERRORS,
    CONF_STALE_HOURS,
    CONF_ZONE_COOLDOWN,
    DAY_END_HOUR,
    DAY_START_HOUR,
    DEFAULT_PROXIMITY_RADIUS,
    DEFAULT_STALE_HOURS,
    DEFAULT_ZONE_COOLDOWN,
    DOMAIN,
    GRACE_SECONDS,
    MIN_APPROACH_REFRESH_GAP,
    STOP_BACKOFF_BASE,
    STOP_BACKOFF_MAX,
)
from .defaults import get_protection
from .parser import build_view, count_passes, describe_tpc, parse_messages, parse_passes, sorted_lines
from .schedules import (
    DEFAULT_SCHEDULE,
    DEFAULT_ZONE_INTERVAL,
    DEFAULT_ZONE_STOP_AFTER,
    SCHEDULES,
    ZONE_INTERVALS,
    ZONE_STOP_AFTER,
)
from .stops import stop_key
from .texts import Texts

_LOGGER = logging.getLogger(__name__)


@dataclass
class DutchBusData:
    api: DutchBusApi
    coordinators: dict[str, StopCoordinator]
    tracker: Any = None  # ProximityTracker


type DutchBusConfigEntry = ConfigEntry[DutchBusData]


def issue_id(key: str) -> str:
    return f"stop_paused_{key}"


class StopCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Fetches the passes of one or more TPC codes (like a combined station sensor)."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: DutchBusConfigEntry,
        api: DutchBusApi,
        stop: dict[str, Any],
        texts: Texts | None = None,
    ) -> None:
        self.t = texts or Texts()
        self.entry = entry
        self.api = api
        self.stop = stop
        self.key = stop_key(stop)
        self.tpc_codes: list[str] = stop["tpc_codes"]
        self.lines: list[str] = stop["lines"]
        self.schedule = DEFAULT_SCHEDULE
        self._unsub_schedule: list[CALLBACK_TYPE] = []
        self._live_location: tuple[float, float] | None = None
        # proximity (set by the tracker and the entities)
        self.proximity_radius: int = DEFAULT_PROXIMITY_RADIUS
        self.refresh_on_approach: bool = True  # master switch 'Vernieuwen als in zone'
        self.approach_only: bool = True
        self.zone_interval_min: float | None = ZONE_INTERVALS[DEFAULT_ZONE_INTERVAL]  # None = on entry only
        self.zone_stop_after_min: int | None = ZONE_STOP_AFTER[DEFAULT_ZONE_STOP_AFTER]  # None = unlimited
        self.zone_cooldown_min: int = int(entry.options.get(CONF_ZONE_COOLDOWN, DEFAULT_ZONE_COOLDOWN))
        self.destinations: list[str] = [str(d).casefold() for d in stop.get("destinations", [])]
        self.nearby: dict[str, int] = {}  # person entity -> distance in m (inside the radius)
        self.nearest_m: int | None = None
        self.nearest_entity: str | None = None
        self.approach_until: dict[str, datetime] = {}
        self.approach_pending = False  # someone got closer since the last interval refresh
        # visit bookkeeping
        self._visit_start: datetime | None = None
        self._window_expired = False
        self._unsub_zone: CALLBACK_TYPE | None = None
        self._unsub_window: CALLBACK_TYPE | None = None
        self._unsub_cooldown: CALLBACK_TYPE | None = None
        # error protection
        self.max_errors: int = get_protection(entry.options)[CONF_MAX_ERRORS]
        self.consecutive_failures = 0
        self.last_error: str | None = None
        self.paused = False
        self._backoff_until = 0.0  # monotonic; automatic refreshes wait until then
        self._auto = False
        self._tick_listeners: set[CALLBACK_TYPE] = set()
        self._unsub_tick: CALLBACK_TYPE | None = None
        self.last_fetch_monotonic: float = -1e9
        self._view_cache: tuple[int, dict[str, Any], list[dict[str, Any]]] | None = None
        self._had_departures = False
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=f"{DOMAIN}_{self.key}",
            update_interval=None,  # refreshing is driven by the schedule (select entity), zone and button
        )
        ir.async_delete_issue(hass, DOMAIN, issue_id(self.key))  # fresh state after a reload

    # ---- refresh entry points
    async def async_auto_refresh(self) -> None:
        """Automatic refresh (schedule, zone): honours pause, backoff and the API circuit breaker."""
        if self.paused or self.api.blocked or time.monotonic() < self._backoff_until:
            _LOGGER.debug("Automatic refresh of %s skipped (paused or backing off)", self.key)
            return
        if time.monotonic() - self.last_fetch_monotonic < AUTO_MIN_GAP:
            _LOGGER.debug("Automatic refresh of %s skipped (refreshed less than %s s ago)", self.key, AUTO_MIN_GAP)
            return
        self._auto = True
        await self.async_refresh()

    async def async_manual_refresh(self) -> None:
        """Manual refresh (button, action, startup): bypasses pause, backoff and pacing; success resumes a paused stop."""
        self._auto = False
        await self.async_refresh()

    # ---- refreshing in the zone: one 'visit' with a window
    #  - first person inside: refresh once (real entries only, at least MIN_APPROACH_REFRESH_GAP after the previous one)
    #    and then every X minutes
    #  - after 'stop after' minutes refreshing stops, even if you are still there
    #  - a new visit only starts once the zone was empty for the cooldown time
    #  - with 'approach only' the interval refreshes only happen while someone is getting closer
    def _cancel(self, *names: str) -> None:
        for name in names:
            unsub = getattr(self, name)
            if unsub:
                setattr(self, name, None)
                unsub()

    @property
    def approaching(self) -> bool:
        """True while a person inside the zone recently got closer to the stop."""
        now = dt_util.utcnow()
        return any(until > now and entity in self.nearby for entity, until in self.approach_until.items())

    @callback
    def _start_window(self) -> None:
        self._cancel("_unsub_window")
        self._window_expired = False
        if self.zone_stop_after_min is None or self._visit_start is None:
            return
        remaining = self.zone_stop_after_min * 60 - (dt_util.utcnow() - self._visit_start).total_seconds()
        if remaining <= 0:
            self._window_expired = True
            return
        self._unsub_window = async_call_later(self.hass, remaining, self._window_end)

    @callback
    def _window_end(self, _now: datetime) -> None:
        self._unsub_window = None
        self._window_expired = True
        self._cancel("_unsub_zone")

    @callback
    def _start_interval(self) -> None:
        self._cancel("_unsub_zone")
        if self._visit_start is None or self._window_expired or not self.zone_interval_min:
            return
        self._unsub_zone = async_track_time_interval(
            self.hass, self._async_zone_tick, timedelta(minutes=self.zone_interval_min)
        )

    async def _async_zone_tick(self, _now: datetime) -> None:
        if not (self.refresh_on_approach and self.nearby and not self._window_expired):
            return
        if self.approach_only:
            if not self.approach_pending:
                return
            self.approach_pending = False
        await self.async_auto_refresh()

    @callback
    def _visit_enter(self, trigger: bool) -> None:
        self._cancel("_unsub_cooldown")
        if self._visit_start is not None:  # back within the cooldown time: same visit, no new start
            return
        self._visit_start = dt_util.utcnow()
        self.approach_pending = False  # the entry refresh covers the approach so far
        if trigger and time.monotonic() - self.last_fetch_monotonic >= MIN_APPROACH_REFRESH_GAP:
            self.hass.async_create_task(self.async_auto_refresh())
        self._start_window()
        self._start_interval()

    @callback
    def _visit_leave(self) -> None:
        self._cancel("_unsub_cooldown")
        if self.zone_cooldown_min <= 0:
            self._visit_end()
            return
        self._unsub_cooldown = async_call_later(self.hass, self.zone_cooldown_min * 60, self._visit_end)

    @callback
    def _visit_end(self, _now: datetime | None = None) -> None:
        self._cancel("_unsub_zone", "_unsub_window", "_unsub_cooldown")
        self._visit_start = None
        self._window_expired = False

    @callback
    def apply_zone_settings(self) -> None:
        """After a change of the switch or selects: set up the timers again."""
        if not self.refresh_on_approach:
            self._visit_end()
            return
        if self._visit_start is None:
            if self.nearby:
                self._visit_enter(trigger=False)
            return
        self._start_window()
        self._start_interval()

    @callback
    def async_add_tick_listener(self, cb: CALLBACK_TYPE) -> CALLBACK_TYPE:
        """One minute timer per stop (instead of one per entity): ``cb`` is called every whole minute."""
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
    def _handle_tick(self, now: datetime) -> None:
        self._prune_departed(now)
        self._refresh_when_empty(now)
        for cb in list(self._tick_listeners):
            cb()

    def _prune_departed(self, now: datetime) -> None:
        """Drop departures that left (beyond the grace period) so they are not filtered again every minute."""
        if not self.data:
            return
        cutoff = now.timestamp() - GRACE_SECONDS
        kept = [d for d in self.data["departures"] if d["expected"].timestamp() > cutoff]
        if len(kept) != len(self.data["departures"]):
            self.data = {**self.data, "departures": kept}
            self._view_cache = None

    def _refresh_when_empty(self, now: datetime) -> None:
        """When the last bus is gone, refresh once (daytime only) instead of waiting for the next schedule."""
        if not self.data:
            return
        if self.view():
            self._had_departures = True
        elif self._had_departures:
            self._had_departures = False  # once per transition, so an empty answer cannot cause a loop
            if DAY_START_HOUR <= dt_util.as_local(now).hour < DAY_END_HOUR:
                self.hass.async_create_task(self.async_auto_refresh())

    # ---- refresh schedule (set by the select entity)
    def set_schedule(self, key: str) -> None:
        """Replace the times at which an automatic refresh happens."""
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
        await self.async_auto_refresh()

    async def async_shutdown(self) -> None:
        self._cancel_schedule()
        self._visit_end()
        if self._unsub_tick is not None:
            self._unsub_tick()
            self._unsub_tick = None
        await super().async_shutdown()

    async def _async_update_data(self) -> dict[str, Any]:
        try:
            data = await self._fetch()
        except OvapiBlocked as err:  # API paused between the check and the call: not the stop's fault
            self.last_error = str(err)
            raise UpdateFailed(str(err)) from err
        except UpdateFailed as err:
            self._register_error(str(err))
            raise
        self._register_success()
        return data

    # ---- error protection
    def _register_error(self, message: str) -> None:
        self.consecutive_failures += 1
        self.last_error = message
        backoff = min(STOP_BACKOFF_BASE * 2 ** (self.consecutive_failures - 1), STOP_BACKOFF_MAX)
        self._backoff_until = time.monotonic() + backoff
        if self.consecutive_failures >= self.max_errors and not self.paused:
            self.paused = True
            _LOGGER.warning(
                "Stop %s paused after %d consecutive errors; press Refresh to try again",
                self.key,
                self.consecutive_failures,
            )
            ir.async_create_issue(
                self.hass,
                DOMAIN,
                issue_id(self.key),
                is_fixable=False,
                severity=ir.IssueSeverity.WARNING,
                translation_key="stop_paused",
                translation_placeholders={"name": self.stop["name"], "errors": str(self.consecutive_failures)},
            )
        # the base class only notifies listeners on a state change; the error counters changed too
        self.async_update_listeners()

    def _register_success(self) -> None:
        self.consecutive_failures = 0
        self.last_error = None
        self._backoff_until = 0.0
        if self.paused:
            self.paused = False
            ir.async_delete_issue(self.hass, DOMAIN, issue_id(self.key))
            _LOGGER.info("Stop %s is working again", self.key)

    async def _fetch(self) -> dict[str, Any]:
        self.last_fetch_monotonic = time.monotonic()
        results = await self.api.async_fetch_group(self.tpc_codes, auto=self._auto)
        departures: list[dict[str, Any]] = []
        raw_passes = 0
        messages: list[str] = []
        coords: list[tuple[float, float]] = []
        errors: list[str] = []
        missing: list[str] = []
        seen: set[tuple[str, str]] = set()
        for code, res in results.items():
            if isinstance(res, BaseException):
                errors.append(f"{code}: {res}")
                continue
            raw_passes += count_passes(res, code)
            if isinstance(res, dict) and code not in res:
                missing.append(code)  # OVapi does not know this code (or has no data for it)
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
        # Like the YAML availability: available as soon as at least one source answers
        if len(errors) == len(self.tpc_codes):
            raise UpdateFailed(f"Fetching OVapi departures failed ({'; '.join(errors)})")
        if errors:
            _LOGGER.warning("%s: partially failed: %s", self.stop["name"], "; ".join(errors))
        if coords:
            self._live_location = (
                sum(c[0] for c in coords) / len(coords),
                sum(c[1] for c in coords) / len(coords),
            )
        return {
            "departures": departures,
            "raw_passes": raw_passes,
            "fetched_count": len(departures),
            "missing": missing,
            "messages": messages[:5],
            "sources_ok": len(self.tpc_codes) - len(errors),
            "fetched": dt_util.utcnow(),
            "partial": bool(errors),
        }

    # ---- derived data (time dependent, recalculated every minute by the entities)
    def view(self, now: datetime | None = None) -> list[dict[str, Any]]:
        """All upcoming departures (no maximum). Cached per minute and data set when ``now`` is not given."""
        if not self.data:
            return []
        if now is not None:
            return build_view(self.data["departures"], now, self.lines, None, GRACE_SECONDS, self.destinations)
        current = dt_util.now()
        minute = int(current.timestamp() // 60)
        cache = self._view_cache
        if cache is not None and cache[0] == minute and cache[1] is self.data:
            return cache[2]
        result = build_view(self.data["departures"], current, self.lines, None, GRACE_SECONDS, self.destinations)
        self._view_cache = (minute, self.data, result)
        return result

    def is_stale(self, now: datetime | None = None) -> bool:
        """Is the data older than 12 hours? (e.g. with refresh frequency 'never'.)"""
        if not self.data:
            return False
        hours = float(self.entry.options.get(CONF_STALE_HOURS, DEFAULT_STALE_HOURS))
        return ((now or dt_util.utcnow()) - self.data["fetched"]).total_seconds() > hours * 3600

    def needs_refresh(self, now: datetime | None = None) -> bool:
        """No or outdated data, or no bus left at the stop: the stop shows the 'refresh data' state."""
        return not self.data or self.is_stale(now) or not self.view(now)

    def view_full(self, now: datetime | None = None) -> list[dict[str, Any]]:
        """Alias of ``view`` (kept for the per-line sensors)."""
        return self.view(now)

    def known_lines(self, now: datetime | None = None) -> list[str]:
        """All lines in the current OVapi data (after the line filter), sorted: ['34', '73', '350']."""
        if not self.data:
            return []
        view = self.view(now)
        return sorted_lines(d["line"] for d in view)

    @property
    def location(self) -> tuple[float, float] | None:
        """Stop position: from the configuration, otherwise from the live OVapi response."""
        lat, lon = self.stop.get("lat"), self.stop.get("lon")
        if lat is not None and lon is not None:
            return float(lat), float(lon)
        return self._live_location

    @property
    def marker_location(self) -> tuple[float, float] | None:
        """Position of the map marker: the user's override if set, otherwise the stop position."""
        marker = self.stop.get("marker")
        if isinstance(marker, dict) and marker.get("lat") is not None and marker.get("lon") is not None:
            return float(marker["lat"]), float(marker["lon"])
        return self.location

    @callback
    def set_proximity(
        self,
        nearby: dict[str, int],
        nearest: int | None,
        trigger: bool = False,
        nearest_entity: str | None = None,
    ) -> None:
        if nearby == self.nearby and nearest == self.nearest_m and nearest_entity == self.nearest_entity:
            return
        was_empty = not self.nearby
        self.nearby = nearby
        self.nearest_m = nearest
        self.nearest_entity = nearest_entity
        if self.refresh_on_approach:
            if nearby and was_empty:
                self._visit_enter(trigger)
            elif not nearby and not was_empty:
                self._visit_leave()
        self.async_update_listeners()

    def age_minutes(self) -> int | None:
        if not self.data:
            return None
        return round((dt_util.utcnow() - self.data["fetched"]).total_seconds() / 60)
