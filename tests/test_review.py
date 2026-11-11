"""Regression tests of the code review: entity ids, write storms, request loops, limits on downloads and unpacking."""
import asyncio
import gzip

import pytest
from homeassistant.components.diagnostics import async_redact_data
from homeassistant.helpers import entity_registry as er

from custom_components.ovapi_bus_departures import chb
from custom_components.ovapi_bus_departures import coordinator as coordinator_module
from custom_components.ovapi_bus_departures.api import DutchBusApi
from custom_components.ovapi_bus_departures.config_flow import DutchBusConfigFlow, _md
from custom_components.ovapi_bus_departures.const import DOMAIN
from custom_components.ovapi_bus_departures.diagnostics import TO_REDACT
from custom_components.ovapi_bus_departures.parser import sort_lines, sorted_lines
from custom_components.ovapi_bus_departures.texts import Texts

from .test_integration import URL, _tz, entry, passes, setup  # noqa: F401

LOC = {"lat": 52.08, "lon": 4.25}


def eid(hass, key, domain="sensor", code="90100421"):
    return er.async_get(hass).async_get_entity_id(domain, DOMAIN, f"{code}_{key}")


async def test_line_with_letters_gives_valid_lowercase_entity_id(hass, aioclient_mock, freezer, caplog):
    freezer.move_to("2026-10-21 14:00:00+02:00")
    extra = {"j9": {"LinePublicNumber": "N74", "DestinationName50": "Z", "TargetDepartureTime": "2026-10-21T14:30:00",
                    "ExpectedDepartureTime": "2026-10-21T14:30:00", "TripStopStatus": "PLANNED"}}
    aioclient_mock.get(URL.format("90100421"), json=passes("90100421", extra))
    await setup(hass, entry([{"tpc_codes": ["90100421"], "name": "K"}]))
    assert hass.states.get("sensor.ovapi_k_line_n74") is not None
    assert "invalid entity ID" not in caplog.text


async def test_proximity_change_only_writes_the_proximity_entities(hass, aioclient_mock):
    aioclient_mock.get(URL.format("90100421"), json=passes("90100421"))
    await setup(hass, entry([{"tpc_codes": ["90100421"], "name": "K", **LOC}]))
    c = hass.config_entries.async_entries(DOMAIN)[0].runtime_data.coordinators["90100421"]
    changed: list[str] = []
    hass.bus.async_listen("state_changed", lambda ev: changed.append(ev.data["entity_id"]))
    c.set_proximity({"person.a": 120}, 120, trigger=False, nearest_entity="person.a")
    await hass.async_block_till_done()
    assert sorted(changed) == ["sensor.ovapi_k_distance_to_person", "sensor.ovapi_k_persons_nearby"]


async def test_manual_refresh_has_a_minimum_gap_against_looping_automations(hass, aioclient_mock, monkeypatch):
    monkeypatch.setattr(coordinator_module, "MANUAL_MIN_GAP", 30)
    aioclient_mock.get(URL.format("90100421"), json=passes("90100421"))
    await setup(hass, entry([{"tpc_codes": ["90100421"], "name": "K"}]))
    calls = aioclient_mock.call_count
    for _ in range(5):  # e.g. an automation that refreshes on every state change of the stop
        await hass.services.async_call(DOMAIN, "refresh", {}, blocking=True)
    assert aioclient_mock.call_count == calls  # the startup refresh was just now: nothing extra


async def test_unexpected_error_counts_as_a_failure_and_keeps_the_data(hass, aioclient_mock, monkeypatch):
    aioclient_mock.get(URL.format("90100421"), json=passes("90100421"))
    await setup(hass, entry([{"tpc_codes": ["90100421"], "name": "K"}]))
    c = hass.config_entries.async_entries(DOMAIN)[0].runtime_data.coordinators["90100421"]

    def boom(*_a, **_kw):
        raise KeyError("surprise")

    monkeypatch.setattr(coordinator_module, "parse_passes", boom)
    await c.async_manual_refresh()
    assert c.consecutive_failures == 1 and "KeyError" in (c.last_error or "") and c.data is not None


async def test_only_numeric_tpc_codes_reach_the_request_path(hass, aioclient_mock):
    api = DutchBusApi(hass)
    for bad in (["1/../admin"], ["12?x=1"], ["١٢٣"], []):
        with pytest.raises(ValueError):
            await api.async_fetch_tpcs(bad)
    assert aioclient_mock.call_count == 0


async def test_failing_server_is_not_hit_once_per_code(hass, aioclient_mock):
    aioclient_mock.get(URL.format("1,2,3,4"), status=503)
    api = DutchBusApi(hass)
    api.configure(spacing=0, max_errors=10)
    result = await api.async_fetch_group(["1", "2", "3", "4"], auto=False)
    assert all(isinstance(v, Exception) for v in result.values())
    assert aioclient_mock.call_count == 3  # the combined request with its 2 retries; no request per code


def test_listing_must_point_to_the_same_host():
    html = '<a href="https://evil.example/ExportCHB_2026-10-22.xml.gz">x</a>'
    with pytest.raises(ValueError):
        chb.parse_listing(html, "https://data.ndovloket.nl/haltes/")
    ok = '<a href="ExportCHB_2026-10-22.xml.gz">x</a>'
    assert chb.parse_listing(ok, "https://data.ndovloket.nl/haltes/")[0].startswith("https://data.ndovloket.nl/")


