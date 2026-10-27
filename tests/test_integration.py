"""Integratietests: setup, sensoren, flows (OVapi gemockt)."""
import os
import re
import time
from datetime import timedelta

import pytest
from homeassistant.core import HomeAssistant, State
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_capture_events,
    async_fire_time_changed,
    mock_restore_cache,
)

from custom_components.ovapi_bustijden import chb
from custom_components.ovapi_bustijden.api import get_api
from custom_components.ovapi_bustijden.const import DOMAIN

from .chb_data import chb_xml, gz, listing_html, psa_xml

URL = "https://v0.ovapi.nl/tpc/{}"


def passes(code, extra=None):
    p = {
        "j1": {"LinePublicNumber": "74", "DestinationName50": "Station Centraal",
               "TargetDepartureTime": "2026-10-21T14:05:00", "ExpectedDepartureTime": "2026-10-21T14:07:00",
               "TripStopStatus": "DRIVING"},
        "j2": {"LinePublicNumber": "34", "DestinationName50": "Voorbeeldstad",
               "TargetDepartureTime": "2026-10-21T14:20:00", "ExpectedDepartureTime": "2026-10-21T14:20:00",
               "TripStopStatus": "PLANNED"},
    }
    p.update(extra or {})
    return {code: {"Stop": {"TimingPointName": "Voorbeeldlaan", "TimingPointTown": "Voorbeeldstad",
                            "Latitude": 52.08, "Longitude": 5.25}, "Passes": p}}


@pytest.fixture(autouse=True)
async def _tz(hass: HomeAssistant, freezer):
    await hass.config.async_set_time_zone("Europe/Amsterdam")
    freezer.move_to("2026-10-21 14:00:00+02:00")


def entry(stops, **options):
    e = MockConfigEntry(domain=DOMAIN, data={"stops": stops}, options=options)
    return e


async def setup(hass, e):
    e.add_to_hass(hass)
    assert await hass.config_entries.async_setup(e.entry_id)
    await hass.async_block_till_done()


async def test_legacy_stop_format_and_states(hass, aioclient_mock):
    aioclient_mock.get(URL.format("90100421"), json=passes("90100421"))
    # 0.1.x-formaat (tpc_code) moet blijven werken
    await setup(hass, entry([{"tpc_code": "90100421", "stop_name": "Voorbeeldlaan"}]))
    reg = er.async_get(hass)
    eid = reg.async_get_entity_id("sensor", DOMAIN, "90100421_departures")
    st = hass.states.get(eid)
    assert st.state == "14:07 (Lijn 74)"
    v = st.attributes["vertrekken"]
    assert [d["tijd"] for d in v] == ["14:07", "14:20"]
    assert v[0]["gepland"] == "14:05" and v[0]["vertraging_min"] == 2 and v[0]["over_min"] == 7
    assert st.attributes["data_leeftijd_min"] == 0 and "bijgewerkt" in st.attributes
    nxt = hass.states.get(reg.async_get_entity_id("sensor", DOMAIN, "90100421_next_departure"))
    assert nxt.state == "2026-10-21T12:07:00+00:00" and nxt.attributes["lijn"] == "74"


async def test_minute_ticker_updates_without_refetch(hass, aioclient_mock, freezer):
    aioclient_mock.get(URL.format("90100421"), json=passes("90100421"))
    await setup(hass, entry([{"tpc_codes": ["90100421"], "name": "K"}]))
    eid = er.async_get(hass).async_get_entity_id("sensor", DOMAIN, "90100421_departures")
    n = aioclient_mock.call_count
    freezer.move_to("2026-10-21 14:08:00+02:00")
    async_fire_time_changed(hass, dt_util.utcnow())
    await hass.async_block_till_done()
    assert aioclient_mock.call_count == n  # geen nieuwe aanvraag (buiten verversvenster)
    assert hass.states.get(eid).state == "14:20 (Lijn 34)"


async def test_combined_stops_line_filter_and_partial_failure(hass, aioclient_mock):
    aioclient_mock.get(URL.format("90000123"), json=passes("90000123"))
    aioclient_mock.get(URL.format("90003330"), status=500)
    await setup(hass, entry([{"tpc_codes": ["90000123", "90003330"], "name": "Station CS", "lines": ["34"]}]))
    eid = er.async_get(hass).async_get_entity_id("sensor", DOMAIN, "90000123_departures")
    st = hass.states.get(eid)
    assert st.state == "14:20 (Lijn 34)"  # filter op lijn 34
    assert st.attributes["gedeeltelijk"] is True


async def test_all_sources_fail_is_unavailable_and_button_retries(hass, aioclient_mock):
    aioclient_mock.get(URL.format("90100421"), status=500)
    await setup(hass, entry([{"tpc_codes": ["90100421"], "name": "K"}]))
    reg = er.async_get(hass)
    eid = reg.async_get_entity_id("sensor", DOMAIN, "90100421_departures")
    assert hass.states.get(eid).state == "unavailable"
    aioclient_mock.clear_requests()
    aioclient_mock.get(URL.format("90100421"), json=passes("90100421"))
    btn = reg.async_get_entity_id("button", DOMAIN, "90100421_refresh")
    await hass.services.async_call("button", "press", {"entity_id": btn}, blocking=True)
    await hass.async_block_till_done()
    assert hass.states.get(eid).state == "14:07 (Lijn 74)"


async def test_flow_manual_creates_entry(hass, aioclient_mock):
    aioclient_mock.get(URL.format("90100421"), json=passes("90100421"))
    r = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
    assert r["type"] is FlowResultType.FORM and r["step_id"] == "user"  # defaults screen
    r = await hass.config_entries.flow.async_configure(r["flow_id"], {})
    assert r["type"] is FlowResultType.MENU
    r = await hass.config_entries.flow.async_configure(r["flow_id"], {"next_step_id": "manual"})
    aioclient_mock.get(URL.format("999"), json={})
    r = await hass.config_entries.flow.async_configure(r["flow_id"], {"tpc_codes": "999"})
    assert r["errors"] == {"base": "tpc_not_found"}
    r = await hass.config_entries.flow.async_configure(r["flow_id"], {"tpc_codes": "90100421"})
    assert r["type"] is FlowResultType.FORM and r["step_id"] == "stop_details"
    opts = r["data_schema"].schema["lines"].config["options"]  # lijnen live uit OVapi
    assert [o["value"] for o in opts] == ["34", "74"] and opts[1]["label"] == "74 → Station Centraal"
    r = await hass.config_entries.flow.async_configure(r["flow_id"], {"name": "", "lines": ["74", "34"], "extra_lines": "350"})
    assert r["type"] is FlowResultType.CREATE_ENTRY
    stop = r["data"]["stops"][0]
    assert stop["name"] == "Voorbeeldstad, Voorbeeldlaan" and stop["tpc_codes"] == ["90100421"] and stop["lines"] == ["74", "34", "350"]
    assert stop["lat"] == 52.08 and stop["lon"] == 5.25


