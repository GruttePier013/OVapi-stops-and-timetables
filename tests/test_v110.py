"""1.1.0: get_departures, departure events, entity_id all, dead stops, stable key, live binary sensor."""
from datetime import timedelta

import pytest
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import issue_registry as ir

from custom_components.ovapi_bus_departures.const import DOMAIN
from custom_components.ovapi_bus_departures.parser import journey_states
from custom_components.ovapi_bus_departures.stops import stop_key

from .test_integration import URL, _tz, entry, passes, setup  # noqa: F401

LOC = {"lat": 52.08, "lon": 4.25}
J = {"LinePublicNumber": "N9", "DestinationName50": "Nachtstad", "TargetDepartureTime": "2026-10-21T14:40:00",
     "ExpectedDepartureTime": "2026-10-21T14:40:00", "TripStopStatus": "PLANNED"}


async def two_stops(hass, aioclient_mock):
    aioclient_mock.get(URL.format("90100421"), json=passes("90100421"))
    aioclient_mock.get(URL.format("90100422"), json=passes("90100422", {"j9": J}))
    await setup(hass, entry([
        {"tpc_codes": ["90100421"], "name": "Alpha", **LOC},
        {"tpc_codes": ["90100422"], "name": "Beta", "lat": 52.2, "lon": 4.4},
    ]))


def device_id(hass, key):
    return dr.async_get(hass).async_get_device(identifiers={(DOMAIN, key)}).id


async def departures(hass, data):
    return await hass.services.async_call(DOMAIN, "get_departures", data, blocking=True, return_response=True)


# ---------------------------------------------------------------- get_departures
async def test_get_departures_for_a_stop(hass, aioclient_mock):
    await two_stops(hass, aioclient_mock)
    r = await departures(hass, {"device_id": device_id(hass, "90100421")})
    assert [s["stop"] for s in r["stops"]] == ["Alpha"]
    assert [(d["line"], d["live"], d["time"]) for d in r["departures"]] == [("74", True, "14:07"), ("34", False, "14:20")]
    assert r["departures"][0]["expected"] == "2026-10-21T12:07:00+00:00" and r["stops"][0]["stale"] is False


async def test_get_departures_filters_and_limit(hass, aioclient_mock):
    await two_stops(hass, aioclient_mock)
    target = {"device_id": device_id(hass, "90100422")}
    assert [d["line"] for d in (await departures(hass, {**target, "line": "34"}))["departures"]] == ["34"]
    assert [d["line"] for d in (await departures(hass, {**target, "line": "74, N9"}))["departures"]] == ["74", "N9"]
    assert len((await departures(hass, {**target, "limit": 1}))["departures"]) == 1
    live = await departures(hass, {**target, "live_only": True})
    assert [d["line"] for d in live["departures"]] == ["74"]


async def test_get_departures_for_a_zone(hass, aioclient_mock):
    await two_stops(hass, aioclient_mock)
    hass.states.async_set("zone.work", "0", {"latitude": 52.08, "longitude": 4.25, "radius": 200})
    r = await departures(hass, {"zone": "zone.work"})
    assert [s["stop"] for s in r["stops"]] == ["Alpha"]  # Beta lies far outside the zone
    hass.states.async_set("zone.big", "0", {"latitude": 52.1, "longitude": 4.3, "radius": 50_000})
    r = await departures(hass, {"zone": "zone.big", "limit": 3})
    assert [s["stop"] for s in r["stops"]] == ["Alpha", "Beta"]
    assert [d["stop"] for d in r["departures"]] == ["Alpha", "Beta", "Alpha"]  # combined, by time, at most 3


async def test_get_departures_all_and_errors(hass, aioclient_mock):
    await two_stops(hass, aioclient_mock)
    assert len((await departures(hass, {"entity_id": "all"}))["stops"]) == 2
    with pytest.raises(ServiceValidationError):
        await departures(hass, {})  # needs a stop or a zone
    with pytest.raises(ServiceValidationError):
        await departures(hass, {"zone": "zone.nope"})


async def test_get_departures_makes_no_ovapi_request_and_hides_stale_data(hass, aioclient_mock, freezer):
    await two_stops(hass, aioclient_mock)
    calls = aioclient_mock.call_count
    await departures(hass, {"entity_id": "all"})
    assert aioclient_mock.call_count == calls
    freezer.tick(timedelta(hours=13))
    r = await departures(hass, {"entity_id": "all"})
    assert all(s["stale"] and not s["departures"] for s in r["stops"])


# ---------------------------------------------------------------- refresh with entity_id: all
async def test_refresh_entity_id_all_and_none(hass, aioclient_mock):
    await two_stops(hass, aioclient_mock)
    calls = aioclient_mock.call_count
    await hass.services.async_call(DOMAIN, "refresh", {"entity_id": "none"}, blocking=True)
    assert aioclient_mock.call_count == calls
    await hass.services.async_call(DOMAIN, "refresh", {"entity_id": "all"}, blocking=True)
    assert aioclient_mock.call_count == calls + 2


