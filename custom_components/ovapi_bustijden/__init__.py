"""OVapi bussen en halteinformatie: vertrektijden van bussen via OVapi."""
from __future__ import annotations

import asyncio
import logging
from datetime import timedelta
from pathlib import Path

from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.helpers.typing import ConfigType

from .api import get_api
from .const import (
    CONF_LINE_SENSORS,
    CONF_MAX_DEPARTURES,
    CONF_PERSONS,
    DEFAULT_LINE_SENSORS,
    DEFAULT_MAX_DEPARTURES,
    DOMAIN,
    FIRST_REFRESH_TIMEOUT,
    NDOV_OUTDATED_DAYS,
    PLATFORMS,
    SERVICE_REFRESH_STOPS,
    STATIC_URL,
)
from .coordinator import DutchBusConfigEntry, DutchBusData, StopCoordinator
from .proximity import ProximityTracker
from .stops import get_stops, stop_key

_LOGGER = logging.getLogger(__name__)
ISSUE_NDOV_OUTDATED = "ndov_outdated"
CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Publiek pad voor het kaartlogo (entity_picture van de halte op de HA-kaart)."""
    if getattr(hass, "http", None) is not None:
        from homeassistant.components.http import StaticPathConfig  # noqa: PLC0415

        await hass.http.async_register_static_paths(
            [StaticPathConfig(STATIC_URL, str(Path(__file__).parent / "static"), True)]
        )

    async def _refresh_stops(call: ServiceCall) -> None:
        """Zonder doel: alle haltes; met doel (apparaten/entiteiten): alleen die haltes."""
        coordinators: dict[str, StopCoordinator] = {}
        for entry in hass.config_entries.async_loaded_entries(DOMAIN):
            coordinators.update(entry.runtime_data.coordinators)
        if not any(k in call.data for k in ("device_id", "entity_id", "area_id", "floor_id", "label_id")):
            targets = list(coordinators.values())
        else:
            # Doel zelf uitlezen (geen afhankelijkheid van interne HA-helpers die per versie verhuizen)
            ent_reg, dev_reg = er.async_get(hass), dr.async_get(hass)
            device_ids = set(cv.ensure_list(call.data.get("device_id")))
            for entity_id in cv.ensure_list(call.data.get("entity_id")):
                if (reg := ent_reg.async_get(entity_id)) and reg.device_id:
                    device_ids.add(reg.device_id)
            for area_id in cv.ensure_list(call.data.get("area_id")):
                device_ids |= {d.id for d in dr.async_entries_for_area(dev_reg, area_id)}
            keys = {
                i[1]
                for device_id in device_ids
                if (dev := dev_reg.async_get(device_id))
                for i in dev.identifiers
                if i[0] == DOMAIN
            }
            targets = [c for k, c in coordinators.items() if k in keys]
        await asyncio.gather(*(c.async_refresh() for c in targets))

    hass.services.async_register(DOMAIN, SERVICE_REFRESH_STOPS, _refresh_stops)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: DutchBusConfigEntry) -> bool:
    api = get_api(hass)
    stops = get_stops(entry)
    max_departures = int(entry.options.get(CONF_MAX_DEPARTURES, DEFAULT_MAX_DEPARTURES))

    coordinators: dict[str, StopCoordinator] = {}
    for stop in stops:
        coordinator = StopCoordinator(hass, entry, api, stop, max_departures)
        coordinators[coordinator.key] = coordinator

    # Eerste verversing parallel (en gebundeld in één OVapi-verzoek) op de achtergrond: een trage of haperende
    # OVapi houdt het opstarten van Home Assistant niet op. We wachten maximaal FIRST_REFRESH_TIMEOUT seconden
    # zodat de meeste entiteiten meteen een waarde hebben; de rest vult zich zodra OVapi antwoordt.
    first = entry.async_create_background_task(
        hass, _async_first_refresh(coordinators), f"{DOMAIN} eerste verversing"
    )
    await asyncio.wait({first}, timeout=FIRST_REFRESH_TIMEOUT)

    entry.runtime_data = DutchBusData(api=api, coordinators=coordinators)

    # Verwijderde haltes: apparaat opruimen
    dev_reg = dr.async_get(hass)
    current = set(coordinators)
    for device in dr.async_entries_for_config_entry(dev_reg, entry.entry_id):
        ids = {i[1] for i in device.identifiers if i[0] == DOMAIN}
        if ids and not ids & current:
            dev_reg.async_update_device(device.id, remove_config_entry_id=entry.entry_id)

    # De binaire sensor 'Persoon nabij' bestaat niet meer (vervangen door de sensor 'Personen nabij')
    registry = er.async_get(hass)
    for old in er.async_entries_for_config_entry(registry, entry.entry_id):
        if old.domain == "sensor" and "_line_" in (old.unique_id or "") and not entry.options.get(CONF_LINE_SENSORS, DEFAULT_LINE_SENSORS):
            registry.async_remove(old.entity_id)  # sensoren per lijn staan uit
        if old.domain in ("binary_sensor", "device_tracker"):  # device_tracker: sinds 0.8 een sensor
            registry.async_remove(old.entity_id)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # Nabijheid pas starten als de entiteiten hun herstelde instellingen (straal, aan/uit) hebben
    tracker = ProximityTracker(hass, coordinators, list(entry.options.get(CONF_PERSONS, [])))
    entry.runtime_data.tracker = tracker
    entry.async_on_unload(tracker.async_start())
    entry.async_on_unload(entry.add_update_listener(_async_reload_entry))

    # Reparatiemelding als het NDOV-haltebestand te oud is (nu en daarna dagelijks)
    async def _check(_now: object = None) -> None:
        await _async_check_ndov_age(hass, api)

    entry.async_create_background_task(hass, _check(), f"{DOMAIN} ouderdom haltebestand")
    entry.async_on_unload(async_track_time_interval(hass, _check, timedelta(days=1)))
    return True


async def _async_first_refresh(coordinators: dict[str, StopCoordinator]) -> None:
    await asyncio.gather(*(c.async_refresh() for c in coordinators.values()))


async def _async_check_ndov_age(hass: HomeAssistant, api) -> None:
    """Meld een reparatie als het haltebestand ouder is dan NDOV_OUTDATED_DAYS; haal de melding weg als het weer vers is."""
    try:
        info = await api.async_chb_info()
    except Exception:  # noqa: BLE001
        _LOGGER.debug("Ouderdom haltebestand bepalen mislukt", exc_info=True)
        return
    if info and info["file_age_days"] > NDOV_OUTDATED_DAYS:
        ir.async_create_issue(
            hass,
            DOMAIN,
            ISSUE_NDOV_OUTDATED,
            is_fixable=False,
            severity=ir.IssueSeverity.WARNING,
            translation_key=ISSUE_NDOV_OUTDATED,
            translation_placeholders={"days": str(info["file_age_days"]), "limit": str(NDOV_OUTDATED_DAYS)},
        )
    else:
        ir.async_delete_issue(hass, DOMAIN, ISSUE_NDOV_OUTDATED)


async def async_unload_entry(hass: HomeAssistant, entry: DutchBusConfigEntry) -> bool:
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def async_remove_config_entry_device(
    hass: HomeAssistant, entry: DutchBusConfigEntry, device: dr.DeviceEntry
) -> bool:
    """Alleen apparaten van niet meer geconfigureerde haltes mogen handmatig weg."""
    current = {stop_key(s) for s in get_stops(entry)}
    return not any(i[0] == DOMAIN and i[1] in current for i in device.identifiers)


async def _async_reload_entry(hass: HomeAssistant, entry: DutchBusConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)
