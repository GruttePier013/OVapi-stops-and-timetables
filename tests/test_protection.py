"""Error protection per stop, visits in the zone, defaults, migration, actions and diagnostics (0.9.0)."""
import ast
import asyncio
import inspect
import re
from datetime import timedelta

import pytest
from homeassistant.const import MATCH_ALL, EntityCategory
from homeassistant.core import State
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
    mock_restore_cache,
)
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMockResponse

from custom_components.ovapi_bustijden import async_remove_config_entry_device
from custom_components.ovapi_bustijden import coordinator as coord_mod
from custom_components.ovapi_bustijden.const import DOMAIN
from custom_components.ovapi_bustijden.coordinator import StopCoordinator
from custom_components.ovapi_bustijden.diagnostics import (
    async_get_config_entry_diagnostics,
    async_get_device_diagnostics,
)

from .test_integration import URL, _tz, entry, passes, settings_input, setup  # noqa: F401

CODE = "90100421"
STOP = [{"tpc_codes": [CODE], "name": "K"}]


def eid(hass, platform, key, code=CODE):
    return er.async_get(hass).async_get_entity_id(platform, DOMAIN, f"{code}_{key}")


async def _fresh_setup(hass, aioclient_mock, **options):
    aioclient_mock.get(URL.format(CODE), json=passes(CODE))
    e = entry(STOP, **options)
    await setup(hass, e)
    return e, e.runtime_data.coordinators[CODE]


async def _fail(aioclient_mock):
    aioclient_mock.clear_requests()
    aioclient_mock.get(URL.format(CODE), status=500)


async def _ok(aioclient_mock):
    aioclient_mock.clear_requests()
    aioclient_mock.get(URL.format(CODE), json=passes(CODE))


# ---------------------------------------------------------------- error protection per stop
async def test_stop_pauses_after_errors_and_resumes_manually(hass, aioclient_mock):
    e, c = await _fresh_setup(hass, aioclient_mock, max_errors=2)
    await _fail(aioclient_mock)
    await c.async_manual_refresh()
    assert not c.paused and c.consecutive_failures == 1
    await c.async_manual_refresh()
    assert c.paused
    assert ir.async_get(hass).async_get_issue(DOMAIN, f"stop_paused_{CODE}") is not None
    assert hass.states.get(eid(hass, "binary_sensor", "paused")).state == "on"
    calls = aioclient_mock.call_count
    await c.async_auto_refresh()  # automatic refreshes are skipped while paused
    assert aioclient_mock.call_count == calls
    await _ok(aioclient_mock)
    await c.async_manual_refresh()  # a manual refresh works and resumes the stop
    assert not c.paused and c.consecutive_failures == 0
    assert ir.async_get(hass).async_get_issue(DOMAIN, f"stop_paused_{CODE}") is None
    assert hass.states.get(eid(hass, "binary_sensor", "paused")).state == "off"


async def test_stop_backs_off_after_an_error(hass, aioclient_mock):
    e, c = await _fresh_setup(hass, aioclient_mock)
    await _fail(aioclient_mock)
    await c.async_manual_refresh()
    calls = aioclient_mock.call_count
    await c.async_auto_refresh()  # still inside the backoff window
    assert aioclient_mock.call_count == calls


async def test_api_paused_skips_automatic_refresh_and_keeps_data(hass, aioclient_mock):
    e, c = await _fresh_setup(hass, aioclient_mock)
    e.runtime_data.api._record_failure(retry_after=300)
    calls = aioclient_mock.call_count
    await c.async_auto_refresh()
    assert aioclient_mock.call_count == calls
    assert hass.states.get(eid(hass, "sensor", "departures")).state == "14:07 (Lijn 74)"  # keeps the last data