# ---------------------------------------------------------------- events
async def test_events_cancelled_delayed_and_live(hass, aioclient_mock):
    aioclient_mock.get(URL.format("90100421"), json=passes("90100421"))
    await setup(hass, entry([{"tpc_codes": ["90100421"], "name": "K"}]))
    seen: list[tuple[str, str, int]] = []
    hass.bus.async_listen("state_changed", lambda ev: ev.data["entity_id"] == "event.ovapi_k_departure_events"
                          and ev.data["new_state"].attributes.get("event_type")
                          and seen.append((ev.data["new_state"].attributes["event_type"],
                                           ev.data["new_state"].attributes["line"],
                                           ev.data["new_state"].attributes["delay_min"])))

    async def refresh(extra):
        aioclient_mock.clear_requests()
        aioclient_mock.get(URL.format("90100421"), json=passes("90100421", extra))
        await hass.services.async_call(DOMAIN, "refresh", {}, blocking=True)
        await hass.async_block_till_done()

    same = {}
    await refresh(same)
    assert seen == []  # nothing changed: no events
    await refresh({"j2": {"LinePublicNumber": "34", "DestinationName50": "Voorbeeldstad",  # PLANNED -> DRIVING
                          "TargetDepartureTime": "2026-10-21T14:20:00", "ExpectedDepartureTime": "2026-10-21T14:20:00",
                          "TripStopStatus": "DRIVING"}})
    assert seen == [("live", "34", 0)]
    await refresh({"j1": {"LinePublicNumber": "74", "DestinationName50": "Station Centraal",  # +6 min extra delay
                          "TargetDepartureTime": "2026-10-21T14:05:00", "ExpectedDepartureTime": "2026-10-21T14:13:00",
                          "TripStopStatus": "DRIVING"}})
    assert seen[-1] == ("delayed", "74", 8) and len(seen) == 2
    await refresh({"j1": {"LinePublicNumber": "74", "DestinationName50": "Station Centraal",
                          "TargetDepartureTime": "2026-10-21T14:05:00", "ExpectedDepartureTime": "2026-10-21T14:13:00",
                          "TripStopStatus": "CANCEL"}})
    assert seen[-1][0] == "cancelled" and len(seen) == 3
    await refresh({"j1": {"LinePublicNumber": "74", "DestinationName50": "Station Centraal",
                          "TargetDepartureTime": "2026-10-21T14:05:00", "ExpectedDepartureTime": "2026-10-21T14:13:00",
                          "TripStopStatus": "CANCEL"}})
    assert len(seen) == 3  # the same state again: no repeat


def test_journey_states_filters_and_flags(freezer):
    from datetime import datetime  # noqa: PLC0415
    from zoneinfo import ZoneInfo  # noqa: PLC0415
    tz = ZoneInfo("Europe/Amsterdam")
    now = datetime(2026, 10, 21, 14, 0, tzinfo=tz)

    def dep(jid, line, status, exp_min, plan_min=0):
        return {"tpc": "1", "journey_id": jid, "line": line, "destination": "X", "status": status,
                "expected": now + timedelta(minutes=exp_min), "planned": now + timedelta(minutes=plan_min)}

    deps = [dep("a", "74", "DRIVING", 5, 3), dep("b", "34", "CANCEL", 9, 9), dep("c", "74", "PLANNED", -5, -5)]
    states = journey_states(deps, now, lines=["74"])
    assert list(states) == [("1", "a")] and states[("1", "a")]["live"] and states[("1", "a")]["delay_min"] == 2
    assert journey_states(deps, now)[("1", "b")]["cancelled"] is True


# ---------------------------------------------------------------- dead stops
async def test_stop_without_any_departure_for_days_gets_a_repair_issue(hass, aioclient_mock, freezer, monkeypatch):
    from custom_components.ovapi_bus_departures import coordinator as coordinator_module  # noqa: PLC0415
    monkeypatch.setattr(coordinator_module, "DAY_START_HOUR", 6)  # conftest switches the daytime window off
    monkeypatch.setattr(coordinator_module, "DAY_END_HOUR", 22)
    empty = {"90100421": {"Stop": {"TimingPointName": "Leeg", "TimingPointTown": "X"}, "Passes": {}}}
    aioclient_mock.get(URL.format("90100421"), json=empty)
    await setup(hass, entry([{"tpc_codes": ["90100421"], "name": "Leeg"}]))
    issue_id = "stop_no_departures_90100421"
    assert ir.async_get(hass).async_get_issue(DOMAIN, issue_id) is None  # just now: not yet
    freezer.tick(timedelta(days=8))
    await hass.services.async_call(DOMAIN, "refresh", {}, blocking=True)
    assert ir.async_get(hass).async_get_issue(DOMAIN, issue_id) is not None
    aioclient_mock.clear_requests()
    aioclient_mock.get(URL.format("90100421"), json=passes("90100421"))
    await hass.services.async_call(DOMAIN, "refresh", {}, blocking=True)
    assert ir.async_get(hass).async_get_issue(DOMAIN, issue_id) is None  # departures are back


