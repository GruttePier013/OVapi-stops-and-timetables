"""Config- en optionsflow: haltes zoeken (NDOV-haltebestand), TPC-codes, bewerken, verwijderen."""
from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any, cast

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.const import CONF_NAME
from homeassistant.core import callback
from homeassistant.data_entry_flow import section
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import selector
from homeassistant.util import dt as dt_util

from .api import AddressNotFound, get_api
from .chb import compass_label, compass_name
from .const import (
    ADDRESS_RADIUS,
    CONF_API_MAX_ERRORS,
    CONF_API_SPACING,
    CONF_APPLY_EXISTING,
    CONF_DEFAULT_APPROACH_ONLY,
    CONF_DEFAULT_RADIUS,
    CONF_DEFAULT_REFRESH_ON_ZONE,
    CONF_DEFAULT_SCHEDULE,
    CONF_DEFAULT_ZONE_INTERVAL,
    CONF_DEFAULT_ZONE_STOP_AFTER,
    CONF_DESTINATIONS,
    CONF_DIRECTION_IN_NAME,
    CONF_EXTRA_LINES,
    CONF_LINE_SENSORS,
    CONF_LINES,
    CONF_LOCATION,
    CONF_MAP_LOGO,
    CONF_MARKER,
    CONF_MARKER_RESET,
    CONF_MAX_DEPARTURES,
    CONF_MAX_ERRORS,
    CONF_MODES,
    CONF_PERSONS,
    CONF_PLACE,
    CONF_REFRESH_NDOV,
    CONF_SECTION_DEFAULTS,
    CONF_SECTION_PROTECTION,
    CONF_STALE_HOURS,
    CONF_STOPS,
    CONF_TPC_CODES,
    CONF_ZONE_COOLDOWN,
    DEFAULT_LINE_SENSORS,
    DEFAULT_MAX_DEPARTURES,
    DEFAULT_MODES,
    DEFAULT_RADIUS,
    DEFAULT_STALE_HOURS,
    DEFAULT_ZONE_COOLDOWN,
    DOMAIN,
    MAX_RADIUS,
    MIN_RADIUS,
    MODES,
    NAME,
    PLACE_RADIUS,
    STREET_RADIUS,
)
from .defaults import get_defaults, get_protection
from .parser import clean_town, distance_m, parse_codes, parse_lines, sort_lines
from .schedules import SCHEDULES, ZONE_INTERVALS, ZONE_STOP_AFTER
from .stops import get_stops, stop_key

_LOGGER = logging.getLogger(__name__)

CONF_STOP = "stop"
CONF_REMOVE = "remove"



MODE_LABELS = {"bus": "Bus", "tram": "Tram", "metro": "Metro", "boat": "Veerboot"}


def _search_schema() -> vol.Schema:
    return vol.Schema(
        {
            # 0. Place name: the map jumps to the centre of that place (after Enter/Save of this field)
            vol.Optional(CONF_PLACE): str,
            # 1. The map picker is at the top: a pin with a draggable search circle. Below the map
            #    Home Assistant itself shows the latitude/longitude and radius fields; they follow the pin.
            vol.Required(CONF_LOCATION): selector.LocationSelector(
                selector.LocationSelectorConfig(radius=True, icon="mdi:bus-stop")
            ),
            # 2. Below that an optional address or coordinates (these take precedence over the pin).
            # 3. Welke vervoerstypen (standaard alleen bus)
            vol.Optional(CONF_MODES, default=DEFAULT_MODES): _select(
                [{"label": label, "value": mode} for mode, label in MODE_LABELS.items()], multiple=True
            ),
        }
    )


def _select(
    options: list[dict[str, str]], multiple: bool = False, checkboxes: bool | None = None
) -> selector.SelectSelector:
    listing = multiple if checkboxes is None else checkboxes
    return selector.SelectSelector(
        selector.SelectSelectorConfig(
            options=cast("list[selector.SelectOptionDict]", options),
            multiple=multiple,
            mode=selector.SelectSelectorMode.LIST if listing else selector.SelectSelectorMode.DROPDOWN,
        )
    )


def _translated_select(options: list[str], key: str) -> selector.SelectSelector:
    return selector.SelectSelector(
        selector.SelectSelectorConfig(options=options, mode=selector.SelectSelectorMode.DROPDOWN, translation_key=key)
    )


