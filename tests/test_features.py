"""Robuustheid en functies van 0.8: lege halte, sensoren per lijn, fouten, batch, SSL, diagnostics, herconfiguratie."""
import ssl
from unittest.mock import AsyncMock

import pytest
from homeassistant.const import EntityCategory
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import issue_registry as ir

from custom_components.ovapi_bus_departures import _async_check_ndov_age
from custom_components.ovapi_bus_departures.api import DutchBusApi
from custom_components.ovapi_bus_departures.const import DOMAIN
from custom_components.ovapi_bus_departures.diagnostics import async_get_config_entry_diagnostics

from .test_integration import URL, _tz, entry, passes, setup  # noqa: F401

LOC = {"lat": 52.08, "lon": 5.25}


def eid(hass, key, domain="sensor", code="90100421"):
    return er.async_get(hass).async_get_entity_id(domain, DOMAIN, f"{code}_{key}")


async def test_no_buses_at_all_jumps_to_data_vernieuwen(hass, aioclient_mock):
    aioclient_mock.get(URL.format("90100421"), json={"90100421": {"Stop": {}, "Passes": {}}})
    await setup(hass, entry([{"tpc_codes": ["90100421"], "name": "K", **LOC}]))
    loc = hass.states.get(eid(hass, "location"))
    assert loc.state == "Refresh data" and loc.attributes["entity_picture"].endswith("/stop_unknown.png")
    assert loc.attributes["icon"] == "mdi:map-marker-off"
    # tekstsensoren houden hun eigen lege-status
    assert hass.states.get(eid(hass, "departures")).state == "No departures"
    assert hass.states.get(eid(hass, "lines")).state == "None"


async def test_line_sensors_optional_and_dynamic(hass, aioclient_mock):
    aioclient_mock.get(URL.format("90100421"), json=passes("90100421"))
    e = entry([{"tpc_codes": ["90100421"], "name": "K"}])  # standaard aan
    await setup(hass, e)
    s74 = hass.states.get("sensor.k_line_74")
    assert s74 is not None and s74.state == "2026-10-21T12:07:00+00:00"
    assert s74.attributes["destination"] == "Station Centraal" and s74.attributes["minutes_until"] == 7
    assert hass.states.get("sensor.k_line_34") is not None
    # een nieuwe lijn bij een volgende verversing krijgt vanzelf een sensor
    extra = {"j9": {"LinePublicNumber": "350", "DestinationName50": "Zuidstad",
                    "TargetDepartureTime": "2026-10-21T14:30:00", "ExpectedDepartureTime": "2026-10-21T14:30:00",
                    "TripStopStatus": "PLANNED"}}
    aioclient_mock.clear_requests()
    aioclient_mock.get(URL.format("90100421"), json=passes("90100421", extra))
    await hass.services.async_call(DOMAIN, "refresh", {}, blocking=True)
    await hass.async_block_till_done()
    assert hass.states.get("sensor.k_line_350") is not None


async def test_line_sensors_can_be_switched_off(hass, aioclient_mock):
    aioclient_mock.get(URL.format("90100421"), json=passes("90100421"))
    await setup(hass, entry([{"tpc_codes": ["90100421"], "name": "K"}], line_sensors=False))
    assert hass.states.get("sensor.k_line_74") is None


async def test_last_data_stays_visible_after_failed_refresh(hass, aioclient_mock):
    aioclient_mock.get(URL.format("90100421"), json=passes("90100421"))
    await setup(hass, entry([{"tpc_codes": ["90100421"], "name": "K"}]))
    aioclient_mock.clear_requests()
    aioclient_mock.get(URL.format("90100421"), status=500)
    await hass.services.async_call(DOMAIN, "refresh", {}, blocking=True)
    st = hass.states.get(eid(hass, "departures"))
    assert st.state == "14:07 (Line 74)"  # niet 'unavailable'
    assert st.attributes["failed_attempts"] == 1 and "500" in st.attributes["last_error"]