# ---------------------------------------------------------------- stable key
def test_stop_key_prefers_the_stored_key():
    assert stop_key({"tpc_codes": ["2", "1"]}) == "2"
    assert stop_key({"tpc_codes": ["2", "1"], "key": "1"}) == "1"


async def test_editing_the_codes_keeps_the_device(hass, aioclient_mock):
    aioclient_mock.get(URL.format("90100421"), json=passes("90100421"))
    aioclient_mock.get(URL.format("90100422"), json=passes("90100422"))
    e = entry([{"tpc_codes": ["90100421"], "name": "K"}])
    await setup(hass, e)
    r = await hass.config_entries.options.async_init(e.entry_id)
    r = await hass.config_entries.options.async_configure(r["flow_id"], {"next_step_id": "edit_stop"})
    r = await hass.config_entries.options.async_configure(r["flow_id"], {"stop": "90100421"})
    r = await hass.config_entries.options.async_configure(
        r["flow_id"], {"name": "K", "tpc_codes": "90100422, 90100421", "lines": [], "extra_lines": ""})
    await hass.async_block_till_done()
    assert e.options["stops"][0]["tpc_codes"] == ["90100422", "90100421"] and e.options["stops"][0]["key"] == "90100421"
    reg = er.async_get(hass)
    assert reg.async_get_entity_id("sensor", DOMAIN, "90100421_departures") is not None  # same entity, same history
    assert dr.async_get(hass).async_get_device(identifiers={(DOMAIN, "90100421")}) is not None
    assert dr.async_get(hass).async_get_device(identifiers={(DOMAIN, "90100422")}) is None


# ---------------------------------------------------------------- live binary sensor
async def test_live_departure_binary_sensor(hass, aioclient_mock, freezer):
    aioclient_mock.get(URL.format("90100421"), json=passes("90100421"))
    await setup(hass, entry([{"tpc_codes": ["90100421"], "name": "K"}]))
    st = hass.states.get("binary_sensor.ovapi_k_live_departure")
    assert st.state == "on" and st.attributes["live_count"] == 1
    freezer.tick(timedelta(minutes=10))  # the DRIVING bus is gone, only the PLANNED one is left
    from homeassistant.util import dt as dt_util  # noqa: PLC0415
    from pytest_homeassistant_custom_component.common import async_fire_time_changed  # noqa: PLC0415
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=1))
    await hass.async_block_till_done()
    assert hass.states.get("binary_sensor.ovapi_k_live_departure").state == "off"


async def test_blueprint_creates_a_working_automation(hass, freezer):
    """The blueprint loads in Home Assistant and notifies once when the next departure gets close."""
    import shutil  # noqa: PLC0415
    from datetime import timedelta  # noqa: PLC0415
    from pathlib import Path  # noqa: PLC0415

    from homeassistant.setup import async_setup_component  # noqa: PLC0415
    from homeassistant.util import dt as dt_util  # noqa: PLC0415
    from pytest_homeassistant_custom_component.common import async_fire_time_changed, async_mock_service  # noqa: PLC0415

    src = Path(__file__).parent.parent / "blueprints/automation/ovapi_bus_departures/bus_leaves_soon.yaml"
    dest = Path(hass.config.path("blueprints/automation/ovapi_bus_departures"))
    dest.mkdir(parents=True, exist_ok=True)
    shutil.copy(src, dest / "bus_leaves_soon.yaml")
    calls = async_mock_service(hass, "notify", "test")
    entity = "sensor.ovapi_example_next_departure"
    in_20 = dt_util.utcnow() + timedelta(minutes=20)
    hass.states.async_set(entity, in_20.isoformat(), {"line": "74", "destination": "Station"})
    assert await async_setup_component(
        hass,
        "automation",
        {
            "automation": {
                "use_blueprint": {
                    "path": "ovapi_bus_departures/bus_leaves_soon.yaml",
                    "input": {
                        "next_departure": entity,
                        "minutes": 5,
                        "notify_action": [
                            {"action": "notify.test", "data": {"message": "Line {{ line }} to {{ destination }} in {{ minutes }} min"}}
                        ],
                    },
                }
            }
        },
    )
    await hass.async_block_till_done()
    assert calls == []
    hass.states.async_set(entity, (dt_util.utcnow() + timedelta(minutes=4)).isoformat(), {"line": "74", "destination": "Station"})
    await hass.async_block_till_done()
    assert [c.data["message"] for c in calls] == ["Line 74 to Station in 4 min"]
    # a minute later (still below the limit): no second message
    freezer.tick(timedelta(minutes=1))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert len(calls) == 1
