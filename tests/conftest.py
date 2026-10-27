import pytest

pytest_plugins = "pytest_homeassistant_custom_component"


@pytest.fixture(autouse=True)
def _auto_enable_custom_integrations(enable_custom_integrations):
    yield


@pytest.fixture(autouse=True)
def _no_retry_delay(monkeypatch):
    """Geen echte wachttijd tussen OVapi-pogingen in tests."""
    from custom_components.ovapi_bus_departures import const  # noqa: PLC0415
    monkeypatch.setattr(const, "RETRY_DELAYS", (0, 0))


@pytest.fixture(autouse=True)
def _no_api_spacing(monkeypatch):
    """No waiting between automatic API calls in tests (the default is a few seconds)."""
    from custom_components.ovapi_bus_departures import const  # noqa: PLC0415
    monkeypatch.setattr(const, "DEFAULT_API_SPACING", 0.0)


@pytest.fixture(autouse=True)
def _no_auto_gap(monkeypatch):
    """The 60 s minimum between automatic refreshes would block most tests; one test covers it explicitly."""
    from custom_components.ovapi_bus_departures import coordinator  # noqa: PLC0415
    monkeypatch.setattr(coordinator, "AUTO_MIN_GAP", 0)


@pytest.fixture(autouse=True)
def _no_empty_refresh(monkeypatch):
    """Refresh-when-empty is off in tests (they move the clock past all departures); one test covers it."""
    from custom_components.ovapi_bus_departures import coordinator  # noqa: PLC0415
    monkeypatch.setattr(coordinator, "DAY_START_HOUR", 0)
    monkeypatch.setattr(coordinator, "DAY_END_HOUR", 0)
