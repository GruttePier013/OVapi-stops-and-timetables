"""OVapi bus times: departure times of buses (and trams, metro, ferries) via OVapi."""
from __future__ import annotations

import asyncio
import contextlib
import logging
from datetime import timedelta
from pathlib import Path

import voluptuous as vol
from homeassistant.components.http import StaticPathConfig
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers import restore_state, translation
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.helpers.target import TargetSelection, async_extract_referenced_entity_ids
from homeassistant.helpers.typing import ConfigType

from .api import get_api
from .const import (
    CONF_API_MAX_ERRORS,
    CONF_API_SPACING,
    CONF_LINE_SENSORS,
    CONF_PERSONS,
    CONF_STOPS,
    DEFAULT_LINE_SENSORS,
    DOMAIN,
    FIRST_REFRESH_TIMEOUT,
    NDOV_OUTDATED_DAYS,
    PLATFORMS,
    SERVICE_REFRESH,
    STATIC_URL,
)
from .coordinator import DutchBusConfigEntry, DutchBusData, StopCoordinator, issue_id
from .defaults import get_protection
from .proximity import ProximityTracker
from .stops import get_stops, stop_key
from .texts import Texts

_LOGGER = logging.getLogger(__name__)
ISSUE_NDOV_OUTDATED = "ndov_outdated"
ISSUE_API_PAUSED = "api_paused"
MIGRATED_KEY = "migrated_zone_settings"  # old number entities -> new selects (value per stop)
STARTUP_PARALLEL = 5  # at most this many stops fetch at the same time during the first refresh
CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    # Public path for the stop pictures (entity_picture of the stop on the HA map)
    if getattr(hass, "http", None) is not None:
        await hass.http.async_register_static_paths(
            [StaticPathConfig(STATIC_URL, str(Path(__file__).parent / "static"), True)]
        )

    async def _refresh(call: ServiceCall) -> None:
        """Action ``refresh``: no target = all stops, otherwise only the targeted stops (devices/entities/areas)."""
        coordinators: dict[str, StopCoordinator] = {}
        for entry in hass.config_entries.async_loaded_entries(DOMAIN):
            coordinators.update(entry.runtime_data.coordinators)
        selected = async_extract_referenced_entity_ids(hass, TargetSelection(call.data))
        if selected.referenced or selected.indirectly_referenced or selected.referenced_devices:
            dev_reg = dr.async_get(hass)
            ent_reg = er.async_get(hass)
            device_ids = set(selected.referenced_devices)
            for entity_id in selected.referenced | selected.indirectly_referenced:
                if (reg_entry := ent_reg.async_get(entity_id)) and reg_entry.device_id:
                    device_ids.add(reg_entry.device_id)
            keys = {
                ident[1]
                for device_id in device_ids
                if (device := dev_reg.async_get(device_id))
                for ident in device.identifiers
                if ident[0] == DOMAIN
            }
            coordinators = {k: c for k, c in coordinators.items() if k in keys}
        await asyncio.gather(*(c.async_manual_refresh() for c in coordinators.values()))

    schema = vol.Schema(cv.TARGET_SERVICE_FIELDS)
    hass.services.async_register(DOMAIN, SERVICE_REFRESH, _refresh, schema=schema)
    return True


def _make_outage_handler(hass: HomeAssistant, api):
    """Create/delete a repair issue when the API pauses automatic refreshes after repeated errors."""

    def handler(outage: bool) -> None:
        if outage:
            ir.async_create_issue(
                hass,
                DOMAIN,
                ISSUE_API_PAUSED,
                is_fixable=False,
                severity=ir.IssueSeverity.WARNING,
                translation_key=ISSUE_API_PAUSED,
                translation_placeholders={"errors": str(api.consecutive_errors)},
            )
        else:
            ir.async_delete_issue(hass, DOMAIN, ISSUE_API_PAUSED)

    return handler


def _migrate_zone_numbers(hass: HomeAssistant, keys: set[str]) -> None:
    """The zone interval and stop-after numbers became selects: remember their last value, remove the old entities."""
    ent_reg = er.async_get(hass)
    stored = restore_state.async_get(hass).last_states
    migrated: dict[str, dict[str, float]] = hass.data.setdefault(DOMAIN, {}).setdefault(MIGRATED_KEY, {})
    for key in keys:
        for suffix, name in (("zone_interval", "interval"), ("zone_stop_after", "stop_after")):
            entity_id = ent_reg.async_get_entity_id("number", DOMAIN, f"{key}_{suffix}")
            if not entity_id:
                continue
            last = stored.get(entity_id)
            if last is not None:
                with contextlib.suppress(TypeError, ValueError):  # unknown/unavailable: the select uses its default
                    migrated.setdefault(key, {})[name] = float(last.state.state)
            ent_reg.async_remove(entity_id)


