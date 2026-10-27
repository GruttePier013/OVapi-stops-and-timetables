"""0.9.2: minimum gap, refresh when empty, pruning, view cache, bundled checks, proximity filtering, minute updates."""
from datetime import timedelta

from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util

from custom_components.ovapi_bus_departures import coordinator as coordinator_module
from custom_components.ovapi_bus_departures.api import get_api
from custom_components.ovapi_bus_departures.const import DOMAIN
from custom_components.ovapi_bus_departures.proximity import ProximityTracker

from .test_integration import URL, _tz, entry, passes, setup  # noqa: F401

CODE = "90100421"


def _coord(hass):
    return hass.config_entries.async_entries(DOMAIN)[0].runtime_data.coordinators[CODE]


async def _setup(hass, aioclient_mock, **options):
    aioclient_mock.get(URL.format(CODE), json=passes(CODE))
    e = entry([{"tpc_codes": [CODE], "name": "K", "lat": 52.08, "lon": 5.25}], **options)
    await setup(hass, e)
    return e


async def test_automatic_refreshes_keep_a_minimum_gap(hass, aioclient_mock, monkeypatch):
    monkeypatch.setattr(coordinator_module, "AUTO_MIN_GAP", 60)
    await _setup(hass, aioclient_mock)
    c = _coord(hass)
    c.last_fetch_monotonic = -1e9
    n = aioclient_mock.call_count
    await c.async_auto_refresh()
    await c.async_auto_refresh()  # right after the previous one: skipped
    assert aioclient_mock.call_count == n + 1
    await c.async_manual_refresh()  # manual always works
    assert aioclient_mock.call_count == n + 2


async def test_refresh_once_when_last_bus_is_gone_during_the_day(hass, aioclient_mock, freezer, monkeypatch):
    monkeypatch.setattr(coordinator_module, "DAY_START_HOUR", 0)
    monkeypatch.setattr(coordinator_module, "DAY_END_HOUR", 24)
    await _setup(hass, aioclient_mock)
    c = _coord(hass)
    c._refresh_when_empty(dt_util.utcnow())
    assert c._had_departures is True
    n = aioclient_mock.call_count
    freezer.tick(timedelta(hours=3))  # all departures are gone
    c._refresh_when_empty(dt_util.utcnow())
    await hass.async_block_till_done()
    assert aioclient_mock.call_count == n + 1
    c._refresh_when_empty(dt_util.utcnow())  # still empty: no loop
    await hass.async_block_till_done()
    assert aioclient_mock.call_count == n + 1


async def test_no_refresh_when_empty_at_night(hass, aioclient_mock, freezer, monkeypatch):
    monkeypatch.setattr(coordinator_module, "DAY_START_HOUR", 0)
    monkeypatch.setattr(coordinator_module, "DAY_END_HOUR", 0)
    await _setup(hass, aioclient_mock)
    c = _coord(hass)
    c._refresh_when_empty(dt_util.utcnow())
    n = aioclient_mock.call_count
    freezer.tick(timedelta(hours=3))
    c._refresh_when_empty(dt_util.utcnow())
    await hass.async_block_till_done()
    assert aioclient_mock.call_count == n


async def test_departed_buses_are_pruned_and_view_is_cached(hass, aioclient_mock, freezer):
    await _setup(hass, aioclient_mock)
    c = _coord(hass)
    assert c.view() is c.view()  # cached within the minute
    fetched = c.data["fetched_count"]
    assert fetched == len(c.data["departures"]) > 0
    freezer.tick(timedelta(hours=3))
    c._handle_tick(dt_util.utcnow())
    assert c.data["departures"] == [] and c.data["fetched_count"] == fetched
    assert c.view() == []


async def test_missing_code_is_reported(hass, aioclient_mock):
    aioclient_mock.get(URL.format("90100421,90100999"), json=passes(CODE))
    aioclient_mock.get(URL.format(CODE), json=passes(CODE))
    e = entry([{"tpc_codes": [CODE, "90100999"], "name": "K"}])
    await setup(hass, e)
    assert "90100999" in _coord_by(hass, "90100421").data["missing"]


def _coord_by(hass, key):
    return hass.config_entries.async_entries(DOMAIN)[0].runtime_data.coordinators[key]


async def test_validation_is_bundled_per_chunk(hass, aioclient_mock):
    both = {**passes("90100421"), **passes("90100422")}
    aioclient_mock.get(URL.format("90100421,90100422"), json=both)
    out = await get_api(hass).async_validate_tpcs(["90100421", "90100422"])
    assert aioclient_mock.call_count == 1
    assert out["90100421"] is not None and out["90100422"] is not None


async def test_attribute_only_person_changes_do_not_reevaluate(hass, aioclient_mock, monkeypatch):
    await _setup(hass, aioclient_mock)
    calls = []
    original = ProximityTracker.evaluate
    monkeypatch.setattr(ProximityTracker, "evaluate", lambda self, c, trigger: (calls.append(1), original(self, c, trigger)))
    pos = {"latitude": 52.5, "longitude": 5.25}
    hass.states.async_set("person.jan", "not_home", {**pos, "battery": 90})
    await hass.async_block_till_done()
    first = len(calls)
    assert first >= 1
    hass.states.async_set("person.jan", "not_home", {**pos, "battery": 80})  # same place
    await hass.async_block_till_done()
    assert len(calls) == first
    hass.states.async_set("person.jan", "not_home", {"latitude": 52.51, "longitude": 5.25})
    await hass.async_block_till_done()
    assert len(calls) > first


async def test_minute_updates_can_be_switched_off(hass, aioclient_mock, freezer):
    await _setup(hass, aioclient_mock, minute_updates=False)
    c = _coord(hass)
    entity_id = er.async_get(hass).async_get_entity_id("sensor", DOMAIN, f"{CODE}_departures")
    before = hass.states.get(entity_id).attributes["departures"][0]["minutes_until"]
    freezer.tick(timedelta(minutes=1))
    c._handle_tick(dt_util.utcnow())
    await hass.async_block_till_done()
    assert hass.states.get(entity_id).attributes["departures"][0]["minutes_until"] == before  # no rewrite
    freezer.tick(timedelta(hours=3))  # buses gone: written again
    c._handle_tick(dt_util.utcnow())
    await hass.async_block_till_done()
    assert hass.states.get(entity_id).state == "No departures"
