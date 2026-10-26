import pytest

pytest_plugins = "pytest_homeassistant_custom_component"


@pytest.fixture(autouse=True)
def _auto_enable_custom_integrations(enable_custom_integrations):
    yield


@pytest.fixture(autouse=True)
def _no_retry_delay(monkeypatch):
    """Geen echte wachttijd tussen OVapi-pogingen in tests."""
    from custom_components.ovapi_bustijden import const  # noqa: PLC0415
    monkeypatch.setattr(const, "RETRY_DELAYS", (0, 0))
