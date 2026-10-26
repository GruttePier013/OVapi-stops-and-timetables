"""Config- en optionsflow: haltes zoeken (NDOV-haltebestand), TPC-codes, bewerken, verwijderen."""
from __future__ import annotations

import asyncio
import logging
from typing import Any

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.const import CONF_NAME
from homeassistant.core import callback
from homeassistant.helpers import selector
from homeassistant.util import dt as dt_util

from .api import AddressNotFound, get_api
from .chb import compass_label, compass_name
from .const import (
    ADDRESS_RADIUS,
    CONF_DESTINATIONS,
    CONF_DIRECTION_IN_NAME,
    CONF_EXTRA_LINES,
    CONF_LINE_SENSORS,
    CONF_LINES,
    CONF_LOCATION,
    CONF_MAP_LOGO,
    CONF_MAX_DEPARTURES,
    CONF_MODES,
    CONF_PERSONS,
    CONF_PLACE,
    CONF_REFRESH_NDOV,
    CONF_STALE_HOURS,
    CONF_STOPS,
    CONF_TPC_CODES,
    DEFAULT_LINE_SENSORS,
    DEFAULT_MAX_DEPARTURES,
    DEFAULT_MODES,
    DEFAULT_RADIUS,
    DEFAULT_STALE_HOURS,
    DOMAIN,
    MODES,
    NAME,
    PLACE_RADIUS,
    STREET_RADIUS,
)
from .parser import clean_town, distance_m, parse_codes, parse_lines, sort_lines
from .stops import get_stops, stop_key

_LOGGER = logging.getLogger(__name__)

CONF_STOP = "stop"
CONF_REMOVE = "remove"


MIN_RADIUS, MAX_RADIUS = 50, 5000


MODE_LABELS = {"bus": "Bus", "tram": "Tram", "metro": "Metro", "boat": "Veerboot"}


def _search_schema() -> vol.Schema:
    return vol.Schema(
        {
            # 0. Plaatsnaam: de kaart springt naar het centrum van die plaats (na Enter/Opslaan van dit veld)
            vol.Optional(CONF_PLACE): str,
            # 1. De kaartkiezer staat bovenaan: een pin met een sleepbare zoekcirkel. Onder de kaart toont
            #    Home Assistant zelf de velden breedte-/lengtegraad en straal; die lopen mee met de pin.
            vol.Required(CONF_LOCATION): selector.LocationSelector(
                selector.LocationSelectorConfig(radius=True, icon="mdi:bus-stop")
            ),
            # 2. Daaronder optioneel een adres of coördinaten (die gaan voor de pin).
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
            options=options,
            multiple=multiple,
            mode=selector.SelectSelectorMode.LIST if listing else selector.SelectSelectorMode.DROPDOWN,
        )
    )


def _display_name(info: dict[str, Any] | None, fallback: str) -> str:
    """'Voorbeeldstad, Voorbeeldlaan' (de plaats niet dubbel als de naam er al mee begint)."""
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


class StopFlowMixin:
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
                # Nieuwe plaatsnaam: de kaart op het centrum van de plaats zetten en ruim uitzoomen; nog niet zoeken
                try:
                    lat, lon, _, kind = await get_api(self.hass).async_locate(place)
                except AddressNotFound:
                    errors["base"] = "place_not_found"
                except Exception:  # noqa: BLE001
                    _LOGGER.exception("Plaatsnaam opzoeken mislukt")
                    errors["base"] = "cannot_connect"
                else:
                    self._last_place = place.casefold()
                    # alleen een plaats: ruim uitzoomen op de bebouwde kom; straat of adres: dichterbij
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
                radius = (location or {}).get("radius") if isinstance(location, dict) else None
                self._radius = int(min(max(float(radius or DEFAULT_RADIUS), MIN_RADIUS), MAX_RADIUS))
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

    # ------------------------------------------------------------ haltebestand (3 fases, elk met een % -voortgangsring)
    def _set_progress(self, fraction: float) -> None:
        """Voortgangsring (0–100 %) bijwerken, als de Home Assistant-versie dat ondersteunt."""
        fraction = round(min(max(fraction, 0.0), 1.0), 2)
        if fraction - self._last_progress < 0.01 and fraction < 1.0:
            return
        self._last_progress = fraction
        update = getattr(self, "async_update_progress", None)
        if update:
            update(fraction)

    async def _async_phase(self, step_id: str, factory, next_step, fail_step: str = "load_failed"):
        """Voer één fase uit als achtergrondtaak met voortgangsring; daarna door naar ``next_step``."""
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
        """Fase 1: haltebestand van NDOV Loket downloaden (alleen als de index ontbreekt of verouderd is)."""
        api = get_api(self.hass)
        if self._task is None:
            self._chb_state = await api.async_chb_state()
            if self._chb_state == "fresh" and not self._force_download:
                return await self.async_step_load_stops()
        # Mislukt het downloaden maar is er een (oudere) index, dan gaan we daarmee verder.
        fail = "load_stops" if self._chb_state != "missing" else "load_failed"
        return await self._async_phase(
            "download", lambda: api.async_download_chb(self._set_progress), "unpack", fail
        )

    async def async_step_unpack(self, user_input: dict[str, Any] | None = None):
        """Fase 2: ±300 MB XML gestreamd uitpakken en omzetten naar een compacte index."""
        api = get_api(self.hass)
        self._force_download = False
        return await self._async_phase(
            "unpack", lambda: api.async_build_chb_index(self._set_progress), "load_stops"
        )

    async def _async_prepare(self) -> None:
        """Fase 3: quays in de zone zoeken en live bij OVapi controleren."""
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
            # label van de checkbox: halte, TPC, afstand en windrichting ...
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
        """Aparte stap na de haltekeuze: richting in de naam, timeout verouderde data en het NDOV-bestand."""
        if user_input is not None:
            if user_input.get(CONF_REFRESH_NDOV):
                # Terug naar stap 1: haltebestand opnieuw downloaden, uitpakken en dezelfde zone opnieuw doorzoeken.
                self._force_download = True
                return await self.async_step_download()
            self._stale_hours = int(user_input.get(CONF_STALE_HOURS, DEFAULT_STALE_HOURS))
            with_direction = bool(user_input.get(CONF_DIRECTION_IN_NAME))
            # Elke aangevinkte richting wordt een eigen apparaat.
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
        # Voor het label van het vinkje: ouderdom van het NDOV-bestand ("72 dagen oud, bestand van 2026-08-12")
        placeholders = {
            "ndov_days": str(info["file_age_days"]) if info else "?",
            "ndov_date": (info["source_date"] or info["built"] or "onbekend") if info else "onbekend",
        }
        return self.async_show_form(  # type: ignore[attr-defined]
            step_id="extra_options", data_schema=vol.Schema(fields), description_placeholders=placeholders
        )

    def _home_distance(self, c: dict[str, Any]) -> float:
        """Afstand (m, hemelsbreed) van de halte tot de thuislocatie van Home Assistant."""
        return distance_m(c["lat"], c["lon"], self.hass.config.latitude, self.hass.config.longitude)

    def _home_distance_text(self, c: dict[str, Any]) -> str:
        """'0,7 km lopen van huis' (onder 2 km) of '50,6 km van huis'; altijd één decimaal."""
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
        """Lijnen die nu op de halte voorkomen (live) + de al gekozen lijnen."""
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
                    known = [infos[c] for c in codes if infos[c]["lat"] is not None]
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