def test_gzip_bomb_is_refused(tmp_path, monkeypatch):
    path = tmp_path / "bomb.xml.gz"
    path.write_bytes(gzip.compress(b"<a>" + b"<b>x</b>" * 200_000 + b"</a>"))
    monkeypatch.setattr(chb, "MAX_UNPACKED_BYTES", 100_000)
    with pytest.raises(ValueError, match="unpacks to more"):
        list(chb._iter_xml(path, None))


async def test_oversized_download_is_refused_and_leaves_no_file(hass, aioclient_mock, tmp_path, monkeypatch):
    from custom_components.ovapi_bus_departures import api as api_module  # noqa: PLC0415
    monkeypatch.setattr(api_module, "MAX_DOWNLOAD_BYTES", 1000)
    aioclient_mock.get("https://data.ndovloket.nl/big.gz", content=b"x" * 5000)
    api = DutchBusApi(hass)
    dest = tmp_path / "big.gz"
    with pytest.raises(ValueError, match="larger than expected"):
        await api._async_download("https://data.ndovloket.nl/big.gz", dest, None)
    assert not dest.exists() and not dest.with_suffix(".part").exists()


def test_broken_translation_does_not_break_texts():
    t = Texts({f"component.{DOMAIN}.exceptions.line_at.message": "Line {nonexistent} {"})
    assert t("line_at", line="74", time="14:00") == "Line 74 at 14:00"
    assert t("unknown_key") == "unknown_key"


async def test_closing_the_flow_cancels_the_running_download(hass):
    flow = DutchBusConfigFlow()
    flow.hass = hass
    flow._task = asyncio.ensure_future(asyncio.sleep(60))
    task = flow._task
    flow.async_remove()
    await asyncio.sleep(0)
    assert task.cancelled() and flow._task is None


def test_markdown_in_names_is_escaped():
    assert _md("[click](https://evil)") == r"\[click\]\(https://evil\)"
    assert _md("Station *Noord*") == r"Station \*Noord\*"
    assert _md("Voorbeeldlaan") == "Voorbeeldlaan"


def test_unicode_digits_do_not_break_line_sorting():
    assert list(sort_lines({"²": "²", "74": "74", "N1": "N1"})) == ["74", "N1", "²"]
    assert sorted_lines(["²", "74", "9"]) == ["9", "74", "²"]


def test_diagnostics_redact_addresses_and_markers():
    data = {"options": {"address": "Voorbeeldlaan 1", "stops": [{"marker": {"lat": 1, "lon": 2}, "lat": 1.0}]}}
    out = async_redact_data(data, TO_REDACT)
    assert out["options"]["address"] == "**REDACTED**" and out["options"]["stops"][0]["marker"] == "**REDACTED**"


async def test_line_sensor_ids_are_stable_across_reload(hass, aioclient_mock, freezer):
    freezer.move_to("2026-10-21 14:00:00+02:00")
    aioclient_mock.get(URL.format("90100421"), json=passes("90100421"))
    e = entry([{"tpc_codes": ["90100421"], "name": "K"}])
    await setup(hass, e)
    before = {x.entity_id for x in er.async_entries_for_config_entry(er.async_get(hass), e.entry_id)}
    assert await hass.config_entries.async_reload(e.entry_id)
    await hass.async_block_till_done()
    after = {x.entity_id for x in er.async_entries_for_config_entry(er.async_get(hass), e.entry_id)}
    assert before == after


async def test_live_flag_per_departure(hass, aioclient_mock, freezer):
    freezer.move_to("2026-10-21 14:00:00+02:00")
    aioclient_mock.get(URL.format("90100421"), json=passes("90100421"))
    await setup(hass, entry([{"tpc_codes": ["90100421"], "name": "K"}]))
    deps = hass.states.get(eid(hass, "departures")).attributes
    assert [(d["line"], d["live"]) for d in deps["departures"]] == [("74", True), ("34", False)]  # DRIVING / PLANNED
    assert deps["live_count"] == 1
    assert hass.states.get("sensor.ovapi_k_next_departure").attributes["live"] is True
    assert hass.states.get("sensor.ovapi_k_line_34").attributes["live"] is False


async def test_live_only_option_hides_timetable_only_departures_from_states(hass, aioclient_mock, freezer):
    freezer.move_to("2026-10-21 14:10:00+02:00")  # bus 74 (DRIVING) is gone, only 34 (PLANNED) is left
    aioclient_mock.get(URL.format("90100421"), json=passes("90100421"))
    e = entry([{"tpc_codes": ["90100421"], "name": "K", **LOC}], live_only=True)
    await setup(hass, e)
    assert hass.states.get("sensor.ovapi_k_next_departure").state == "unknown"
    assert hass.states.get("sensor.ovapi_k_line_34").state == "unknown"
    deps = hass.states.get(eid(hass, "departures"))
    assert deps.state == "No departures"
    assert [(d["line"], d["live"]) for d in deps.attributes["departures"]] == [("34", False)]  # still listed
    # without the option the planned bus does count
    await hass.config_entries.async_unload(e.entry_id)
    hass.config_entries.async_update_entry(e, options={})
    await hass.config_entries.async_setup(e.entry_id)
    await hass.async_block_till_done()
    assert hass.states.get("sensor.ovapi_k_next_departure").state != "unknown"