def _defaults_fields(values: dict[str, Any]) -> dict[Any, Any]:
    """The single defaults screen (same fields at install time and in Settings)."""
    d = get_defaults(values)
    return {
        vol.Required(CONF_DEFAULT_SCHEDULE, default=d[CONF_DEFAULT_SCHEDULE]): _translated_select(
            list(SCHEDULES), "refresh_schedule"
        ),
        vol.Required(CONF_DEFAULT_RADIUS, default=d[CONF_DEFAULT_RADIUS]): selector.NumberSelector(
            selector.NumberSelectorConfig(
                min=MIN_RADIUS, max=MAX_RADIUS, step=50, unit_of_measurement="m", mode=selector.NumberSelectorMode.BOX
            )
        ),
        vol.Required(CONF_DEFAULT_REFRESH_ON_ZONE, default=d[CONF_DEFAULT_REFRESH_ON_ZONE]): selector.BooleanSelector(),
        vol.Required(CONF_DEFAULT_APPROACH_ONLY, default=d[CONF_DEFAULT_APPROACH_ONLY]): selector.BooleanSelector(),
        vol.Required(CONF_DEFAULT_ZONE_INTERVAL, default=d[CONF_DEFAULT_ZONE_INTERVAL]): _translated_select(
            list(ZONE_INTERVALS), "zone_refresh"
        ),
        vol.Required(CONF_DEFAULT_ZONE_STOP_AFTER, default=d[CONF_DEFAULT_ZONE_STOP_AFTER]): _translated_select(
            list(ZONE_STOP_AFTER), "zone_stop_after"
        ),
    }


def _protection_fields(values: dict[str, Any]) -> dict[Any, Any]:
    """Error protection: pause a stop / the API after repeated errors, spacing between API calls."""
    p = get_protection(values)

    def number(minimum: float, maximum: float, step: float, unit: str | None = None) -> selector.NumberSelector:
        return selector.NumberSelector(
            selector.NumberSelectorConfig(
                min=minimum,
                max=maximum,
                step=step,
                mode=selector.NumberSelectorMode.BOX,
                **({"unit_of_measurement": unit} if unit else {}),
            )
        )

    return {
        vol.Required(CONF_MAX_ERRORS, default=p[CONF_MAX_ERRORS]): number(1, 50, 1),
        vol.Required(CONF_API_MAX_ERRORS, default=p[CONF_API_MAX_ERRORS]): number(1, 100, 1),
        vol.Required(CONF_API_SPACING, default=p[CONF_API_SPACING]): number(0, 60, 0.5, "s"),
    }


def _clean_defaults(values: dict[str, Any]) -> dict[str, Any]:
    keys = (
        CONF_DEFAULT_SCHEDULE,
        CONF_DEFAULT_RADIUS,
        CONF_DEFAULT_REFRESH_ON_ZONE,
        CONF_DEFAULT_APPROACH_ONLY,
        CONF_DEFAULT_ZONE_INTERVAL,
        CONF_DEFAULT_ZONE_STOP_AFTER,
    )
    return get_defaults({k: values[k] for k in keys if k in values})


def _display_name(info: dict[str, Any] | None, fallback: str) -> str:
    """'Voorbeeldstad, Voorbeeldlaan' (the place is not repeated if the name already starts with it)."""
    if not info or not info.get("name"):
        return fallback
    name, town = info["name"], clean_town(info.get("town"))
    if town and not name.casefold().startswith(town.casefold()):
        return f"{town}, {name}"
    return name


def _candidate_name(candidate: dict[str, Any], with_direction: bool = False) -> str:
    """'Voorbeeldstad, Markt' of, met richting, 'Voorbeeldstad, Markt - ri. Station'."""
    base = _display_name(candidate, f"Halte {candidate['tpc']}")
    destination = candidate.get("destination")
    return f"{base} - ri. {destination}" if with_direction and destination else base


def _split_list(text: str | None) -> list[str]:
    return [x.strip() for x in (text or "").split(",") if x.strip()]


def _combine_lines(selected: list[str] | None, extra: str | None) -> list[str]:
    return parse_lines([*(selected or []), *parse_lines(extra)])


def _hours_selector() -> selector.NumberSelector:
    return selector.NumberSelector(
        selector.NumberSelectorConfig(min=1, max=168, step=1, unit_of_measurement="uur", mode=selector.NumberSelectorMode.BOX)
    )


if TYPE_CHECKING:  # the mixins run inside a ConfigFlow/OptionsFlow; tell the type checker about hass, async_show_form, ...
    _FlowBase = config_entries.ConfigFlow
else:
    _FlowBase = object