class EditStopsMixin:
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
        return self.async_show_form(step_id="edit_details", data_schema=vol.Schema(fields), errors=errors)

    # ------------------------------------------------------------ verwijderen


class DutchBusConfigFlow(EditStopsMixin, StopFlowMixin, config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1
    ask_stale_hours = True  # alleen bij de eerste installatie; daarna via Configureren → Instellingen

    async def async_step_user(self, user_input: dict[str, Any] | None = None):
        return self.async_show_menu(step_id="user", menu_options=["search", "manual"])

    async def _async_finish_stops(self, stops: list[dict[str, Any]]):
        await self.async_set_unique_id(DOMAIN)
        self._abort_if_unique_id_configured()
        return self.async_create_entry(
            title=NAME,
            data={CONF_STOPS: stops},
            options={CONF_STALE_HOURS: getattr(self, "_stale_hours", DEFAULT_STALE_HOURS)},
        )

    # ------------------------------------------------------------ herconfiguratie (Opnieuw configureren)
    def _stops(self) -> list[dict[str, Any]]:
        return get_stops(self._get_reconfigure_entry())

    def _save_stops(self, stops: list[dict[str, Any]]):
        entry = self._get_reconfigure_entry()
        # de update-listener van de integratie laadt de entry daarna zelf opnieuw
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

    # ------------------------------------------------------------ instellingen
    async def async_step_settings(self, user_input: dict[str, Any] | None = None):
        opts = self.config_entry.options
        if user_input is not None:
            return self._save(
                **{
                    CONF_MAX_DEPARTURES: int(user_input[CONF_MAX_DEPARTURES]),
                    CONF_PERSONS: list(user_input.get(CONF_PERSONS, [])),
                    CONF_MAP_LOGO: bool(user_input.get(CONF_MAP_LOGO, True)),
                    CONF_STALE_HOURS: int(user_input[CONF_STALE_HOURS]),
                    CONF_LINE_SENSORS: bool(user_input.get(CONF_LINE_SENSORS, DEFAULT_LINE_SENSORS)),
                }
            )
        return self.async_show_form(
            step_id="settings",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_MAX_DEPARTURES,
                        default=opts.get(CONF_MAX_DEPARTURES, DEFAULT_MAX_DEPARTURES),
                    ): selector.NumberSelector(
                        selector.NumberSelectorConfig(
                            min=1, max=15, step=1, mode=selector.NumberSelectorMode.BOX
                        )
                    ),
                    vol.Optional(
                        CONF_PERSONS, default=opts.get(CONF_PERSONS, [])
                    ): selector.EntitySelector(
                        selector.EntitySelectorConfig(domain="person", multiple=True)
                    ),
                    vol.Required(CONF_STALE_HOURS, default=opts.get(CONF_STALE_HOURS, DEFAULT_STALE_HOURS)): _hours_selector(),
                    vol.Optional(CONF_LINE_SENSORS, default=opts.get(CONF_LINE_SENSORS, DEFAULT_LINE_SENSORS)): bool,
                    vol.Optional(CONF_MAP_LOGO, default=opts.get(CONF_MAP_LOGO, True)): bool,
                }
            ),
        )
