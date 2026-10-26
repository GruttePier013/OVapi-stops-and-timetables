"""De zone-timer in echte tijd (los van test_integration: die bevriest de klok in een autouse-fixture)."""
import asyncio

from .test_integration import URL, entry, passes, setup


async def test_zone_timer_really_refreshes_in_real_time(hass, aioclient_mock, monkeypatch):
    """Met een korte interval in echte tijd: de timer ververst herhaaldelijk en stopt als iedereen weg is."""
    aioclient_mock.get(URL.format("90100421"), json=passes("90100421"))
    e = entry([{"tpc_codes": ["90100421"], "name": "K"}])
    await setup(hass, e)
    coordinator = e.runtime_data.coordinators["90100421"]
    coordinator.zone_interval_min = 0.2 / 60  # 0.2 s
    coordinator.approach_only = False  # refresh at every tick while someone is in the zone
    hass.states.async_set("person.jan", "not_home", {"latitude": 52.0801, "longitude": 5.2501})
    await hass.async_block_till_done()
    start = aioclient_mock.call_count
    await asyncio.sleep(0.75)
    assert aioclient_mock.call_count >= start + 2  # meerdere verversingen binnen de zone
    hass.states.async_set("person.jan", "not_home", {"latitude": 52.50, "longitude": 5.25})
    await hass.async_block_till_done()
    stopped = aioclient_mock.call_count
    await asyncio.sleep(0.5)
    assert aioclient_mock.call_count == stopped  # niemand meer in de zone: geen verversingen
