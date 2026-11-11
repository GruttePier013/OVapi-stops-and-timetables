"""Entering a zone with every kind of person/device state."""
import pytest

from .test_integration import URL, _tz, entry, passes, setup  # noqa: F401

CODE = "90100421"
GPS = {"latitude": 52.0801, "longitude": 4.2501}  # at the stop
FAR = {"latitude": 53.0, "longitude": 6.0}


async def _stop(hass, aioclient_mock, **options):
    aioclient_mock.get(URL.format(CODE), json=passes(CODE))
    e = entry([{"tpc_codes": [CODE], "name": "K", "lat": 52.08, "lon": 4.25}], **options)
    await setup(hass, e)
    return e.runtime_data.coordinators[CODE]


async def test_gps_person_enters_and_leaves(hass, aioclient_mock):
    c = await _stop(hass, aioclient_mock)
    hass.states.async_set("person.jan", "not_home", FAR)
    await hass.async_block_till_done()
    assert c.nearby == {}
    hass.states.async_set("person.jan", "not_home", GPS)
    await hass.async_block_till_done()
    assert list(c.nearby) == ["person.jan"]
    hass.states.async_set("person.jan", "not_home", FAR)
    await hass.async_block_till_done()
    assert c.nearby == {}


@pytest.mark.parametrize("state", ["unavailable", "unknown"])
async def test_unavailable_or_unknown_person_is_never_nearby(hass, aioclient_mock, state):
    c = await _stop(hass, aioclient_mock)
    hass.states.async_set("person.jan", "not_home", GPS)
    await hass.async_block_till_done()
    assert list(c.nearby) == ["person.jan"]
    hass.states.async_set("person.jan", state, GPS)  # an old position is still in the attributes
    await hass.async_block_till_done()
    assert c.nearby == {} and c.nearest_m is None


async def test_person_at_home_without_gps_counts_at_the_home_zone(hass, aioclient_mock):
    c = await _stop(hass, aioclient_mock)
    hass.config.latitude, hass.config.longitude = 52.0801, 4.2501
    hass.states.async_set("person.jan", "not_home", FAR)
    await hass.async_block_till_done()
    hass.states.async_set("person.jan", "home", {})  # presence by router: no coordinates
    await hass.async_block_till_done()
    assert list(c.nearby) == ["person.jan"]


async def test_person_in_a_named_zone_without_gps_uses_that_zone(hass, aioclient_mock):
    c = await _stop(hass, aioclient_mock)
    hass.states.async_set("zone.work", "0", {"friendly_name": "Work", "latitude": 52.0801, "longitude": 4.2501, "radius": 100})
    hass.states.async_set("person.jan", "Work", {})
    await hass.async_block_till_done()
    assert list(c.nearby) == ["person.jan"]
    hass.states.async_set("person.jan", "School", {})  # unknown zone name and no coordinates: no position
    await hass.async_block_till_done()
    assert c.nearby == {}


async def test_selected_device_tracker_is_followed_but_other_trackers_are_not(hass, aioclient_mock):
    c = await _stop(hass, aioclient_mock, persons=["device_tracker.car"])
    hass.states.async_set("device_tracker.phone", "not_home", GPS)  # not selected
    hass.states.async_set("person.jan", "not_home", GPS)  # not selected either
    await hass.async_block_till_done()
    assert c.nearby == {}
    hass.states.async_set("device_tracker.car", "not_home", GPS)
    await hass.async_block_till_done()
    assert list(c.nearby) == ["device_tracker.car"]


async def test_without_selection_only_persons_count(hass, aioclient_mock):
    c = await _stop(hass, aioclient_mock)
    hass.states.async_set("device_tracker.router_device", "home", GPS)
    await hass.async_block_till_done()
    assert c.nearby == {}


async def test_state_flapping_does_not_start_extra_visits(hass, aioclient_mock, monkeypatch):
    """unavailable -> back again within the cool-down is the same visit: no second entry refresh."""
    from custom_components.ovapi_bus_departures import coordinator as coord_mod  # noqa: PLC0415

    monkeypatch.setattr(coord_mod, "MIN_APPROACH_REFRESH_GAP", 0)
    c = await _stop(hass, aioclient_mock)
    c.refresh_on_approach = True
    before = aioclient_mock.call_count
    hass.states.async_set("person.jan", "not_home", GPS)
    await hass.async_block_till_done()
    calls = aioclient_mock.call_count
    assert calls == before + 1  # the entry refresh
    for state in ("unavailable", "not_home", "unknown", "not_home"):
        hass.states.async_set("person.jan", state, GPS)
        await hass.async_block_till_done()
    assert aioclient_mock.call_count == calls