DEFAULTS = {
    "default_schedule": "never",
    "default_radius": 500,
    "default_refresh_on_approach": True,
    "default_approach_only": True,
    "default_zone_interval": "every_5_min",
    "default_zone_stop_after": "30_min",
}


def settings_input(**over):
    """Input of the Settings step (sections included)."""
    defaults = {**DEFAULTS, "apply_existing": False, **over.pop("defaults", {})}
    protection = {"max_errors": 5, "api_max_errors": 10, "api_spacing": 3, **over.pop("protection", {})}
    return {
        "persons": [],
        "zone_cooldown": 15,
        "stale_hours": 12,
        "defaults": defaults,
        "protection": protection,
        **over,
    }


async def test_options_settings_and_remove(hass, aioclient_mock):
    aioclient_mock.get(URL.format("90100421"), json=passes("90100421"))
    e = entry([{"tpc_codes": ["90100421"], "name": "K"}])
    await setup(hass, e)
    r = await hass.config_entries.options.async_init(e.entry_id)
    r = await hass.config_entries.options.async_configure(r["flow_id"], {"next_step_id": "settings"})
    r = await hass.config_entries.options.async_configure(r["flow_id"], settings_input(stale_hours=6))
    assert r["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()
    assert e.options["stale_hours"] == 6
    r = await hass.config_entries.options.async_init(e.entry_id)
    r = await hass.config_entries.options.async_configure(r["flow_id"], {"next_step_id": "remove_stops"})
    r = await hass.config_entries.options.async_configure(r["flow_id"], {"remove": ["90100421"]})
    await hass.async_block_till_done()
    assert e.options["stops"] == []
    assert er.async_get(hass).async_get_entity_id("sensor", DOMAIN, "90100421_departures") is None
    assert not dr.async_entries_for_config_entry(dr.async_get(hass), e.entry_id)




async def _select(hass, eid, option):
    await hass.services.async_call("select", "select_option", {"entity_id": eid, "option": option}, blocking=True)
    await hass.async_block_till_done()


async def test_schedule_select_controls_refreshes(hass, aioclient_mock, freezer):
    aioclient_mock.get(URL.format("90100421"), json=passes("90100421"))
    await setup(hass, entry([{"tpc_codes": ["90100421"], "name": "K"}]))
    sel = er.async_get(hass).async_get_entity_id("select", DOMAIN, "90100421_refresh_schedule")
    assert hass.states.get(sel).state == "never"  # standaard: alleen knop/nabijheid
    n = aioclient_mock.call_count
    freezer.move_to("2026-10-21 16:05:00+02:00")
    async_fire_time_changed(hass, dt_util.utcnow()); await hass.async_block_till_done()
    assert aioclient_mock.call_count == n  # nooit
    await _select(hass, sel, "every_5_min_rush")

    freezer.move_to("2026-10-21 14:30:00+02:00")  # buiten spits: geen refresh
    async_fire_time_changed(hass, dt_util.utcnow()); await hass.async_block_till_done()
    assert aioclient_mock.call_count == n
    freezer.move_to("2026-10-21 16:35:00+02:00")  # in spits: wel
    async_fire_time_changed(hass, dt_util.utcnow()); await hass.async_block_till_done()
    assert aioclient_mock.call_count == n + 1

    await _select(hass, sel, "never")
    freezer.move_to("2026-10-21 16:40:00+02:00")
    async_fire_time_changed(hass, dt_util.utcnow()); await hass.async_block_till_done()
    assert aioclient_mock.call_count == n + 1  # nooit

    await _select(hass, sel, "hourly")
    freezer.move_to("2026-10-21 17:00:00+02:00")
    async_fire_time_changed(hass, dt_util.utcnow()); await hass.async_block_till_done()
    assert aioclient_mock.call_count == n + 2


async def test_schedule_restored_after_restart(hass, aioclient_mock):
    aioclient_mock.get(URL.format("90100421"), json=passes("90100421"))
    mock_restore_cache(hass, [State("select.k_vernieuwingsfrequentie", "hourly")])
    await setup(hass, entry([{"tpc_codes": ["90100421"], "name": "K"}]))
    assert hass.states.get("select.k_vernieuwingsfrequentie").state == "hourly"


async def test_diagnostic_count_map_url_and_location(hass, aioclient_mock):
    extra = {"j3": {"LinePublicNumber": "74", "DestinationName50": "X", "TargetDepartureTime": "2026-10-21T14:30:00",
                    "ExpectedDepartureTime": "2026-10-21T14:30:00", "TripStopStatus": "CANCEL"},
             "j4": {"LinePublicNumber": "73", "DestinationName50": "Eind", "ExpectedArrivalTime": "2026-10-21T14:31:00"}}
    aioclient_mock.get(URL.format("90100421"), json=passes("90100421", extra))
    e = entry([{"tpc_codes": ["90100421"], "name": "K"}])
    await setup(hass, e)
    reg = er.async_get(hass)
    cnt = hass.states.get(reg.async_get_entity_id("sensor", DOMAIN, "90100421_fetched_departures"))
    assert cnt.state == "2" and cnt.attributes["opgehaald"] == 3  # nog in de toekomst: j1, j2 (j3 geannuleerd, j4 = aankomst)
    assert cnt.attributes["passes_in_antwoord"] == 4 and cnt.attributes["geannuleerd"] == 1
    # kaart: apparaat-link en device_tracker (positie uit live OVapi-antwoord)
    dev = dr.async_get(hass).async_get_device(identifiers={(DOMAIN, "90100421")})
    assert dev.configuration_url.startswith("https://www.openstreetmap.org/?mlat=52.08")
    trk = hass.states.get(reg.async_get_entity_id("sensor", DOMAIN, "90100421_location"))
    assert trk.attributes["latitude"] == 52.08 and trk.attributes["longitude"] == 5.25


async def test_zero_departures_counts_zero(hass, aioclient_mock):
    aioclient_mock.get(URL.format("90100421"), json={"90100421": {"Stop": {}, "Passes": {}}})
    await setup(hass, entry([{"tpc_codes": ["90100421"], "name": "K"}]))
    eid = er.async_get(hass).async_get_entity_id("sensor", DOMAIN, "90100421_fetched_departures")
    assert hass.states.get(eid).state == "0"


async def test_proximity_names_zone_timer_and_refresh(hass, aioclient_mock, freezer):
    aioclient_mock.get(URL.format("90100421"), json=passes("90100421"))
    await setup(hass, entry([{"tpc_codes": ["90100421"], "name": "K"}]))
    reg = er.async_get(hass)
    sw = reg.async_get_entity_id("switch", DOMAIN, "90100421_refresh_on_approach")
    num = reg.async_get_entity_id("number", DOMAIN, "90100421_proximity_radius")
    who = reg.async_get_entity_id("sensor", DOMAIN, "90100421_nearby_count")
    assert reg.async_get_entity_id("binary_sensor", DOMAIN, "90100421_nearby") is None  # 'Persoon nabij' bestaat niet meer
    assert float(hass.states.get(num).state) == 500
    assert hass.states.get(who).state == "Geen" and hass.states.get(who).attributes["personen"] == []
    assert "zone-radius" in hass.states.get(sw).attributes["uitleg"]
    n = aioclient_mock.call_count

    freezer.tick(timedelta(minutes=5))
    hass.states.async_set("person.jan", "not_home", {"latitude": 52.50, "longitude": 5.25, "friendly_name": "Jan"})
    await hass.async_block_till_done()
    assert hass.states.get(who).state == "Geen" and aioclient_mock.call_count == n  # ver weg
    assert hass.states.get(who).attributes["dichtstbij_m"] > 40000

    # in de zone: de status toont de naam (geen aantal) en er wordt direct ververst
    hass.states.async_set("person.jan", "not_home", {"latitude": 52.0815, "longitude": 5.2505, "friendly_name": "Jan"})  # ~170 m
    await hass.async_block_till_done()
    assert hass.states.get(who).state == "Jan"
    assert hass.states.get(who).attributes["personen"] == ["Jan"] and hass.states.get(who).attributes["aantal"] == 1
    assert hass.states.get(who).attributes["person_entiteiten"] == ["person.jan"]
    assert aioclient_mock.call_count == n + 1

    # zolang Jan in de zone blijft loopt er een bezoek met een timer (alleen dan)
    coord = hass.config_entries.async_entries(DOMAIN)[0].runtime_data.coordinators["90100421"]
    assert coord._unsub_zone is not None and coord.refresh_on_approach is True
    before = aioclient_mock.call_count
    await coord._async_zone_tick(dt_util.utcnow())  # 'alleen bij naderen' staat aan en Jan beweegt niet: geen verversing
    assert aioclient_mock.call_count == before
    coord.approach_only = False
    await coord._async_zone_tick(dt_util.utcnow())  # wat de timer elke X minuten doet
    assert aioclient_mock.call_count == before + 1
    # schakelaar uit: timer weg; weer aan (iemand is nog in de zone): timer terug
    await hass.services.async_call("switch", "turn_off", {"entity_id": sw}, blocking=True)
    assert coord._unsub_zone is None and hass.states.get(who).state == "Jan"
    await hass.services.async_call("switch", "turn_on", {"entity_id": sw}, blocking=True)
    assert coord._unsub_zone is not None
    # twee personen: namen gesorteerd en gescheiden door een komma
    hass.states.async_set("person.anna", "not_home", {"latitude": 52.0815, "longitude": 5.2505, "friendly_name": "Anna"})
    await hass.async_block_till_done()
    assert hass.states.get(who).state == "Anna, Jan"
    # straal verkleinen: 170 m valt er dan buiten; de timer ververst niet meer
    await hass.services.async_call("number", "set_value", {"entity_id": num, "value": 100}, blocking=True)
    await hass.async_block_till_done()
    assert hass.states.get(who).state == "Geen"
    calls = aioclient_mock.call_count
    await coord._async_zone_tick(dt_util.utcnow())
    assert aioclient_mock.call_count == calls  # niemand meer in de zone: geen verversing


async def test_zone_timer_default_is_every_five_minutes(hass, aioclient_mock, monkeypatch):
    from datetime import timedelta as td  # noqa: PLC0415

    from custom_components.ovapi_bustijden import coordinator as coord_mod  # noqa: PLC0415
    seen = []
    real = coord_mod.async_track_time_interval

    def spy(hass_, action, interval, **kw):
        seen.append(interval)
        return real(hass_, action, interval, **kw)

    monkeypatch.setattr(coord_mod, "async_track_time_interval", spy)
    aioclient_mock.get(URL.format("90100421"), json=passes("90100421"))
    await setup(hass, entry([{"tpc_codes": ["90100421"], "name": "K"}]))
    hass.states.async_set("person.jan", "not_home", {"latitude": 52.0801, "longitude": 5.2501})
    await hass.async_block_till_done()
    assert seen == [td(minutes=5)]  # één timer, standaard elke 5 minuten


async def test_old_binary_sensor_is_removed_from_registry(hass, aioclient_mock):
    aioclient_mock.get(URL.format("90100421"), json=passes("90100421"))
    e = entry([{"tpc_codes": ["90100421"], "name": "K"}])
    e.add_to_hass(hass)
    reg = er.async_get(hass)
    old = reg.async_get_or_create("binary_sensor", DOMAIN, "90100421_nearby", config_entry=e)
    await hass.config_entries.async_setup(e.entry_id)
    await hass.async_block_till_done()
    assert reg.async_get(old.entity_id) is None


async def test_combined_tpc_request_single_call_and_messages(hass, aioclient_mock):
    p = passes("90000123")["90000123"]
    p["GeneralMessages"] = {"m1": {"MessageContent": "Omleiding door wegwerkzaamheden"}}
    aioclient_mock.get(URL.format("90000123,90003330"), json={"90000123": p, "90003330": passes("90003330")["90003330"]})
    await setup(hass, entry([{"tpc_codes": ["90000123", "90003330"], "name": "Station CS"}]))
    assert aioclient_mock.call_count == 1  # één verzoek voor beide codes
    reg = er.async_get(hass)
    st = hass.states.get(reg.async_get_entity_id("sensor", DOMAIN, "90000123_departures"))
    assert st.attributes["berichten"] == ["Omleiding door wegwerkzaamheden"]
    assert "latitude" not in st.attributes and len(st.attributes["vertrekken"]) == 4  # 2 bussen x 2 codes
    assert {d["tpc"] for d in st.attributes["vertrekken"]} == {"90000123", "90003330"}
    from homeassistant.const import MATCH_ALL  # noqa: PLC0415

    from custom_components.ovapi_bustijden.sensor import Departures, NextDeparture
    assert MATCH_ALL in Departures._unrecorded_attributes  # attributen niet in de recorder
    assert MATCH_ALL in NextDeparture._unrecorded_attributes




async def test_dutch_names_and_single_location_entity(hass, aioclient_mock):
    assert hass.config.language == "en"  # ook met Engelse Home Assistant-taal Nederlandse namen
    aioclient_mock.get(URL.format("90100421"), json=passes("90100421"))
    e = entry([{"tpc_codes": ["90100421"], "name": "Voorbeeldstad, Voorbeeldlaan"}])
    await setup(hass, e)
    ids = {st.entity_id: st for st in hass.states.async_all() if st.entity_id.split(".")[0] in ("sensor", "select", "number", "switch", "button", "binary_sensor", "device_tracker")}
    for expected in ("sensor.voorbeeldstad_voorbeeldlaan_vertrektijden", "sensor.voorbeeldstad_voorbeeldlaan_volgende_vertrek",
                     "select.voorbeeldstad_voorbeeldlaan_vernieuwingsfrequentie", "button.voorbeeldstad_voorbeeldlaan_vernieuwen",
                     "number.voorbeeldstad_voorbeeldlaan_zone_radius", "switch.voorbeeldstad_voorbeeldlaan_vernieuwen_als_in_zone",
                     "sensor.voorbeeldstad_voorbeeldlaan_lijnen", "sensor.voorbeeldstad_voorbeeldlaan_personen_nabij",
                     "sensor.voorbeeldstad_voorbeeldlaan",
                     "select.voorbeeldstad_voorbeeldlaan_vernieuwen_in_zone_stoppen_na",
                     "select.voorbeeldstad_voorbeeldlaan_vernieuwingsfrequentie_in_zone",
                     "switch.voorbeeldstad_voorbeeldlaan_alleen_vernieuwen_bij_naderen",
                     "binary_sensor.voorbeeldstad_voorbeeldlaan_gepauzeerd",
                     "sensor.voorbeeldstad_voorbeeldlaan_afstand_tot_persoon"):
        assert expected in ids, expected
    # precies één entiteit met een positie => één marker op de kaart, met het OVapi bussen en haltelogo i.p.v. initialen
    with_pos = [x for x in ids.values() if "latitude" in x.attributes]
    assert [x.entity_id for x in with_pos] == ["sensor.voorbeeldstad_voorbeeldlaan"]
    assert with_pos[0].attributes["entity_picture"] == "/ovapi_bustijden_static/halte_marker.png"
    from pathlib import Path  # noqa: PLC0415
    assert (Path(__file__).parent.parent / "custom_components/ovapi_bustijden/static/halte_marker.png").is_file()
    # apparaatinfo: bron OVapi en NDOV
    dev = dr.async_get(hass).async_get_device(identifiers={(DOMAIN, "90100421")})
    assert dev.manufacturer == "OVapi en NDOV" and dev.model == "OVapi bussen en halteinformatie"


async def test_map_logo_can_be_switched_off(hass, aioclient_mock):
    aioclient_mock.get(URL.format("90100421"), json=passes("90100421"))
    e = entry([{"tpc_codes": ["90100421"], "name": "K"}])
    e.add_to_hass(hass)
    hass.config_entries.async_update_entry(e, options={"map_logo": False})
    await hass.config_entries.async_setup(e.entry_id)
    await hass.async_block_till_done()
    trk = hass.states.get(er.async_get(hass).async_get_entity_id("sensor", DOMAIN, "90100421_location"))
    assert "entity_picture" not in trk.attributes and trk.attributes["icon"] == "mdi:bus-stop"


async def test_lines_sensor_sorted_with_list_attribute(hass, aioclient_mock):
    extra = {"x1": {"LinePublicNumber": "350", "DestinationName50": "A", "TargetDepartureTime": "2026-10-21T14:30:00",
                    "ExpectedDepartureTime": "2026-10-21T14:30:00", "TripStopStatus": "PLANNED"},
             "x2": {"LinePublicNumber": "73", "DestinationName50": "B", "TargetDepartureTime": "2026-10-21T14:20:00",
                    "ExpectedDepartureTime": "2026-10-21T14:20:00", "TripStopStatus": "PLANNED"}}
    aioclient_mock.get(URL.format("90100421"), json=passes("90100421", extra))
    await setup(hass, entry([{"tpc_codes": ["90100421"], "name": "K"}]))
    eid = er.async_get(hass).async_get_entity_id("sensor", DOMAIN, "90100421_lines")
    st = hass.states.get(eid)
    assert st.state == "34, 73, 74, 350"  # numeriek gesorteerd, als string
    assert st.attributes["lijnen"] == ["34", "73", "74", "350"] and st.attributes["aantal"] == 4  # lijst voor code


async def test_lines_sensor_says_geen_without_departures(hass, aioclient_mock):
    aioclient_mock.get(URL.format("90100421"), json={"90100421": {"Stop": {}, "Passes": {}}})
    await setup(hass, entry([{"tpc_codes": ["90100421"], "name": "K"}]))
    st = hass.states.get(er.async_get(hass).async_get_entity_id("sensor", DOMAIN, "90100421_lines"))
    assert st.state == "Geen" and st.attributes["lijnen"] == []


def test_display_name_dedupes_town():
    from custom_components.ovapi_bustijden.config_flow import _display_name
    assert _display_name({"name": "Voorbeeldstad, Voorbeeldlaan", "town": "Voorbeeldstad"}, "x") == "Voorbeeldstad, Voorbeeldlaan"
    assert _display_name({"name": "Voorbeeldlaan", "town": "Voorbeeldstad"}, "x") == "Voorbeeldstad, Voorbeeldlaan"
    assert _display_name(None, "Halte 1") == "Halte 1"










async def test_edit_details_offers_live_lines_and_back(hass, aioclient_mock):
    aioclient_mock.get(URL.format("90100421"), json=passes("90100421"))
    e = entry([{"tpc_codes": ["90100421"], "name": "K", "lines": ["350"]}])
    await setup(hass, e)
    r = await hass.config_entries.options.async_init(e.entry_id)
    r = await hass.config_entries.options.async_configure(r["flow_id"], {"next_step_id": "edit_stop"})
    r = await hass.config_entries.options.async_configure(r["flow_id"], {"stop": "90100421"})
    assert r["step_id"] == "edit_details"
    values = [o["value"] for o in r["data_schema"].schema["lines"].config["options"]]
    assert values == ["34", "74", "350"]  # live lijnen + al gekozen lijn
    r = await hass.config_entries.options.async_configure(r["flow_id"], {"name": "K", "tpc_codes": "90100421", "lines": ["74"], "extra_lines": ""})
    assert r["type"] is FlowResultType.CREATE_ENTRY and e.options["stops"][0]["lines"] == ["74"]


# ------------------------------------------------------------------ configflow: NDOV-haltebestand, voortgang, checkboxes

NDOV = "http://data.ndovloket.nl/haltes"


def _isolate_cache(hass, tmp_path):
    api = get_api(hass)
    api.cache_dir = tmp_path
    api.chb_gz, api.psa_gz = tmp_path / "chb.xml.gz", tmp_path / "chb-psa.xml.gz"
    api.index_path = tmp_path / "chb_index.json"
    return api


def _mock_ndov(aioclient_mock, psa=True):
    chb_bytes, psa_bytes = gz(chb_xml()), gz(psa_xml())
    aioclient_mock.get("https://data.ndovloket.nl/haltes/", text=listing_html())
    aioclient_mock.get(f"{NDOV}/ExportCHB_2026-10-22.xml.gz", content=chb_bytes,
                       headers={"Content-Length": str(len(chb_bytes))})
    if psa:
        aioclient_mock.get(f"{NDOV}/PassengerStopAssignmentExportCHB_2026-10-22.xml.gz", content=psa_bytes,
                           headers={"Content-Length": str(len(psa_bytes))})
    else:
        aioclient_mock.get(f"{NDOV}/PassengerStopAssignmentExportCHB_2026-10-22.xml.gz", status=404)


def _tpc(name, lat, lon, passes_):
    return {"Stop": {"TimingPointName": name, "TimingPointTown": "Voorbeeldstad", "Latitude": lat, "Longitude": lon},
            "Passes": passes_}


def _pass(line, dest, hhmm):
    return {"LinePublicNumber": line, "DestinationName50": dest, "TripStopStatus": "PLANNED",
            "TargetDepartureTime": f"2026-10-21T{hhmm}:00", "ExpectedDepartureTime": f"2026-10-21T{hhmm}:00"}


def _mock_live(aioclient_mock):
    """OVapi kent 90100411/90100451 (één paal, twee vervoerders), 90100412 en 82000101; 90100777 niet."""
    live = {
        "90100411": passes("90100411")["90100411"],  # lijn 74 -> Station Centraal, lijn 34 -> Voorbeeldstad
        "90100451": _tpc("Voorbeeldlaan", 52.0801, 5.2501, {"k": _pass("73", "Voorbeeldstad Centrum", "14:09")}),
        "90100412": _tpc("Voorbeeldlaan", 52.0793, 5.2488, {"m": _pass("74", "Noordstad", "14:12")}),
        "82000101": _tpc("Voorbeeldweg", 52.0804, 5.2507, {"n": _pass("350", "Station Overvecht", "14:15")}),
    }
    aioclient_mock.get(re.compile(r"https://v0\.ovapi\.nl/tpc/.*"), json=live)


async def _advance_to_form(hass, r, phases=None):
    for _ in range(12):
        if r["type"] is FlowResultType.SHOW_PROGRESS:
            if phases is not None:
                phases.append(r["progress_action"])
            await hass.async_block_till_done()
        elif r["type"] is not FlowResultType.SHOW_PROGRESS_DONE:
            break
        r = await hass.config_entries.flow.async_configure(r["flow_id"])
    return r


async def _open_search(hass):
    r = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
    assert r["type"] is FlowResultType.FORM and r["step_id"] == "user"  # defaults screen
    r = await hass.config_entries.flow.async_configure(r["flow_id"], {})
    return await hass.config_entries.flow.async_configure(r["flow_id"], {"next_step_id": "search"})


ZONE = {"latitude": 52.08, "longitude": 5.25, "radius": 250}


async def test_flow_downloads_unpacks_and_lists_stops(hass, aioclient_mock, tmp_path):
    from homeassistant.helpers.selector import LocationSelector  # noqa: PLC0415
    hass.config.language = "nl"
    _isolate_cache(hass, tmp_path)
    _mock_ndov(aioclient_mock)
    _mock_live(aioclient_mock)
    events = async_capture_events(hass, "data_entry_flow_progress_update")
    r = await _open_search(hass)
    # kaart bovenaan, daaronder adres en coördinaten; de kaartkiezer heeft een sleepbare straal
    fields = list(r["data_schema"].schema)
    assert [str(k) for k in fields] == ["place", "location", "modes"]  # adres/plaatsnaam boven de kaart
    assert isinstance(r["data_schema"].schema[fields[1]], LocationSelector)
    assert r["data_schema"].schema[fields[1]].config["radius"] is True
    assert fields[1].description["suggested_value"]["radius"] == 250

    r = await hass.config_entries.flow.async_configure(r["flow_id"], {"location": ZONE})
    assert r["type"] is FlowResultType.SHOW_PROGRESS and r["progress_action"] == "download"
    phases: list[str] = []
    r = await _advance_to_form(hass, r, phases)
    assert phases[:2] == ["download", "unpack"]  # download en uitpakken elk met een eigen voortgangsring

    # direct een lijst met checkboxes (één per halte); geen kaartje, geen apart overzicht meer
    assert r["step_id"] == "select_stop" and "overview" not in r["description_placeholders"]
    ph = r["description_placeholders"]
    assert ph["found"].startswith("3 richtingen gevonden.") and "1 haltes zonder live gegevens" in ph["found"]
    fields = r["data_schema"].schema
    assert [str(k) for k in fields] == ["stop_1", "stop_2", "stop_3"]  # alleen de checkboxen; de rest staat in een eigen stap
    assert all(k.default() is False for k in fields)
    # checkbox = halte, TPC, afstand lopen | windrichting; eronder (helpertekst) de huidige vertrektijden per lijn
    labels = [ph[f"stop_{i}_label"] for i in (1, 2, 3)]
    infos = [ph[f"stop_{i}_info"] for i in (1, 2, 3)]
    i_first = next(i for i, label in enumerate(labels) if "90100411, 90100451" in label)
    i_other = next(i for i, label in enumerate(labels) if "TPC 90100412" in label)
    assert re.fullmatch(r"Voorbeeldlaan · 📍 TPC 90100411, 90100451 · \d+,\d km (lopen )?van huis \| ZW ↙", labels[i_first])
    assert infos[i_first].startswith("Huidige vertrektijden OVapi:\n\n- 🚌 **74** → Station Centraal · 14:07")
    assert "- 🚌 **73** → Voorbeeldstad Centrum · 14:09" in infos[i_first]  # beide vervoerders op dezelfde paal samengevoegd
    assert "NO ↗" in labels[i_other] and "- 🚌 **74** → Noordstad · 14:12" in infos[i_other]  # overkant = eigen richting
    assert any("Voorbeeldweg" in label for label in labels)

    # beide richtingen van de Voorbeeldlaan aanvinken, met richting in de naam -> twee apparaten
    r = await hass.config_entries.flow.async_configure(
        r["flow_id"], {f"stop_{i_first + 1}": True, f"stop_{i_other + 1}": True})
    assert r["step_id"] == "extra_options"
    assert [str(k) for k in r["data_schema"].schema] == ["direction_in_name", "stale_hours", "refresh_ndov"]
    # label van het ouderdomsvinkje: ouderdom volgens de datumstempel van het bestand
    assert (r["description_placeholders"]["ndov_days"], r["description_placeholders"]["ndov_date"]) == ("0", "2026-10-22")
    defaults = {str(k): k.default() for k in r["data_schema"].schema}
    assert defaults["direction_in_name"] is True and defaults["refresh_ndov"] is False  # richting in de naam standaard aan
    r = await hass.config_entries.flow.async_configure(r["flow_id"], {"direction_in_name": True, "stale_hours": 12})
    assert r["step_id"] == "stop_details" and r["description_placeholders"]["progress"] == "Richting 1 van 2"
    assert r["description_placeholders"]["direction"].startswith("74 → Station Centraal")
    r = await hass.config_entries.flow.async_configure(r["flow_id"], {"name": "", "lines": [], "extra_lines": ""})
    assert r["description_placeholders"]["progress"] == "Richting 2 van 2"
    r = await hass.config_entries.flow.async_configure(r["flow_id"], {"name": "", "lines": ["74"], "extra_lines": ""})
    first_stop, second_stop = sorted(r["data"]["stops"], key=lambda x: x["tpc_codes"])
    assert first_stop["name"] == "Voorbeeldstad, Voorbeeldlaan - ri. Station Centraal"  # afgekort: ' - ri. '
    assert first_stop["tpc_codes"] == ["90100411", "90100451"] and first_stop["stop_code"] == "NL:Q:90100411"
    assert second_stop["name"] == "Voorbeeldstad, Voorbeeldlaan - ri. Noordstad" and second_stop["lines"] == ["74"]
    assert second_stop["tpc_codes"] == ["90100412"] and second_stop["distance_m"] > 1_000_000  # afstand tot huis (testhuis ligt ver weg)

    # alleen NDOV Loket en OVapi-TPC's: geen GTFS, haltegebiedenlijst of kaarttegels
    urls = [str(call[1]) for call in aioclient_mock.mock_calls]
    assert all(("ndovloket.nl" in u) or ("v0.ovapi.nl/tpc/" in u) for u in urls)
    # elke fase begon bij 0 % en eindigde op 100 %
    values = [e.data["progress"] for e in events]
    assert values.count(0.0) >= 3 and values.count(1.0) >= 3
    # de index blijft bewaard (.storage), de grote downloads zijn opgeruimd
    assert (tmp_path / "chb_index.json").exists() and not (tmp_path / "chb.xml.gz").exists()


async def test_flow_skips_download_when_index_is_fresh(hass, aioclient_mock, tmp_path):
    api = _isolate_cache(hass, tmp_path)
    (tmp_path / "c.gz").write_bytes(gz(chb_xml()))
    chb.build_index(tmp_path / "c.gz", None, api.index_path)
    _mock_live(aioclient_mock)
    r = await _open_search(hass)
    r = await hass.config_entries.flow.async_configure(r["flow_id"], {"location": ZONE})
    phases: list[str] = []
    r = await _advance_to_form(hass, r, phases)
    assert phases == ["load_stops"] and r["step_id"] == "select_stop"
    assert not any("ndovloket" in str(call[1]) for call in aioclient_mock.mock_calls)


async def test_flow_download_failure_aborts_or_uses_stale_index(hass, aioclient_mock, tmp_path):
    api = _isolate_cache(hass, tmp_path)
    aioclient_mock.get("https://data.ndovloket.nl/haltes/", status=503)
    aioclient_mock.get("http://data.ndovloket.nl/haltes/", status=503)
    _mock_live(aioclient_mock)
    # zonder index: afbreken met een duidelijke melding
    r = await _open_search(hass)
    r = await hass.config_entries.flow.async_configure(r["flow_id"], {"location": ZONE})
    r = await _advance_to_form(hass, r)
    assert r["type"] is FlowResultType.ABORT and r["reason"] == "chb_failed"
    # met een verouderde index: toch doorgaan met de oude gegevens
    (tmp_path / "c.gz").write_bytes(gz(chb_xml()))
    chb.build_index(tmp_path / "c.gz", None, api.index_path)
    old = time.time() - 30 * 24 * 3600
    os.utime(api.index_path, (old, old))
    r = await _open_search(hass)
    r = await hass.config_entries.flow.async_configure(r["flow_id"], {"location": ZONE})
    calls_before = aioclient_mock.call_count
    r = await _advance_to_form(hass, r)
    assert r["step_id"] == "select_stop" and [str(k) for k in r["data_schema"].schema][:3] == ["stop_1", "stop_2", "stop_3"]
    assert any("ndovloket" in str(c[1]) for c in aioclient_mock.mock_calls[:calls_before])  # download is wél geprobeerd


async def test_flow_zone_precedence_radius_clamp_and_no_stops(hass, monkeypatch, tmp_path):
    api = _isolate_cache(hass, tmp_path)
    (tmp_path / "c.gz").write_bytes(gz(chb_xml()))
    chb.build_index(tmp_path / "c.gz", None, api.index_path)
    seen = []

    async def fake_candidates(lat, lon, radius, tz, now, progress=None, modes=("bus",)):
        seen.append((lat, lon, radius))
        return [], 0

    monkeypatch.setattr(api, "async_candidates", fake_candidates)
    flow = hass.config_entries.flow
    r = await _open_search(hass)
    # de straal komt uit de kaartkiezer en wordt begrensd (50–5000 m)
    for given, expected in ((400, 400), (10, 50), (99999, 5000)):
        r = await flow.async_configure(
            r["flow_id"], {"location": {**ZONE, "radius": given}})
        r = await _advance_to_form(hass, r)
        assert r["type"] is FlowResultType.ABORT and r["reason"] == "no_stops"
        assert seen[-1] == (52.08, 5.25, expected)
        r = await _open_search(hass)


async def test_candidates_live_validation_and_other_side_of_street(hass, aioclient_mock, tmp_path):
    api = _isolate_cache(hass, tmp_path)
    (tmp_path / "c.gz").write_bytes(gz(chb_xml()))
    (tmp_path / "p.gz").write_bytes(gz(psa_xml()))
    chb.build_index(tmp_path / "c.gz", tmp_path / "p.gz", api.index_path)
    _mock_live(aioclient_mock)
    found, skipped = await api.async_candidates(52.08, 5.25, 60, dt_util.DEFAULT_TIME_ZONE, dt_util.now())
    # in de zone: 411 en 777 (geen live gegevens -> overgeslagen); de overkant (412) gaat mee
    assert [c["quay"] for c in found] == ["NL:Q:90100411", "NL:Q:90100412"] and skipped == 1
    assert found[0]["tpc_codes"] == ["90100411", "90100451"] and set(found[0]["lines"]) == {"34", "73", "74"}
    assert found[0]["destination"] == "Station Centraal" and found[1]["destination"] == "Noordstad"
    assert found[1]["distance_m"] > 60 and found[0]["bearing"] == 225


async def test_download_and_unpack_report_real_progress(hass, aioclient_mock, tmp_path, monkeypatch):
    monkeypatch.setattr(chb, "CHUNK", 256)
    api = _isolate_cache(hass, tmp_path)
    _mock_ndov(aioclient_mock)
    down, unpack = [], []
    await api.async_download_chb(down.append)
    assert down[-1] == 1.0 and down == sorted(down) and any(0 < x < 1 for x in down)
    assert api.chb_gz.exists() and api.psa_gz.exists() and await api.async_chb_state() == "missing"
    assert await api.async_build_chb_index(unpack.append) == 6
    await hass.async_block_till_done()
    assert unpack[-1] == 1.0 and any(0.05 < x < 0.99 for x in unpack)
    assert not api.chb_gz.exists() and not api.psa_gz.exists()  # 13 MB-download opgeruimd
    assert await api.async_chb_state() == "fresh"
    quays = await api.async_quays_near(52.08, 5.25, 250)
    assert {q["quay"] for q in quays} >= {"NL:Q:90100411", "NL:Q:82000101"}


async def test_missing_assignment_table_falls_back_to_quay_number(hass, aioclient_mock, tmp_path):
    api = _isolate_cache(hass, tmp_path)
    _mock_ndov(aioclient_mock, psa=False)
    await api.async_download_chb()
    assert not api.psa_gz.exists()
    await api.async_build_chb_index()
    quays = {q["quay"]: q for q in await api.async_quays_near(52.08, 5.25, 250)}
    assert quays["NL:Q:90100411"]["tpc_candidates"] == ["90100411"]  # zonder verwijstabel: het quaynummer


async def test_results_step_offers_ndov_refresh_and_flags_missing_departures(hass, aioclient_mock, tmp_path):
    """Vraag in de lijst om het haltebestand te verversen: terug naar stap 1 (downloaden) met dezelfde zone."""
    hass.config.language = "nl"
    api = _isolate_cache(hass, tmp_path)
    (tmp_path / "c.gz").write_bytes(gz(chb_xml()))
    chb.build_index(tmp_path / "c.gz", None, api.index_path, source_date="2026-10-01")  # vers genoeg, geen download
    _mock_ndov(aioclient_mock)
    live = {"90100411": _tpc("Hoofdlaan", 52.0801, 5.2501, {})}  # bekend bij OVapi, maar geen vertrekken
    aioclient_mock.get(re.compile(r"https://v0\.ovapi\.nl/tpc/.*"), json=live)
    r = await _open_search(hass)
    r = await hass.config_entries.flow.async_configure(r["flow_id"], {"location": ZONE})
    phases: list[str] = []
    r = await _advance_to_form(hass, r, phases)
    assert phases == ["load_stops"] and not any("ndovloket" in str(c[1]) for c in aioclient_mock.mock_calls)
    ph = r["description_placeholders"]
    assert ph["stop_1_info"] == "⚠️ Geen vertrektijden informatie"
    r = await hass.config_entries.flow.async_configure(r["flow_id"], {"stop_1": True})
    assert r["step_id"] == "extra_options"
    assert (r["description_placeholders"]["ndov_days"], r["description_placeholders"]["ndov_date"]) == ("20", "2026-10-01")
    # opnieuw downloaden aanvinken -> download en uitpakken draaien opnieuw, daarna weer dezelfde lijst
    r = await hass.config_entries.flow.async_configure(r["flow_id"], {"refresh_ndov": True})
    phases = []
    r = await _advance_to_form(hass, r if r["type"] is not FlowResultType.SHOW_PROGRESS else r, phases)
    assert r["step_id"] == "select_stop"
    assert any("ndovloket" in str(c[1]) for c in aioclient_mock.mock_calls)
    r = await hass.config_entries.flow.async_configure(r["flow_id"], {"stop_1": True})
    assert r["description_placeholders"]["ndov_date"] == "2026-10-22" and r["description_placeholders"]["ndov_days"] == "0"
    assert api._source_date == "2026-10-22"


def test_checkbox_translations_cover_every_possible_stop():
    import json  # noqa: PLC0415
    from pathlib import Path  # noqa: PLC0415

    from custom_components.ovapi_bustijden.const import PREVIEW_MAX_STOPS  # noqa: PLC0415
    base = Path(__file__).parent.parent / "custom_components/ovapi_bustijden/translations"
    for lang in ("nl", "en"):
        d = json.loads((base / f"{lang}.json").read_text(encoding="utf-8"))
        for flow in ("config", "options"):
            step = d[flow]["step"]["select_stop"]
            for i in range(1, PREVIEW_MAX_STOPS + 1):
                assert step["data"][f"stop_{i}"] == f"{{stop_{i}_label}}"        # halte, TPC, afstand, windrichting
                assert step["data_description"][f"stop_{i}"] == f"{{stop_{i}_info}}"  # vertrektijden eronder
            extra = d[flow]["step"]["extra_options"]
            assert "{ndov_days}" in extra["data"]["refresh_ndov"] and "{ndov_date}" in extra["data"]["refresh_ndov"]
            assert extra["data"]["direction_in_name"] and extra["data_description"]["refresh_ndov"]
            assert "{found}" in step["description"]
            assert "- ri." in extra["data"]["direction_in_name"] or "- ri." in extra["data_description"]["direction_in_name"]


async def test_names_without_direction_by_default_and_compass_when_duplicate(hass, aioclient_mock, tmp_path):
    """Standaard staat er geen richting in de naam; dubbele namen worden op windrichting onderscheiden."""
    hass.config.language = "nl"
    api = _isolate_cache(hass, tmp_path)
    (tmp_path / "c.gz").write_bytes(gz(chb_xml()))
    (tmp_path / "p.gz").write_bytes(gz(psa_xml()))
    chb.build_index(tmp_path / "c.gz", tmp_path / "p.gz", api.index_path)
    _mock_live(aioclient_mock)
    r = await _open_search(hass)
    r = await hass.config_entries.flow.async_configure(r["flow_id"], {"location": ZONE})
    r = await _advance_to_form(hass, r)
    ph = r["description_placeholders"]
    both = [i for i in (1, 2, 3) if "Voorbeeldlaan" in ph[f"stop_{i}_label"]]
    r = await hass.config_entries.flow.async_configure(r["flow_id"], {f"stop_{i}": True for i in both})
    r = await hass.config_entries.flow.async_configure(r["flow_id"], {})  # extra opties: alles standaard
    r = await hass.config_entries.flow.async_configure(r["flow_id"], {"name": "", "lines": [], "extra_lines": ""})
    r = await hass.config_entries.flow.async_configure(r["flow_id"], {"name": "", "lines": [], "extra_lines": ""})
    assert sorted(x["name"] for x in r["data"]["stops"]) == ["Voorbeeldstad, Voorbeeldlaan - ri. Noordstad", "Voorbeeldstad, Voorbeeldlaan - ri. Station Centraal"]
    assert all("- ri." in x["name"] for x in r["data"]["stops"])  # richting in de naam staat standaard aan


def test_brand_logos_are_transparent_with_rounded_corners():
    from pathlib import Path  # noqa: PLC0415

    from PIL import Image  # noqa: PLC0415
    brand = Path(__file__).parent.parent / "custom_components/ovapi_bustijden/brand"
    for name in ("icon", "icon@2x", "logo", "logo@2x"):
        im = Image.open(brand / f"{name}.png").convert("RGBA")
        assert im.getpixel((0, 0))[3] == 0 and im.getpixel((im.width - 1, im.height - 1))[3] == 0
        assert im.getpixel((im.width // 2, im.height // 2))[3] > 0 or im.getpixel((im.width // 2, im.height // 3))[3] > 0


async def test_stale_data_service_and_zone_stop(hass, aioclient_mock, freezer):
    from datetime import timedelta  # noqa: PLC0415

    from homeassistant.util import dt as dt_util  # noqa: PLC0415
    from pytest_homeassistant_custom_component.common import async_fire_time_changed  # noqa: PLC0415
    aioclient_mock.get(URL.format("90100421"), json=passes("90100421"))
    await setup(hass, entry([{"tpc_codes": ["90100421"], "name": "K"}]))
    reg = er.async_get(hass)
    ids = {k: reg.async_get_entity_id("sensor", DOMAIN, f"90100421_{k}") for k in ("location", "departures", "lines")}
    assert hass.states.get(ids["location"]).state.startswith("Lijn ")
    assert hass.states.get(ids["departures"]).attributes["icon"] == "mdi:bus-clock"
    # standaardwaarden van de nieuwe instellingen
    sel = reg.async_get_entity_id("select", DOMAIN, "90100421_zone_stop_after")
    assert hass.states.get(sel).state == "30_min"
    assert hass.states.get(reg.async_get_entity_id("select", DOMAIN, "90100421_refresh_schedule")).state == "never"
    assert hass.states.get(reg.async_get_entity_id("switch", DOMAIN, "90100421_refresh_on_approach")).state == "on"
    # ouder dan 12 uur => 'Data vernieuwen' met doorgestreept icoon, zonder marker-plaatje
    freezer.tick(timedelta(hours=12, minutes=1))
    async_fire_time_changed(hass, dt_util.utcnow())
    await hass.async_block_till_done()
    for k, icon in (("location", "mdi:map-marker-off"), ("departures", "mdi:sync-off"), ("lines", "mdi:sync-off")):
        st = hass.states.get(ids[k])
        assert st.state == "Data vernieuwen" and st.attributes["icon"] == icon
    assert hass.states.get(ids["location"]).attributes["entity_picture"].endswith("/halte_onbekend.png")
    # service zonder doel vernieuwt alles en haalt de gegevens weer op
    await hass.services.async_call(DOMAIN, "refresh", {}, blocking=True)
    assert hass.states.get(ids["departures"]).state != "Data vernieuwen"
    # service met doel: alleen die halte
    n = aioclient_mock.call_count
    await hass.services.async_call(DOMAIN, "refresh_stops", {"entity_id": ids["lines"]}, blocking=True)
    assert aioclient_mock.call_count > n
    n = aioclient_mock.call_count
    await hass.services.async_call(DOMAIN, "refresh_stops", {"entity_id": "sensor.bestaat_niet"}, blocking=True)
    assert aioclient_mock.call_count == n


async def test_stale_timeout_is_configurable(hass, aioclient_mock, freezer):
    from datetime import timedelta  # noqa: PLC0415

    from homeassistant.util import dt as dt_util  # noqa: PLC0415
    from pytest_homeassistant_custom_component.common import async_fire_time_changed  # noqa: PLC0415
    aioclient_mock.get(URL.format("90100421"), json=passes("90100421"))
    e = entry([{"tpc_codes": ["90100421"], "name": "K"}])
    e.add_to_hass(hass)
    hass.config_entries.async_update_entry(e, options={"stale_hours": 2})
    await hass.config_entries.async_setup(e.entry_id)
    await hass.async_block_till_done()
    eid = er.async_get(hass).async_get_entity_id("sensor", DOMAIN, "90100421_location")
    assert hass.states.get(eid).attributes["entity_picture"].endswith("/halte_marker.png")
    freezer.tick(timedelta(hours=2, minutes=1))
    async_fire_time_changed(hass, dt_util.utcnow())
    await hass.async_block_till_done()
    assert hass.states.get(eid).state == "Data vernieuwen"