class StopFlowMixin(_FlowBase):
    """Gedeelde stappen voor config- en optionsflow."""

    _task: asyncio.Task | None = None
    ask_stale_hours = False
    _candidates: list[dict[str, Any]]
    _skipped: int = 0
    _chb_state: str = "missing"
    _force_download: bool = False
    _last_progress: float = -1.0
    _queue: list[dict[str, Any]]
    _done: list[dict[str, Any]]
    _location: tuple[float, float]
    _radius: int
    _search_input: dict[str, Any] | None = None
    _modes: list[str] = list(DEFAULT_MODES)
    _last_place: str = ""
    _picked: list[int]

    async def _async_finish_stops(self, stops: list[dict[str, Any]]):  # pragma: no cover - overridden
        raise NotImplementedError

    # ------------------------------------------------------------ zoeken via coördinaten/adres/kaart
    async def async_step_search(self, user_input: dict[str, Any] | None = None):
        errors: dict[str, str] = {}
        if user_input is not None:
            self._search_input = dict(user_input)
            place = (user_input.get(CONF_PLACE) or "").strip()
            if place and place.casefold() != self._last_place:
                # New place name: put the map on the centre of the place and zoom out wide; do not search yet
                try:
                    lat, lon, _, kind = await get_api(self.hass).async_locate(place)
                except AddressNotFound:
                    errors["base"] = "place_not_found"
                except Exception:  # noqa: BLE001
                    _LOGGER.exception("Plaatsnaam opzoeken mislukt")
                    errors["base"] = "cannot_connect"
                else:
                    self._last_place = place.casefold()
                    # place only: zoom out wide on the built-up area; street or address: closer
                    radius = {"woonplaats": PLACE_RADIUS, "weg": STREET_RADIUS}.get(kind, ADDRESS_RADIUS)
                    self._search_input = {
                        **user_input,
                        CONF_LOCATION: {"latitude": lat, "longitude": lon, "radius": radius},
                    }
                return self._show_search(errors)
            self._modes = [m for m in (user_input.get(CONF_MODES) or DEFAULT_MODES) if m in MODES] or list(DEFAULT_MODES)
            try:
                location = user_input.get(CONF_LOCATION)
                if isinstance(location, dict) and location.get("latitude") is not None:
                    lat, lon = float(location["latitude"]), float(location["longitude"])
                else:  # niets ingevuld: thuislocatie van Home Assistant
                    lat, lon = self.hass.config.latitude, self.hass.config.longitude
            except Exception:  # noqa: BLE001
                _LOGGER.exception("Locatie bepalen mislukt")
                errors["base"] = "cannot_connect"
            else:
                self._location = (lat, lon)
                chosen = location.get("radius") if isinstance(location, dict) else None
                self._radius = int(min(max(float(chosen or DEFAULT_RADIUS), MIN_RADIUS), MAX_RADIUS))
                return await self.async_step_download()
        return self._show_search(errors)

    def _show_search(self, errors: dict[str, str]):
        suggested = self._search_input or {
            CONF_LOCATION: {
                "latitude": self.hass.config.latitude,
                "longitude": self.hass.config.longitude,
                "radius": DEFAULT_RADIUS,
            }
        }
        schema = self.add_suggested_values_to_schema(_search_schema(), suggested)  # type: ignore[attr-defined]
        return self.async_show_form(step_id="search", data_schema=schema, errors=errors)  # type: ignore[attr-defined]

    # ------------------------------------------------------------ stop file (3 phases, each with a % progress ring)
    def _set_progress(self, fraction: float) -> None:
        """Update the progress ring (0-100 %) if the Home Assistant version supports it."""
        fraction = round(min(max(fraction, 0.0), 1.0), 2)
        if fraction - self._last_progress < 0.01 and fraction < 1.0:
            return
        self._last_progress = fraction
        update = getattr(self, "async_update_progress", None)
        if update:
            update(fraction)

    async def _async_phase(self, step_id: str, factory, next_step, fail_step: str = "load_failed"):
        """Run one phase as a background task with a progress ring; then continue to ``next_step``."""
        direct = False
        if self._task is None:
            self._last_progress = -1.0
            self._set_progress(0.0)
            self._task = self.hass.async_create_task(factory())
            direct = self._task.done()
        if not self._task.done():
            return self.async_show_progress(  # type: ignore[attr-defined]
                step_id=step_id, progress_action=step_id, progress_task=self._task
            )
        task, self._task = self._task, None
        try:
            task.result()
        except Exception:  # noqa: BLE001
            _LOGGER.exception("Fase '%s' van het zoeken naar haltes mislukt", step_id)
            next_step = fail_step
        else:
            if callable(next_step):
                next_step = next_step()
        if direct:
            return await getattr(self, f"async_step_{next_step}")()
        return self.async_show_progress_done(next_step_id=next_step)  # type: ignore[attr-defined]

    async def async_step_download(self, user_input: dict[str, Any] | None = None):
        """Phase 1: download the stop file from NDOV Loket (only if the index is missing or outdated)."""
        api = get_api(self.hass)
        if self._task is None:
            self._chb_state = await api.async_chb_state()
            if self._chb_state == "fresh" and not self._force_download:
                return await self.async_step_load_stops()
        # If the download fails but an (older) index exists, continue with that.
        fail = "load_stops" if self._chb_state != "missing" else "load_failed"
        return await self._async_phase(
            "download", lambda: api.async_download_chb(self._set_progress), "unpack", fail
        )

    async def async_step_unpack(self, user_input: dict[str, Any] | None = None):
        """Phase 2: stream-unpack ~300 MB XML and convert it to a compact index."""
        api = get_api(self.hass)
        self._force_download = False
        return await self._async_phase(
            "unpack", lambda: api.async_build_chb_index(self._set_progress), "load_stops"
        )

    async def _async_prepare(self) -> None:
        """Phase 3: find quays in the zone and check them live against OVapi."""
        api = get_api(self.hass)
        lat, lon = self._location
        self._candidates, self._skipped = await api.async_candidates(
            lat, lon, self._radius, dt_util.DEFAULT_TIME_ZONE, dt_util.now(), self._set_progress, self._modes
        )
        self._set_progress(1.0)

    async def async_step_load_stops(self, user_input: dict[str, Any] | None = None):
        return await self._async_phase(
            "load_stops", self._async_prepare, lambda: "select_stop" if self._candidates else "no_stops"
        )

    async def async_step_load_failed(self, user_input: dict[str, Any] | None = None):
        return self.async_abort(reason="chb_failed")  # type: ignore[attr-defined]

    async def async_step_no_stops(self, user_input: dict[str, Any] | None = None):
        return self.async_abort(reason="no_stops")  # type: ignore[attr-defined]

    async def async_step_select_stop(self, user_input: dict[str, Any] | None = None):
        errors: dict[str, str] = {}
        if user_input is not None:
            picked = [i for i in range(len(self._candidates)) if user_input.get(f"stop_{i + 1}")]
            if not picked:
                errors["base"] = "no_selection"
            else:
                self._picked = picked
                return await self.async_step_extra_options()
        towns = {c["town"] for c in self._candidates if c.get("town")}
        fields: dict[Any, Any] = {}
        placeholders: dict[str, str] = {}
        for i, c in enumerate(self._candidates, start=1):
            title = c["name"] + (f" ({c['town']})" if len(towns) > 1 and c.get("town") else "")
            compass = compass_label(c.get("bearing"))
            # label of the checkbox: stop, TPC, distance and heading ...
            placeholders[f"stop_{i}_label"] = " · ".join(
                part
                for part in (
                    title,
                    f"📍 TPC {', '.join(c['tpc_codes'])}",
                    self._home_distance_text(c) + (f" | {compass}" if compass else ""),
                )
                if part
            )
            # ... en eronder (helpertekst, markdown) de huidige vertrektijden per lijn
            if c.get("departures"):
                rows = "\n".join(
                    f"- 🚌 **{d['lijn']}**" + (f" → {' / '.join(d['bestemmingen'])}" if d["bestemmingen"] else "") + f" · {d['tijd']}"
                    for d in c["departures"]
                )
                placeholders[f"stop_{i}_info"] = f"Huidige vertrektijden OVapi:\n\n{rows}"
            else:
                placeholders[f"stop_{i}_info"] = "⚠️ Geen vertrektijden informatie"
            fields[vol.Optional(f"stop_{i}", default=False)] = bool
        n = len(self._candidates)
        found = f"{n} richting gevonden." if n == 1 else f"{n} richtingen gevonden."
        if self._skipped:
            found += f" {self._skipped} haltes zonder live gegevens bij OVapi zijn niet getoond."
        placeholders["found"] = found
        return self.async_show_form(  # type: ignore[attr-defined]
            step_id="select_stop",
            data_schema=vol.Schema(fields),
            description_placeholders=placeholders,
            errors=errors,
        )

    async def async_step_extra_options(self, user_input: dict[str, Any] | None = None):
        """Separate step after the stop choice: heading in the name, stale-data timeout and the NDOV file."""
        if user_input is not None:
            if user_input.get(CONF_REFRESH_NDOV):
                # Terug naar stap 1: haltebestand opnieuw downloaden, uitpakken en dezelfde zone opnieuw doorzoeken.
                self._force_download = True
                return await self.async_step_download()
            self._stale_hours = int(user_input.get(CONF_STALE_HOURS, DEFAULT_STALE_HOURS))
            with_direction = bool(user_input.get(CONF_DIRECTION_IN_NAME))
            # Every ticked direction becomes its own device.
            self._queue = [self._pending_from(self._candidates[i], with_direction) for i in self._picked]
            self._done = []
            names = [p["default_name"] for p in self._queue]
            for n, pending in enumerate(self._queue, start=1):
                if names.count(pending["default_name"]) > 1:
                    # zelfde naam (bijv. beide richtingen): onderscheid op windrichting, anders op volgnummer
                    suffix = pending.get("compass") or str(n)
                    pending["default_name"] = f"{pending['default_name']} ({suffix})"
            return await self.async_step_stop_details()
        fields: dict[Any, Any] = {vol.Optional(CONF_DIRECTION_IN_NAME, default=True): bool}
        if self.ask_stale_hours:
            fields[vol.Optional(CONF_STALE_HOURS, default=DEFAULT_STALE_HOURS)] = _hours_selector()
        fields[vol.Optional(CONF_REFRESH_NDOV, default=False)] = bool
        info = await get_api(self.hass).async_chb_info()
        # For the checkbox label: age of the NDOV file ("72 dagen oud, bestand van 2026-08-12")
        placeholders = {
            "ndov_days": str(info["file_age_days"]) if info else "?",
            "ndov_date": (info["source_date"] or info["built"] or "onbekend") if info else "onbekend",
        }
        return self.async_show_form(  # type: ignore[attr-defined]
            step_id="extra_options", data_schema=vol.Schema(fields), description_placeholders=placeholders
        )

    def _home_distance(self, c: dict[str, Any]) -> float:
        """Distance (m, as the crow flies) from the stop to the Home Assistant home location."""
        return distance_m(c["lat"], c["lon"], self.hass.config.latitude, self.hass.config.longitude)

    def _home_distance_text(self, c: dict[str, Any]) -> str:
        """'0,7 km lopen van huis' (under 2 km) or '50,6 km van huis'; always one decimal."""
        meters = self._home_distance(c)
        km = f"{meters / 1000:.1f}".replace(".", ",")
        return f"{km} km lopen van huis" if meters < 2000 else f"{km} km van huis"

    def _pending_from(self, candidate: dict[str, Any], with_direction: bool = False) -> dict[str, Any]:
        return {
            "compass": compass_name(candidate.get("bearing")),
            "tpc_codes": list(candidate["tpc_codes"]),
            "stop_code": candidate.get("quay") or candidate["tpc"],
            "distance_m": round(self._home_distance(candidate)),
            "lat": candidate["lat"],
            "lon": candidate["lon"],
            "default_name": _candidate_name(candidate, with_direction),
            "line_options": dict(candidate.get("lines") or {}) or None,
            "preview": candidate.get("preview") or "",
        }

    async def _async_line_options(self, codes: list[str], current: list[str]) -> dict[str, str]:
        """Lines currently at the stop (live) + the lines already chosen."""
        try:
            live = await get_api(self.hass).async_line_options(codes, dt_util.DEFAULT_TIME_ZONE)
        except Exception:  # noqa: BLE001
            _LOGGER.debug("Lijnen ophalen mislukt", exc_info=True)
            live = {}
        return sort_lines({**live, **{c: c for c in current if c not in live}})

    async def async_step_stop_details(self, user_input: dict[str, Any] | None = None):
        pending = self._queue[0]
        if pending.get("line_options") is None:
            pending["line_options"] = await self._async_line_options(pending["tpc_codes"], [])
        line_opts: dict[str, str] = pending["line_options"]
        if user_input is not None:
            stop = {
                "name": (user_input.get(CONF_NAME) or "").strip() or pending["default_name"],
                "tpc_codes": pending["tpc_codes"],
                "lines": _combine_lines(user_input.get(CONF_LINES), user_input.get(CONF_EXTRA_LINES)),
                "destinations": _split_list(user_input.get(CONF_DESTINATIONS)),
            }
            for key in ("stop_code", "distance_m", "lat", "lon"):
                if pending.get(key) is not None:
                    stop[key] = pending[key]
            self._done.append(stop)
            self._queue.pop(0)
            if self._queue:
                return await self.async_step_stop_details()
            return await self._async_finish_stops(self._done)
        fields: dict[Any, Any] = {vol.Required(CONF_NAME, default=pending["default_name"]): str}
        if line_opts:
            fields[vol.Optional(CONF_LINES, default=[])] = _select(
                [{"label": label, "value": value} for value, label in line_opts.items()], multiple=True
            )
        fields[vol.Optional(CONF_EXTRA_LINES, default="")] = str
        fields[vol.Optional(CONF_DESTINATIONS, default="")] = str
        total = len(self._done) + len(self._queue)
        progress = ""
        if total > 1:
            progress = f"Richting {len(self._done) + 1} van {total}"
        return self.async_show_form(  # type: ignore[attr-defined]
            step_id="stop_details",
            data_schema=vol.Schema(fields),
            description_placeholders={"progress": progress, "direction": pending.get("preview") or "-"},
        )

    # ------------------------------------------------------------ handmatig TPC-codes
    async def async_step_manual(self, user_input: dict[str, Any] | None = None):
        errors: dict[str, str] = {}
        if user_input is not None:
            codes = parse_codes(user_input.get(CONF_TPC_CODES))
            if not codes:
                errors["base"] = "invalid_tpc"
            else:
                infos = await get_api(self.hass).async_validate_tpcs(codes)
                if any(infos[c] is None for c in codes):
                    errors["base"] = "tpc_not_found"
                else:
                    known = [info for c in codes if (info := infos[c]) is not None and info["lat"] is not None]
                    pending: dict[str, Any] = {
                        "tpc_codes": codes,
                        "default_name": _display_name(infos[codes[0]], f"Halte {codes[0]}"),
                        "line_options": None,
                    }
                    if known:
                        pending["lat"] = sum(i["lat"] for i in known) / len(known)
                        pending["lon"] = sum(i["lon"] for i in known) / len(known)
                    self._queue, self._done = [pending], []
                    return await self.async_step_stop_details()
        return self.async_show_form(  # type: ignore[attr-defined]
            step_id="manual",
            data_schema=vol.Schema({vol.Required(CONF_TPC_CODES): str}),
            errors=errors,
        )