async def async_setup_entry(hass: HomeAssistant, entry: DutchBusConfigEntry) -> bool:
    protection = get_protection(entry.options)
    api = get_api(hass)
    api.configure(spacing=protection[CONF_API_SPACING], max_errors=protection[CONF_API_MAX_ERRORS])
    api.on_outage_change = _make_outage_handler(hass, api)
    ir.async_delete_issue(hass, DOMAIN, ISSUE_API_PAUSED)  # fresh state after a reload
    stops = get_stops(entry)
    texts = Texts(await translation.async_get_translations(hass, hass.config.language, "exceptions", [DOMAIN]))

    coordinators: dict[str, StopCoordinator] = {}
    for stop in stops:
        coordinator = StopCoordinator(hass, entry, api, stop, texts)
        coordinators[coordinator.key] = coordinator

    # First refresh in the background (at most STARTUP_PARALLEL stops at a time, combined into few OVapi requests):
    # a slow or failing OVapi does not hold up the startup of Home Assistant. We wait at most
    # FIRST_REFRESH_TIMEOUT seconds so most entities have a value right away; the rest fills in when OVapi answers.
    first = entry.async_create_background_task(
        hass, _async_first_refresh(coordinators), f"{DOMAIN} first refresh"
    )
    await asyncio.wait({first}, timeout=FIRST_REFRESH_TIMEOUT)

    entry.runtime_data = DutchBusData(api=api, coordinators=coordinators)

    # Removed stops: clean up the device
    dev_reg = dr.async_get(hass)
    current = set(coordinators)
    for device in dr.async_entries_for_config_entry(dev_reg, entry.entry_id):
        ids = {i[1] for i in device.identifiers if i[0] == DOMAIN}
        if ids and not ids & current:
            dev_reg.async_remove_device(device.id)  # a device belongs to a single config entry

    registry = er.async_get(hass)
    _migrate_zone_numbers(hass, current)
    for old in er.async_entries_for_config_entry(registry, entry.entry_id):
        unique_id = old.unique_id or ""
        if old.domain == "sensor" and "_line_" in unique_id and not entry.options.get(CONF_LINE_SENSORS, DEFAULT_LINE_SENSORS):
            registry.async_remove(old.entity_id)  # line sensors are switched off
        if old.domain == "device_tracker":  # a sensor since 0.8
            registry.async_remove(old.entity_id)
        if old.domain == "binary_sensor" and not unique_id.endswith("_paused"):  # the old 'person nearby' sensor
            registry.async_remove(old.entity_id)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # Start proximity only after the entities restored their settings (radius, on/off)
    tracker = ProximityTracker(hass, coordinators, list(entry.options.get(CONF_PERSONS, [])))
    entry.runtime_data.tracker = tracker
    entry.async_on_unload(tracker.async_start())
    entry.async_on_unload(entry.add_update_listener(_async_reload_entry))

    # Repair issue when the NDOV stop file is too old (now and then daily)
    async def _check(_now: object = None) -> None:
        await _async_check_ndov_age(hass, api)

    entry.async_create_background_task(hass, _check(), f"{DOMAIN} stop file age")
    entry.async_on_unload(async_track_time_interval(hass, _check, timedelta(days=1)))
    return True


async def _async_first_refresh(coordinators: dict[str, StopCoordinator]) -> None:
    limit = asyncio.Semaphore(STARTUP_PARALLEL)

    async def one(coordinator: StopCoordinator) -> None:
        async with limit:
            await coordinator.async_auto_refresh()

    await asyncio.gather(*(one(c) for c in coordinators.values()))


async def _async_check_ndov_age(hass: HomeAssistant, api) -> None:
    """Raise a repair issue when the stop file is older than NDOV_OUTDATED_DAYS; remove it when the file is fresh again."""
    try:
        info = await api.async_chb_info()
    except Exception:  # noqa: BLE001
        _LOGGER.debug("Determining the age of the stop file failed", exc_info=True)
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
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        ir.async_delete_issue(hass, DOMAIN, ISSUE_API_PAUSED)
        for key in entry.runtime_data.coordinators:  # no stale 'stop paused' repair issues
            ir.async_delete_issue(hass, DOMAIN, issue_id(key))
    return unloaded


async def async_remove_config_entry_device(
    hass: HomeAssistant, entry: DutchBusConfigEntry, device: dr.DeviceEntry
) -> bool:
    """Deleting a stop's device removes that stop from the configuration (same as Configure -> remove)."""
    ids = {i[1] for i in device.identifiers if i[0] == DOMAIN}
    stops = get_stops(entry)
    remaining = [s for s in stops if stop_key(s) not in ids]
    if len(remaining) != len(stops):
        # the update listener reloads the entry, which also cleans up the stop's entities and issues
        hass.config_entries.async_update_entry(entry, options={**entry.options, CONF_STOPS: remaining})
    return True


async def _async_reload_entry(hass: HomeAssistant, entry: DutchBusConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)