async def test_api_outage_creates_and_clears_repair_issue(hass, aioclient_mock):
    e, _ = await _fresh_setup(hass, aioclient_mock)
    api = e.runtime_data.api
    api._record_failure(retry_after=120)
    assert ir.async_get(hass).async_get_issue(DOMAIN, "api_paused") is not None
    api._record_success()
    assert ir.async_get(hass).async_get_issue(DOMAIN, "api_paused") is None


async def test_backoff_does_not_clash_with_coordinator_base_class(hass, aioclient_mock):
    """Regression: no attribute set before ``super().__init__`` may be one that the base class resets."""
    e, c = await _fresh_setup(hass, aioclient_mock)
    base = DataUpdateCoordinator(hass, coord_mod._LOGGER, name="x", config_entry=None)
    init = ast.parse(inspect.getsource(StopCoordinator.__init__).replace("\n    ", "\n").lstrip())
    ours: set[str] = set()
    for node in ast.walk(init):
        if isinstance(node, ast.Attribute) and isinstance(node.ctx, ast.Store) and getattr(node.value, "id", "") == "self":
            ours.add(node.attr)
    assert not (ours & (set(vars(base)) | {"_retry_after"})), ours & set(vars(base))
    assert isinstance(c._backoff_until, float)
    await _fail(aioclient_mock)
    await c.async_manual_refresh()
    assert c._backoff_until > 0
    assert c._retry_after is None  # the base class attribute is left alone
    await c.async_auto_refresh()  # must not raise while backing off


async def test_unload_removes_repair_issues(hass, aioclient_mock):
    e, c = await _fresh_setup(hass, aioclient_mock, max_errors=1)
    await _fail(aioclient_mock)
    await c.async_manual_refresh()
    e.runtime_data.api._record_failure(retry_after=60)
    reg = ir.async_get(hass)
    assert reg.async_get_issue(DOMAIN, f"stop_paused_{CODE}") and reg.async_get_issue(DOMAIN, "api_paused")
    assert await hass.config_entries.async_unload(e.entry_id)
    assert reg.async_get_issue(DOMAIN, f"stop_paused_{CODE}") is None and reg.async_get_issue(DOMAIN, "api_paused") is None


async def test_startup_refresh_is_limited_to_five_stops_at_a_time(hass, aioclient_mock):
    sizes: list[int] = []

    async def answer(method, url, data):
        codes = str(url).split("/tpc/")[1].split(",")
        sizes.append(len(codes))
        await asyncio.sleep(0)
        return AiohttpClientMockResponse(method, url, json={c: passes(c)[c] for c in codes})

    aioclient_mock.get(re.compile(r"https://v0\.ovapi\.nl/tpc/.*"), side_effect=answer)
    stops = [{"tpc_codes": [f"9010{i:04d}"], "name": f"Halte {i}"} for i in range(12)]
    await setup(hass, entry(stops))
    assert sum(sizes) == 12 and max(sizes) <= 5 and len(sizes) >= 3


# ---------------------------------------------------------------- visits in the zone
async def _zone_setup(hass, aioclient_mock, monkeypatch, approach_only=False):
    monkeypatch.setattr(coord_mod, "MIN_APPROACH_REFRESH_GAP", 0)
    e, c = await _fresh_setup(hass, aioclient_mock)
    if not approach_only:
        await hass.services.async_call(
            "switch", "turn_off", {"entity_id": eid(hass, "switch", "approach_only")}, blocking=True
        )
    return e, c


def _person(hass, near: bool, lat: float | None = None):
    pos = {"latitude": lat or 52.0801, "longitude": 5.2501} if near else {"latitude": 53.0, "longitude": 6.0}
    hass.states.async_set("person.alice", "home" if near else "not_home", pos)


async def _advance(hass, freezer, minutes: float):
    freezer.tick(timedelta(minutes=minutes))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()