async def test_retry_on_transient_error_then_success(hass, aioclient_mock, monkeypatch):
    from aiohttp import ClientResponseError, RequestInfo  # noqa: PLC0415
    from yarl import URL as YURL  # noqa: PLC0415

    api = DutchBusApi(hass)
    err = ClientResponseError(RequestInfo(YURL("http://x"), "GET", {}, YURL("http://x")), (), status=503)
    attempts = []

    async def flaky(path, timeout):
        attempts.append(path)
        if len(attempts) == 1:
            raise err
        return {"90100421": {}}

    monkeypatch.setattr(api, "_ovapi_get_once", flaky)
    assert await api._ovapi_get("tpc/90100421", retries=1) == {"90100421": {}}
    assert len(attempts) == 2  # tijdelijke fout (503): tweede poging slaagt
    attempts.clear()
    with pytest.raises(ClientResponseError):
        await api._ovapi_get("tpc/90100421", retries=0)  # zonder nieuwe pogingen faalt het meteen
    assert len(attempts) == 1
    # een 404 is geen tijdelijke fout: niet opnieuw proberen
    attempts.clear()
    not_found = ClientResponseError(RequestInfo(YURL("http://x"), "GET", {}, YURL("http://x")), (), status=404)

    async def missing(path, timeout):
        attempts.append(path)
        raise not_found

    monkeypatch.setattr(api, "_ovapi_get_once", missing)
    with pytest.raises(ClientResponseError):
        await api._ovapi_get("tpc/x", retries=2)
    assert len(attempts) == 1


async def test_ssl_error_falls_back_once_and_retries_secure_later(hass, monkeypatch):
    api = DutchBusApi(hass)
    calls = []

    async def fake_request(session, path, timeout):
        secure = session is api.session
        calls.append(secure)
        if secure:
            raise ssl.SSLError("certificate verify failed")
        return {"ok": {}}

    monkeypatch.setattr(api, "_ovapi_request", fake_request)
    assert await api._ovapi_get("tpc/1") == {"ok": {}}
    assert calls == [True, False]  # eerst met verificatie, daarna zonder
    calls.clear()
    await api._ovapi_get("tpc/1")
    assert calls == [False]  # daarna direct zonder (24 uur)
    api._insecure_since = None
    calls.clear()
    await api._ovapi_get("tpc/1")
    assert calls == [True, False]


async def test_multiple_stops_share_one_request(hass, aioclient_mock):
    payload = {**passes("90100421"), **passes("90100422")}
    aioclient_mock.get(URL.format("90100421,90100422"), json=payload)
    await setup(hass, entry([{"tpc_codes": ["90100421"], "name": "A"}, {"tpc_codes": ["90100422"], "name": "B"}]))
    assert aioclient_mock.call_count == 1
    assert hass.states.get(eid(hass, "departures", code="90100422")).state == "14:07 (Line 74)"


async def test_zone_selects_default_and_change(hass, aioclient_mock):
    aioclient_mock.get(URL.format("90100421"), json=passes("90100421"))
    e = entry([{"tpc_codes": ["90100421"], "name": "K"}])
    await setup(hass, e)
    sel = eid(hass, "zone_refresh", "select")
    assert hass.states.get(sel).state == "every_5_min"
    coord = e.runtime_data.coordinators["90100421"]
    assert coord.zone_interval_min == 5 and coord.zone_stop_after_min == 30
    await hass.services.async_call("select", "select_option", {"entity_id": sel, "option": "every_10_min"}, blocking=True)
    assert coord.zone_interval_min == 10
    await hass.services.async_call("select", "select_option", {"entity_id": sel, "option": "on_entry"}, blocking=True)
    assert coord.zone_interval_min is None  # once per visit
    stop = eid(hass, "zone_stop_after", "select")
    await hass.services.async_call("select", "select_option", {"entity_id": stop, "option": "unlimited"}, blocking=True)
    assert coord.zone_stop_after_min is None


async def test_destination_filter(hass, aioclient_mock):
    aioclient_mock.get(URL.format("90100421"), json=passes("90100421"))
    await setup(hass, entry([{"tpc_codes": ["90100421"], "name": "K", "destinations": ["voorbeeldstad"]}]))
    st = hass.states.get(eid(hass, "departures"))
    assert st.state == "14:20 (Line 34)" and [d["line"] for d in st.attributes["departures"]] == ["34"]


async def test_diagnostics_redacts_locations(hass, aioclient_mock):
    aioclient_mock.get(URL.format("90100421"), json=passes("90100421"))
    e = entry([{"tpc_codes": ["90100421"], "name": "K", **LOC}])
    await setup(hass, e)
    diag = await async_get_config_entry_diagnostics(hass, e)
    stop = diag["stops"]["90100421"]
    assert stop["config"]["lat"] == "**REDACTED**" and stop["departures_in_view"] == 2
    assert stop["health"]["consecutive_errors"] == 0 and stop["health"]["stale"] is False
    assert stop["raw_response"]["90100421"]["Passes"]  # raw OVapi answer of this stop