def _marker_override(current: dict[str, Any], user_input: dict[str, Any]) -> dict[str, float] | None:
    """New map-marker override: None when reset or unchanged from the real stop position."""
    if user_input.get(CONF_MARKER_RESET):
        return None
    picked = user_input.get(CONF_MARKER)
    if not isinstance(picked, dict):
        return current.get("marker")
    lat, lon = float(picked["latitude"]), float(picked["longitude"])
    base = current.get("marker") or {"lat": current.get("lat"), "lon": current.get("lon")}
    if base.get("lat") is not None and abs(base["lat"] - lat) < 1e-7 and abs(base["lon"] - lon) < 1e-7:
        return current.get("marker")  # not moved
    if current.get("lat") is not None and abs(current["lat"] - lat) < 1e-7 and abs(current["lon"] - lon) < 1e-7:
        return None  # moved back onto the stop
    return {"lat": lat, "lon": lon}


class EditStopsMixin(StopFlowMixin):
    """Bestaande haltes bewerken: naam, TPC-codes, lijnen en bestemmingen (opties en herconfiguratie)."""

    _edit_key: str

    def _stops(self) -> list[dict[str, Any]]:  # pragma: no cover - overridden
        raise NotImplementedError

    def _save_stops(self, stops: list[dict[str, Any]]):  # pragma: no cover - overridden
        raise NotImplementedError

    async def async_step_edit_stop(self, user_input: dict[str, Any] | None = None):
        stops = self._stops()
        if not stops:
            return self.async_abort(reason="no_stops_configured")
        if user_input is not None:
            self._edit_key = user_input[CONF_STOP]
            return await self.async_step_edit_details()
        options = [{"label": f"{s['name']} ({', '.join(s['tpc_codes'])})", "value": stop_key(s)} for s in stops]
        return self.async_show_form(
            step_id="edit_stop",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_STOP): _select(options),
                }
            ),
        )

    async def async_step_edit_details(self, user_input: dict[str, Any] | None = None):
        stops = self._stops()
        current = next(s for s in stops if stop_key(s) == self._edit_key)
        errors: dict[str, str] = {}
        if user_input is not None:
            codes = parse_codes(user_input.get(CONF_TPC_CODES))
            if not codes:
                errors["base"] = "invalid_tpc"
            else:
                new_codes = [c for c in codes if c not in current["tpc_codes"]]
                infos = await get_api(self.hass).async_validate_tpcs(new_codes) if new_codes else {}
                if any(v is None for v in infos.values()):
                    errors["base"] = "tpc_not_found"
                else:
                    updated = {
                        **current,
                        "marker": _marker_override(current, user_input),
                        "name": (user_input.get(CONF_NAME) or "").strip() or current["name"],
                        "tpc_codes": codes,
                        "lines": _combine_lines(user_input.get(CONF_LINES), user_input.get(CONF_EXTRA_LINES)),
                        "destinations": _split_list(user_input.get(CONF_DESTINATIONS)),
                    }
                    others = [s for s in stops if stop_key(s) != self._edit_key]
                    if stop_key(updated) != self._edit_key and stop_key(updated) in {stop_key(s) for s in others}:
                        return self.async_abort(reason="already_configured")
                    return self._save_stops([updated if stop_key(s) == self._edit_key else s for s in stops])
        line_opts = await self._async_line_options(current["tpc_codes"], current["lines"])
        fields: dict[Any, Any] = {
            vol.Required(CONF_NAME, default=current["name"]): str,
            vol.Required(CONF_TPC_CODES, default=", ".join(current["tpc_codes"])): str,
        }
        if line_opts:
            fields[vol.Optional(CONF_LINES, default=current["lines"])] = _select(
                [{"label": label, "value": value} for value, label in line_opts.items()], multiple=True
            )
        fields[vol.Optional(CONF_EXTRA_LINES, default="")] = str
        fields[vol.Optional(CONF_DESTINATIONS, default=", ".join(current.get("destinations", [])))] = str
        shown = current.get("marker") or ({"lat": current.get("lat"), "lon": current.get("lon")})
        if shown.get("lat") is not None and shown.get("lon") is not None:
            fields[
                vol.Optional(CONF_MARKER, default={"latitude": shown["lat"], "longitude": shown["lon"]})
            ] = selector.LocationSelector(selector.LocationSelectorConfig(radius=False, icon="mdi:bus-stop"))
            fields[vol.Optional(CONF_MARKER_RESET, default=False)] = bool
        return self.async_show_form(step_id="edit_details", data_schema=vol.Schema(fields), errors=errors)

    # ------------------------------------------------------------ verwijderen