async def test_zone_defaults(hass, aioclient_mock, monkeypatch):
    await _zone_setup(hass, aioclient_mock, monkeypatch, approach_only=True)
    assert hass.states.get(eid(hass, "select", "zone_refresh")).state == "every_5_min"
    assert hass.states.get(eid(hass, "select", "zone_stop_after")).state == "30_min"
    assert hass.states.get(eid(hass, "number", "proximity_radius")).state == "500"
    assert hass.states.get(eid(hass, "switch", "approach_only")).state == "on"


async def test_zone_visit_window(hass, aioclient_mock, monkeypatch, freezer):
    await _zone_setup(hass, aioclient_mock, monkeypatch)
    await _advance(hass, freezer, 6)
    assert aioclient_mock.call_count == 1  # nobody in the zone: nothing
    _person(hass, True)
    await hass.async_block_till_done()
    assert aioclient_mock.call_count == 2  # entry: one refresh
    for expected in (3, 4):
        await _advance(hass, freezer, 5)
        assert aioclient_mock.call_count == expected  # then every 5 minutes
    for _ in range(5):
        await _advance(hass, freezer, 5)
    count = aioclient_mock.call_count
    await _advance(hass, freezer, 5)
    await _advance(hass, freezer, 20)
    assert aioclient_mock.call_count == count  # refreshing stopped after 30 minutes, although still in the zone
    assert 5 <= count - 2 <= 6


async def test_zone_entry_refresh_respects_the_minimum_gap(hass, aioclient_mock, freezer):
    await _fresh_setup(hass, aioclient_mock)
    await hass.services.async_call(
        "switch", "turn_off", {"entity_id": eid(hass, "switch", "approach_only")}, blocking=True
    )
    _person(hass, True)  # right after the startup refresh: less than 60 s ago
    await hass.async_block_till_done()
    assert aioclient_mock.call_count == 1
    _person(hass, False)
    await _advance(hass, freezer, 20)  # a new visit after the cool-down, long after the previous refresh
    _person(hass, True)
    await hass.async_block_till_done()
    assert aioclient_mock.call_count == 2


async def test_zone_cooldown_prevents_new_visit(hass, aioclient_mock, monkeypatch, freezer):
    await _zone_setup(hass, aioclient_mock, monkeypatch)
    await hass.services.async_call(
        "select", "select_option", {"entity_id": eid(hass, "select", "zone_refresh"), "option": "on_entry"}, blocking=True
    )
    _person(hass, True)
    await hass.async_block_till_done()
    assert aioclient_mock.call_count == 2  # new visit
    _person(hass, False)
    await _advance(hass, freezer, 5)
    _person(hass, True)  # back within the cool-down (15 min): same visit, no refresh
    await hass.async_block_till_done()
    assert aioclient_mock.call_count == 2
    _person(hass, False)
    await _advance(hass, freezer, 20)  # longer than the cool-down: the visit is over
    _person(hass, True)
    await hass.async_block_till_done()
    assert aioclient_mock.call_count == 3  # new visit


async def test_zone_switch_off_stops_everything(hass, aioclient_mock, monkeypatch, freezer):
    await _zone_setup(hass, aioclient_mock, monkeypatch)
    switch_id = eid(hass, "switch", "refresh_on_approach")
    await hass.services.async_call("switch", "turn_off", {"entity_id": switch_id}, blocking=True)
    _person(hass, True)
    await hass.async_block_till_done()
    await _advance(hass, freezer, 6)
    assert aioclient_mock.call_count == 1  # main switch off
    await hass.services.async_call("switch", "turn_on", {"entity_id": switch_id}, blocking=True)
    await _advance(hass, freezer, 6)
    assert aioclient_mock.call_count == 2  # switched on while someone is in the zone: a visit starts