async def test_old_ndov_file_creates_repair_issue(hass):
    api = AsyncMock()
    api.async_chb_info.return_value = {"file_age_days": 120}
    await _async_check_ndov_age(hass, api)
    issue = ir.async_get(hass).async_get_issue(DOMAIN, "ndov_outdated")
    assert issue and issue.translation_placeholders["days"] == "120"
    api.async_chb_info.return_value = {"file_age_days": 10}
    await _async_check_ndov_age(hass, api)
    assert ir.async_get(hass).async_get_issue(DOMAIN, "ndov_outdated") is None
    api.async_chb_info.return_value = None  # geen index (alleen handmatige TPC-codes): geen melding
    await _async_check_ndov_age(hass, api)
    assert ir.async_get(hass).async_get_issue(DOMAIN, "ndov_outdated") is None


async def test_reconfigure_edits_stop_and_reloads(hass, aioclient_mock):
    aioclient_mock.get(URL.format("90100421"), json=passes("90100421"))
    e = entry([{"tpc_codes": ["90100421"], "name": "K"}])
    await setup(hass, e)
    r = await e.start_reconfigure_flow(hass)
    assert r["step_id"] == "edit_stop"
    r = await hass.config_entries.flow.async_configure(r["flow_id"], {"stop": "90100421"})
    assert r["step_id"] == "edit_details"
    r = await hass.config_entries.flow.async_configure(
        r["flow_id"], {"name": "Nieuwe naam", "tpc_codes": "90100421", "destinations": "station"})
    assert r["type"].value == "abort" and r["reason"] == "reconfigure_successful"
    await hass.async_block_till_done()
    stop = e.options["stops"][0]
    assert stop["name"] == "Nieuwe naam" and stop["destinations"] == ["station"]


async def test_stop_on_map_is_diagnostic(hass, aioclient_mock):
    aioclient_mock.get(URL.format("90100421"), json=passes("90100421"))
    await setup(hass, entry([{"tpc_codes": ["90100421"], "name": "K", **LOC}]))
    reg = er.async_get(hass).async_get(eid(hass, "location"))
    assert reg.entity_category == EntityCategory.DIAGNOSTIC


async def test_zone_switch_default_off_inside_home_zone(hass, aioclient_mock):
    hass.config.latitude, hass.config.longitude, hass.config.radius = 52.08, 5.25, 100
    aioclient_mock.get(URL.format("90100421"), json=passes("90100421"))
    aioclient_mock.get(URL.format("90100422"), json=passes("90100422"))
    await setup(hass, entry([
        {"tpc_codes": ["90100421"], "name": "Thuis", **LOC},          # in de thuiszone
        {"tpc_codes": ["90100422"], "name": "Ver weg", "lat": 52.2, "lon": 5.4},
    ]))
    assert hass.states.get(eid(hass, "refresh_on_approach", "switch", "90100421")).state == "off"
    assert hass.states.get(eid(hass, "refresh_on_approach", "switch", "90100422")).state == "on"


async def test_place_name_recenters_map_with_2km_radius(hass, monkeypatch):
    from homeassistant.data_entry_flow import FlowResultType  # noqa: PLC0415

    from custom_components.ovapi_bus_departures.api import get_api  # noqa: PLC0415
    calls = []

    kinds = iter(["woonplaats", "weg"])

    async def fake_locate(text):
        calls.append(text)
        return 52.0907, 5.2376, text, next(kinds)

    monkeypatch.setattr(get_api(hass), "async_locate", fake_locate)
    r = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
    assert r["type"] is FlowResultType.FORM and r["step_id"] == "user"  # defaults screen
    r = await hass.config_entries.flow.async_configure(r["flow_id"], {})
    r = await hass.config_entries.flow.async_configure(r["flow_id"], {"next_step_id": "search"})
    assert r["step_id"] == "search" and [str(k) for k in r["data_schema"].schema][0] == "place"
    loc = {"latitude": 52.0, "longitude": 5.0, "radius": 250}
    r = await hass.config_entries.flow.async_configure(r["flow_id"], {"place": "Voorbeeldstad", "location": loc})
    # nog niet zoeken: het formulier verschijnt opnieuw met de kaart op het centrum en 2 km radius
    assert r["type"] is FlowResultType.FORM and r["step_id"] == "search" and calls == ["Voorbeeldstad"]
    key = next(k for k in r["data_schema"].schema if str(k) == "location")
    assert key.description["suggested_value"] == {"latitude": 52.0907, "longitude": 5.2376, "radius": 2000}
    # straat + plaats: de pin op de straat, dichterbij (500 m)
    r = await hass.config_entries.flow.async_configure(r["flow_id"], {"place": "Voorbeeldlaan Voorbeeldstad", "location": loc})
    key = next(k for k in r["data_schema"].schema if str(k) == "location")
    assert calls == ["Voorbeeldstad", "Voorbeeldlaan Voorbeeldstad"] and key.description["suggested_value"]["radius"] == 500