class DutchBusConfigFlow(EditStopsMixin, StopFlowMixin, config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1
    ask_stale_hours = True  # only on first install; afterwards via Configure → Settings

    _defaults: dict[str, Any] = {}  # noqa: RUF012

    async def async_step_user(self, user_input: dict[str, Any] | None = None):
        """Step 1: defaults for refreshing and the zone (one screen; changeable later in Settings)."""
        if user_input is not None:
            self._defaults = _clean_defaults(user_input)
            return await self.async_step_menu()
        return self.async_show_form(step_id="user", data_schema=vol.Schema(_defaults_fields(self._defaults)))

    async def async_step_menu(self, user_input: dict[str, Any] | None = None):
        return self.async_show_menu(step_id="menu", menu_options=["search", "manual"])

    async def _async_finish_stops(self, stops: list[dict[str, Any]]):
        await self.async_set_unique_id(DOMAIN)
        self._abort_if_unique_id_configured()
        return self.async_create_entry(
            title=NAME,
            data={CONF_STOPS: stops},
            options={
                **(get_defaults(self._defaults) if self._defaults else {}),
                CONF_STALE_HOURS: getattr(self, "_stale_hours", DEFAULT_STALE_HOURS),
            },
        )

    # ------------------------------------------------------------ herconfiguratie (Opnieuw configureren)
    def _stops(self) -> list[dict[str, Any]]:
        return get_stops(self._get_reconfigure_entry())

    def _save_stops(self, stops: list[dict[str, Any]]):
        entry = self._get_reconfigure_entry()
        # the integration's update listener reloads the entry afterwards
        self.hass.config_entries.async_update_entry(entry, options={**entry.options, CONF_STOPS: stops})
        return self.async_abort(reason="reconfigure_successful")

    async def async_step_reconfigure(self, user_input: dict[str, Any] | None = None):
        return await self.async_step_edit_stop()

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: config_entries.ConfigEntry):
        return DutchBusOptionsFlow()