async def test_zone_refresh_only_while_approaching(hass, aioclient_mock, monkeypatch, freezer):
    await _zone_setup(hass, aioclient_mock, monkeypatch, approach_only=True)
    _person(hass, True, lat=52.0845)  # about 500 m north: inside the 500 m zone
    await hass.async_block_till_done()
    assert aioclient_mock.call_count == 2  # entry refresh
    await _advance(hass, freezer, 5)
    assert aioclient_mock.call_count == 2  # standing still: no refresh
    _person(hass, True, lat=52.0820)  # closer by about 280 m
    await hass.async_block_till_done()
    await _advance(hass, freezer, 6)
    assert aioclient_mock.call_count == 3  # approaching since the last tick: the interval refresh happens
    await _advance(hass, freezer, 5)
    assert aioclient_mock.call_count == 3  # not getting closer any more: no refresh
    await hass.services.async_call(
        "switch", "turn_off", {"entity_id": eid(hass, "switch", "approach_only")}, blocking=True
    )
    await _advance(hass, freezer, 5)
    assert aioclient_mock.call_count == 4  # approach-only off: refreshes as long as someone is in the zone


async def test_distance_to_person_sensor(hass, aioclient_mock):
    await _fresh_setup(hass, aioclient_mock)
    sensor = eid(hass, "sensor", "distance_to_person")
    assert hass.states.get(sensor).state == "unknown"
    hass.states.async_set("person.alice", "not_home", {"latitude": 53.0, "longitude": 6.0, "friendly_name": "Alice"})
    await hass.async_block_till_done()
    st = hass.states.get(sensor)
    assert int(st.state) % 1000 == 0 and int(st.state) > 50_000  # far away: rounded to whole kilometers
    assert st.attributes["person"] == "Alice" and st.attributes["in_zone"] is False
    hass.states.async_set("person.alice", "home", {"latitude": 52.0801, "longitude": 5.2501, "friendly_name": "Alice"})
    await hass.async_block_till_done()
    st = hass.states.get(sensor)
    assert st.state == "10" and st.attributes["in_zone"] is True  # 13 m, rounded to 10 m
    assert er.async_get(hass).async_get(sensor).entity_category is EntityCategory.DIAGNOSTIC


# ---------------------------------------------------------------- defaults and settings
async def test_new_entities_start_with_defaults(hass, aioclient_mock):
    await _fresh_setup(
        hass,
        aioclient_mock,
        default_schedule="hourly",
        default_radius=750,
        default_refresh_on_approach=False,
        default_approach_only=False,
        default_zone_interval="every_10_min",
        default_zone_stop_after="60_min",
    )
    assert hass.states.get(eid(hass, "select", "refresh_schedule")).state == "hourly"
    assert hass.states.get(eid(hass, "number", "proximity_radius")).state == "750"
    assert hass.states.get(eid(hass, "switch", "refresh_on_approach")).state == "off"
    assert hass.states.get(eid(hass, "switch", "approach_only")).state == "off"
    assert hass.states.get(eid(hass, "select", "zone_refresh")).state == "every_10_min"
    assert hass.states.get(eid(hass, "select", "zone_stop_after")).state == "60_min"


async def _open_settings(hass, e):
    r = await hass.config_entries.options.async_init(e.entry_id)
    return await hass.config_entries.options.async_configure(r["flow_id"], {"next_step_id": "settings"})