async def test_map_entity_named_after_stop_and_remaining_count_decreases(hass, aioclient_mock, freezer):
    from homeassistant.util import dt as dt_util  # noqa: PLC0415
    from pytest_homeassistant_custom_component.common import async_fire_time_changed  # noqa: PLC0415
    aioclient_mock.get(URL.format("90100421"), json=passes("90100421"))
    await setup(hass, entry([{"tpc_codes": ["90100421"], "name": "Voorbeeldstad, Markt", **LOC}]))
    st = hass.states.get(eid(hass, "location"))
    assert st.entity_id == "sensor.voorbeeldstad_markt" and st.attributes["friendly_name"] == "Voorbeeldstad, Markt"
    cnt = eid(hass, "fetched_departures")
    assert hass.states.get(cnt).state == "2"
    freezer.move_to("2026-10-21 14:10:00+02:00")  # bus 74 (14:07) is weg
    async_fire_time_changed(hass, dt_util.utcnow())
    await hass.async_block_till_done()
    assert hass.states.get(cnt).state == "1"
    freezer.move_to("2026-10-21 14:25:00+02:00")  # ook 34 (14:20) is weg: 0 => halte zonder data
    async_fire_time_changed(hass, dt_util.utcnow())
    await hass.async_block_till_done()
    assert hass.states.get(cnt).state == "0"
    assert hass.states.get(eid(hass, "location")).state == "Refresh data"


async def test_home_distance_text_one_decimal_and_walking_only_below_2km(hass):
    from custom_components.ovapi_bus_departures.config_flow import StopFlowMixin  # noqa: PLC0415
    flow = StopFlowMixin()
    flow.hass = hass
    hass.config.latitude, hass.config.longitude = 52.0, 5.0
    near = {"lat": 52.0063, "lon": 5.0}   # ca. 700 m
    far = {"lat": 52.46, "lon": 5.0}      # ca. 51 km
    hass.config.language = "nl"
    await flow._async_load_texts()
    assert flow._home_distance_text(near) == "0,7 km lopen van huis"
    text = flow._home_distance_text(far)
    assert text.endswith(" km van huis") and "lopen" not in text and "," in text
    hass.config.language = "en"
    await flow._async_load_texts()
    assert flow._home_distance_text(near) == "0.7 km walk from home"
    assert flow._home_distance_text(far).endswith(" km from home")
    truncated = flow._t("list_truncated", shown=25, total=40)
    assert truncated.startswith("⚠️ **") and truncated.endswith("**") and "25" in truncated and "40" in truncated


async def test_marker_override_and_reset(hass, aioclient_mock):
    aioclient_mock.get(URL.format("90100421"), json=passes("90100421"))
    e = entry([{"tpc_codes": ["90100421"], "name": "K", **LOC}])
    await setup(hass, e)

    async def edit(extra):
        r = await e.start_reconfigure_flow(hass)
        r = await hass.config_entries.flow.async_configure(r["flow_id"], {"stop": "90100421"})
        r = await hass.config_entries.flow.async_configure(
            r["flow_id"], {"name": "K", "tpc_codes": "90100421", **extra})
        assert r["reason"] == "reconfigure_successful"
        await hass.async_block_till_done()

    await edit({"marker": {"latitude": 52.1, "longitude": 5.3}})
    assert e.options["stops"][0]["marker"] == {"lat": 52.1, "lon": 5.3}
    attrs = hass.states.get(eid(hass, "location")).attributes
    assert (attrs["latitude"], attrs["longitude"]) == (52.1, 5.3)
    await edit({"marker_reset": True, "marker": {"latitude": 52.1, "longitude": 5.3}})
    assert e.options["stops"][0]["marker"] is None
    assert hass.states.get(eid(hass, "location")).attributes["latitude"] == LOC["lat"]


def test_view_never_truncates_departures():
    from datetime import UTC, datetime, timedelta  # noqa: PLC0415

    from custom_components.ovapi_bus_departures.parser import build_view  # noqa: PLC0415

    now = datetime(2026, 10, 27, 12, 0, tzinfo=UTC)
    deps = [
        {"status": "PLANNED", "expected": now + timedelta(minutes=i + 1), "planned": now + timedelta(minutes=i + 1),
         "line": "34", "destination": "Station", "tpc": "1"}
        for i in range(35)
    ]
    assert len(build_view(deps, now)) == 35