class DutchBusOptionsFlow(EditStopsMixin, StopFlowMixin, config_entries.OptionsFlow):
    def _stops(self) -> list[dict[str, Any]]:
        return get_stops(self.config_entry)

    def _save_stops(self, stops: list[dict[str, Any]]):
        return self._save(**{CONF_STOPS: stops})

    def _save(self, **changes: Any):
        return self.async_create_entry(title="", data={**self.config_entry.options, **changes})

    async def async_step_init(self, user_input: dict[str, Any] | None = None):
        return self.async_show_menu(
            step_id="init",
            menu_options=["search", "manual", "edit_stop", "remove_stops", "settings"],
        )

    async def _async_finish_stops(self, stops: list[dict[str, Any]]):
        current = self._stops()
        known = {stop_key(s) for s in current}
        new = [s for s in stops if stop_key(s) not in known]
        if not new:
            return self.async_abort(reason="already_configured")
        return self._save(**{CONF_STOPS: [*current, *new]})

    async def async_step_remove_stops(self, user_input: dict[str, Any] | None = None):
        stops = self._stops()
        if user_input is not None:
            remove = set(user_input.get(CONF_REMOVE, []))
            return self._save(**{CONF_STOPS: [s for s in stops if stop_key(s) not in remove]})
        options = [{"label": f"{s['name']} ({', '.join(s['tpc_codes'])})", "value": stop_key(s)} for s in stops]
        return self.async_show_form(
            step_id="remove_stops",
            data_schema=vol.Schema(
                {
                    vol.Optional(CONF_REMOVE, default=[]): _select(options, multiple=True),
                }
            ),
        )

    # ------------------------------------------------------------ settings
    def _settings_schema(self, opts: dict[str, Any]) -> vol.Schema:
        return vol.Schema(
            {
                vol.Required(
                    CONF_MAX_DEPARTURES, default=opts.get(CONF_MAX_DEPARTURES, DEFAULT_MAX_DEPARTURES)
                ): selector.NumberSelector(
                    selector.NumberSelectorConfig(min=1, max=15, step=1, mode=selector.NumberSelectorMode.BOX)
                ),
                vol.Optional(CONF_PERSONS, default=opts.get(CONF_PERSONS, [])): selector.EntitySelector(
                    selector.EntitySelectorConfig(domain="person", multiple=True)
                ),
                vol.Required(
                    CONF_ZONE_COOLDOWN, default=opts.get(CONF_ZONE_COOLDOWN, DEFAULT_ZONE_COOLDOWN)
                ): selector.NumberSelector(
                    selector.NumberSelectorConfig(
                        min=1, max=240, step=1, unit_of_measurement="min", mode=selector.NumberSelectorMode.BOX
                    )
                ),
                vol.Required(CONF_STALE_HOURS, default=opts.get(CONF_STALE_HOURS, DEFAULT_STALE_HOURS)): _hours_selector(),
                vol.Optional(CONF_LINE_SENSORS, default=opts.get(CONF_LINE_SENSORS, DEFAULT_LINE_SENSORS)): bool,
                vol.Optional(CONF_MAP_LOGO, default=opts.get(CONF_MAP_LOGO, True)): bool,
                vol.Required(CONF_SECTION_DEFAULTS): section(
                    vol.Schema(
                        {
                            **_defaults_fields(opts),
                            vol.Required(CONF_APPLY_EXISTING, default=False): selector.BooleanSelector(),
                        }
                    ),
                    {"collapsed": False},
                ),
                vol.Required(CONF_SECTION_PROTECTION): section(
                    vol.Schema(_protection_fields(opts)),
                    {"collapsed": True},
                ),
            }
        )

    async def _async_apply_defaults_to_existing(self, d: dict[str, Any]) -> None:
        """Apply the defaults to the existing stops (via the services of their entities)."""
        ent_reg = er.async_get(self.hass)
        on = d[CONF_DEFAULT_REFRESH_ON_ZONE]
        actions: tuple[tuple[str, str, str, dict[str, Any]], ...] = (
            ("select", "refresh_schedule", "select_option", {"option": d[CONF_DEFAULT_SCHEDULE]}),
            ("select", "zone_refresh", "select_option", {"option": d[CONF_DEFAULT_ZONE_INTERVAL]}),
            ("select", "zone_stop_after", "select_option", {"option": d[CONF_DEFAULT_ZONE_STOP_AFTER]}),
            ("number", "proximity_radius", "set_value", {"value": d[CONF_DEFAULT_RADIUS]}),
            ("switch", "refresh_on_approach", "turn_on" if on else "turn_off", {}),
            ("switch", "approach_only", "turn_on" if d[CONF_DEFAULT_APPROACH_ONLY] else "turn_off", {}),
        )
        for stop in self._stops():
            for platform, key, service, data in actions:
                entity_id = ent_reg.async_get_entity_id(platform, DOMAIN, f"{stop_key(stop)}_{key}")
                if not entity_id:
                    continue
                try:
                    await self.hass.services.async_call(
                        platform, service, {"entity_id": entity_id, **data}, blocking=True
                    )
                except Exception:  # noqa: BLE001
                    _LOGGER.debug("Applying the default to %s failed", entity_id, exc_info=True)

    async def async_step_settings(self, user_input: dict[str, Any] | None = None):
        opts = dict(self.config_entry.options)
        if user_input is not None:
            defaults_in = dict(user_input.get(CONF_SECTION_DEFAULTS, {}))
            apply = bool(defaults_in.pop(CONF_APPLY_EXISTING, False))
            cleaned = _clean_defaults(defaults_in)
            protection = get_protection(dict(user_input.get(CONF_SECTION_PROTECTION, {})))
            if apply:
                await self._async_apply_defaults_to_existing(cleaned)
            return self._save(
                **cleaned,
                **protection,
                **{
                    CONF_MAX_DEPARTURES: int(user_input[CONF_MAX_DEPARTURES]),
                    CONF_PERSONS: list(user_input.get(CONF_PERSONS, [])),
                    CONF_ZONE_COOLDOWN: int(user_input.get(CONF_ZONE_COOLDOWN, DEFAULT_ZONE_COOLDOWN)),
                    CONF_MAP_LOGO: bool(user_input.get(CONF_MAP_LOGO, True)),
                    CONF_STALE_HOURS: int(user_input[CONF_STALE_HOURS]),
                    CONF_LINE_SENSORS: bool(user_input.get(CONF_LINE_SENSORS, DEFAULT_LINE_SENSORS)),
                },
            )
        return self.async_show_form(step_id="settings", data_schema=self._settings_schema(opts))
