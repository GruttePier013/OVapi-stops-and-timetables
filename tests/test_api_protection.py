"""Circuit breaker, rate limiting and pacing of the OVapi client (no clock freezing: real short sleeps)."""
import asyncio
import time

import pytest

from custom_components.ovapi_bus_departures.api import DutchBusApi, OvapiBlocked

URL = "https://v0.ovapi.nl/tpc/{}"


async def test_circuit_breaker_and_manual_bypass(hass, aioclient_mock):
    aioclient_mock.get(URL.format("90100421"), status=500)
    api = DutchBusApi(hass)
    api.configure(spacing=0, max_errors=3)
    changes: list[bool] = []
    api.on_outage_change = changes.append
    for _ in range(3):
        result = await api.async_fetch_group(["90100421"], auto=True)
        assert isinstance(result["90100421"], Exception)
    assert api.outage and changes == [True] and api.consecutive_errors == 3
    calls = aioclient_mock.call_count
    with pytest.raises(OvapiBlocked):
        await api.async_fetch_group(["90100421"], auto=True)  # paused: no request is made
    assert aioclient_mock.call_count == calls
    aioclient_mock.clear_requests()
    aioclient_mock.get(URL.format("90100421"), json={"90100421": {"Stop": {}, "Passes": {}}})
    result = await api.async_fetch_group(["90100421"])  # manual refresh bypasses the pause; success resets it
    assert not isinstance(result["90100421"], Exception)
    assert not api.outage and changes == [True, False] and api.consecutive_errors == 0


async def test_rate_limit_uses_retry_after(hass, aioclient_mock):
    aioclient_mock.get(URL.format("90100421"), status=429, headers={"Retry-After": "120"})
    api = DutchBusApi(hass)
    api.configure(spacing=0, max_errors=10)
    await api.async_fetch_group(["90100421"], auto=True)
    assert api.outage and 100 < api.blocked_for <= 120
    assert aioclient_mock.call_count == 1  # a 429 is not retried and not split into single requests


async def test_rate_limit_without_retry_after_waits_a_default_time(hass, aioclient_mock):
    aioclient_mock.get(URL.format("90100421"), status=429)
    api = DutchBusApi(hass)
    api.configure(spacing=0, max_errors=10)
    await api.async_fetch_group(["90100421"], auto=True)
    assert api.outage and api.blocked_for > 250


async def test_backoff_grows_after_the_threshold(hass):
    api = DutchBusApi(hass)
    api.configure(spacing=0, max_errors=2)
    api._record_failure()
    assert not api.outage  # below the threshold
    api._record_failure()
    first = api.blocked_for
    api._record_failure()
    assert 50 < first <= 60 and api.blocked_for > first  # 60 s, then 120 s


async def test_automatic_calls_are_spaced(hass):
    api = DutchBusApi(hass)
    api.configure(spacing=0.05, max_errors=10)
    started = time.monotonic()
    await asyncio.gather(*(api._pace() for _ in range(4)))
    assert time.monotonic() - started >= 0.14  # three gaps of 0.05 s, although all started at once
    api.configure(spacing=0, max_errors=10)
    started = time.monotonic()
    await asyncio.gather(*(api._pace() for _ in range(4)))
    assert time.monotonic() - started < 0.05


async def test_mixed_batch_with_a_manual_refresh_is_not_blocked(hass, aioclient_mock):
    aioclient_mock.get(URL.format("90100421,90100422"), json={"90100421": {}, "90100422": {}})
    api = DutchBusApi(hass)
    api.configure(spacing=0, max_errors=1)
    api._record_failure()
    assert api.blocked
    auto, manual = await asyncio.gather(
        api.async_fetch_group(["90100421"], auto=True), api.async_fetch_group(["90100422"], auto=False)
    )
    assert "90100421" in auto and "90100422" in manual  # a manual refresh lets the whole batch through