async def test_settings_save_protection_cooldown_and_apply_existing_only_when_ticked(hass, aioclient_mock):
    e, _ = await _fresh_setup(hass, aioclient_mock)
    sel = eid(hass, "select", "zone_refresh")
    await hass.services.async_call("select", "select_option", {"entity_id": sel, "option": "every_15_min"}, blocking=True)
    new = {"default_zone_interval": "every_2_min", "default_radius": 2000, "default_refresh_on_approach": False}
    r = await _open_settings(hass, e)
    assert r["step_id"] == "settings"
    r = await hass.config_entries.options.async_configure(
        r["flow_id"], settings_input(zone_cooldown=30, defaults=new, protection={"max_errors": 7, "api_spacing": 5})
    )
    assert r["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()
    assert e.options["zone_cooldown"] == 30 and e.options["default_zone_interval"] == "every_2_min"
    assert (e.options["max_errors"], e.options["api_max_errors"], e.options["api_spacing"]) == (7, 10, 5)
    sel = eid(hass, "select", "zone_refresh")
    assert hass.states.get(sel).state == "every_15_min"  # unticked: existing stops keep their choice
    assert hass.states.get(eid(hass, "number", "proximity_radius")).state != "2000"
    r = await _open_settings(hass, e)
    await hass.config_entries.options.async_configure(r["flow_id"], settings_input(defaults={**new, "apply_existing": True}))
    await hass.async_block_till_done()
    assert hass.states.get(eid(hass, "select", "zone_refresh")).state == "every_2_min"
    assert hass.states.get(eid(hass, "number", "proximity_radius")).state == "2000"
    assert hass.states.get(eid(hass, "switch", "refresh_on_approach")).state == "off"


async def test_first_install_defaults_screen_is_saved(hass, aioclient_mock):
    aioclient_mock.get(URL.format(CODE), json=passes(CODE))
    r = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
    assert r["type"] is FlowResultType.FORM and r["step_id"] == "user"
    keys = {str(k) for k in r["data_schema"].schema}
    assert keys == {
        "default_schedule", "default_radius", "default_refresh_on_approach",
        "default_approach_only", "default_zone_interval", "default_zone_stop_after",
    }
    r = await hass.config_entries.flow.async_configure(r["flow_id"], {"default_zone_interval": "every_10_min"})
    assert r["type"] is FlowResultType.MENU and r["step_id"] == "menu"


# ---------------------------------------------------------------- migration of the old numbers
async def test_old_zone_numbers_migrate_to_selects(hass, aioclient_mock):
    aioclient_mock.get(URL.format(CODE), json=passes(CODE))
    e = entry(STOP)
    e.add_to_hass(hass)
    reg = er.async_get(hass)
    old_interval = reg.async_get_or_create("number", DOMAIN, f"{CODE}_zone_interval", config_entry=e)
    old_stop = reg.async_get_or_create("number", DOMAIN, f"{CODE}_zone_stop_after", config_entry=e)
    mock_restore_cache(
        hass, [State(old_interval.entity_id, "10"), State(old_stop.entity_id, "100")]
    )
    await hass.config_entries.async_setup(e.entry_id)
    await hass.async_block_till_done()
    assert reg.async_get(old_interval.entity_id) is None and reg.async_get(old_stop.entity_id) is None
    assert hass.states.get(eid(hass, "select", "zone_refresh")).state == "every_10_min"
    assert hass.states.get(eid(hass, "select", "zone_stop_after")).state == "120_min"  # nearest option to 100


# ---------------------------------------------------------------- actions, buttons, devices
async def test_refresh_action_with_target_and_old_alias(hass, aioclient_mock):
    await _fresh_setup(hass, aioclient_mock)
    assert hass.services.has_service(DOMAIN, "refresh") and hass.services.has_service(DOMAIN, "refresh_stops")
    before = aioclient_mock.call_count
    await hass.services.async_call(DOMAIN, "refresh", {}, blocking=True)  # no target: all stops
    assert aioclient_mock.call_count == before + 1
    device = dr.async_get(hass).async_get_device(identifiers={(DOMAIN, CODE)})
    await hass.services.async_call(DOMAIN, "refresh", {"device_id": device.id}, blocking=True)
    assert aioclient_mock.call_count == before + 2
    await hass.services.async_call(DOMAIN, "refresh_stops", {"entity_id": eid(hass, "sensor", "lines")}, blocking=True)
    assert aioclient_mock.call_count == before + 3
    await hass.services.async_call(DOMAIN, "refresh", {"device_id": "unknown"}, blocking=True)
    assert aioclient_mock.call_count == before + 3  # other target: nothing refreshed


async def test_manual_refresh_works_when_paused_and_refresh_all_button(hass, aioclient_mock):
    e, c = await _fresh_setup(hass, aioclient_mock, max_errors=1)
    await _fail(aioclient_mock)
    await c.async_manual_refresh()
    assert c.paused
    await _ok(aioclient_mock)
    reg = er.async_get(hass)
    button = reg.async_get_entity_id("button", DOMAIN, f"{e.entry_id}_refresh_all")
    assert button and reg.async_get(button).device_id is None  # no device of its own
    await hass.services.async_call("button", "press", {"entity_id": button}, blocking=True)
    assert not c.paused


async def test_removing_a_stop_device_removes_the_stop_from_the_configuration(hass, aioclient_mock):
    e, _ = await _fresh_setup(hass, aioclient_mock)
    device = dr.async_get(hass).async_get_device(identifiers={(DOMAIN, CODE)})
    assert await async_remove_config_entry_device(hass, e, device) is True
    await hass.async_block_till_done()
    assert e.options["stops"] == []
    assert not dr.async_entries_for_config_entry(dr.async_get(hass), e.entry_id)


# ---------------------------------------------------------------- recorder, diagnostics
async def test_all_entities_exclude_attributes_from_the_recorder():
    from custom_components.ovapi_bustijden import sensor  # noqa: PLC0415
    for cls in (sensor.NextDeparture, sensor.Departures, sensor.LineDeparture, sensor.StopOnMap, sensor.NearbyPersons):
        assert MATCH_ALL in cls._unrecorded_attributes, cls


async def test_diagnostics_config_and_device(hass, aioclient_mock):
    aioclient_mock.get(URL.format(CODE), json=passes(CODE))
    e = entry([{**STOP[0], "lat": 52.08, "lon": 5.25}], persons=["person.alice"])
    await setup(hass, e)
    diag = await async_get_config_entry_diagnostics(hass, e)
    assert diag["api"]["outage"] is False and diag["stops"][CODE]["health"]["paused"] is False
    assert diag["stops"][CODE]["config"]["lat"] == "**REDACTED**"
    assert diag["stops"][CODE]["raw_response"][CODE]["Passes"]
    device = dr.async_get(hass).async_get_device(identifiers={(DOMAIN, CODE)})
    one = await async_get_device_diagnostics(hass, e, device)
    assert one["settings"]["zone_interval_min"] == 5 and one["raw_response"][CODE]["Passes"]
    other = dr.DeviceEntry(identifiers={(DOMAIN, "nope")})
    assert await async_get_device_diagnostics(hass, e, other) == {}


async def test_categories_and_disabled_defaults(hass, aioclient_mock):
    await _fresh_setup(hass, aioclient_mock)
    reg = er.async_get(hass)
    for key in ("distance_to_person", "error_count", "fetched_departures", "last_update"):
        assert reg.async_get(eid(hass, "sensor", key)).entity_category is EntityCategory.DIAGNOSTIC
    assert reg.async_get(eid(hass, "sensor", "error_count")).disabled_by is not None  # off by default
    assert reg.async_get(eid(hass, "binary_sensor", "paused")).entity_category is EntityCategory.DIAGNOSTIC


@pytest.mark.parametrize("platform", ["device_tracker"])
async def test_old_device_tracker_is_removed(hass, aioclient_mock, platform):
    aioclient_mock.get(URL.format(CODE), json=passes(CODE))
    e = entry(STOP)
    e.add_to_hass(hass)
    reg = er.async_get(hass)
    old = reg.async_get_or_create(platform, DOMAIN, f"{CODE}_location", config_entry=e)
    await hass.config_entries.async_setup(e.entry_id)
    await hass.async_block_till_done()
    assert reg.async_get(old.entity_id) is None


async def test_unrelated_config_entry_does_not_break_target_parsing(hass, aioclient_mock):
    MockConfigEntry(domain="other").add_to_hass(hass)
    await _fresh_setup(hass, aioclient_mock)
    await hass.services.async_call(DOMAIN, "refresh", {"area_id": "nowhere"}, blocking=True)
