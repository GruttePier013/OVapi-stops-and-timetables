"""Typos in the place or address: fuzzy matching and the PDOK spell checker."""
import re

import pytest
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMockResponse

from custom_components.ovapi_bus_departures.api import AddressNotFound, DutchBusApi, get_api, spelling_corrections
from custom_components.ovapi_bus_departures.const import DOMAIN


def doc(name, kind, lon=4.25, lat=52.08):
    return {"weergavenaam": name, "type": kind, "centroide_ll": f"POINT({lon} {lat})"}


TOWN = doc("Voorbeeldstad", "woonplaats", 4.3, 52.1)
STREET = doc("Voorbeeldlaan, Voorbeeldstad", "weg")


def router(aioclient_mock, free, suggest=None):
    """``free``: query text -> docs (404 for unknown queries); ``suggest``: spellcheck block."""
    calls: list[str] = []

    async def answer(method, url, data):
        query = url.query.get("q", "")
        calls.append(f"{url.name}:{query}")
        if url.name == "suggest":
            return AiohttpClientMockResponse(method, url, json={"response": {"docs": []}, "spellcheck": suggest or {}})
        if query not in free:
            return AiohttpClientMockResponse(method, url, status=400)
        return AiohttpClientMockResponse(method, url, json={"response": {"docs": free[query]}})

    aioclient_mock.get(re.compile(r"https://api\.pdok\.nl/.*"), side_effect=answer)
    return calls


async def test_exact_match_needs_one_request(hass, aioclient_mock):
    calls = router(aioclient_mock, {"Voorbeeldlaan Voorbeeldstad": [STREET, TOWN]})
    assert await DutchBusApi(hass).async_locate("Voorbeeldlaan Voorbeeldstad") == (52.08, 4.25, "Voorbeeldlaan Voorbeeldstad", "weg")
    assert calls == ["free:Voorbeeldlaan Voorbeeldstad"]


async def test_typo_in_street_is_corrected_by_fuzzy_search(hass, aioclient_mock):
    # PDOK only recognises the town for the misspelled street; the fuzzy query finds the street
    router(aioclient_mock, {"Voorbeeldlan Voorbeeldstad": [TOWN], "Voorbeeldlan~1 Voorbeeldstad~1": [STREET, TOWN]})
    lat, lon, text, kind = await DutchBusApi(hass).async_locate("Voorbeeldlan  Voorbeeldstad")
    assert (lat, lon, text, kind) == (52.08, 4.25, "Voorbeeldlaan Voorbeeldstad", "weg")


async def test_typo_in_place_is_corrected_by_the_spell_checker(hass, aioclient_mock):
    calls = router(
        aioclient_mock,
        {"Voorbeedstad": [], "Voorbeeldstad": [TOWN]},
        suggest={"collations": ["collation", "Voorbeeldstad"]},
    )
    assert await DutchBusApi(hass).async_locate("Voorbeedstad") == (52.1, 4.3, "Voorbeeldstad", "woonplaats")
    assert "suggest:Voorbeedstad" in calls


async def test_spell_checker_when_fuzzy_queries_are_refused(hass, aioclient_mock):
    router(aioclient_mock, {"Voorbeedstad": [], "Voorbeeldstad": [TOWN]}, suggest={"suggestions": ["Voorbeedstad", {"suggestion": ["Voorbeeldstad"]}]})
    assert (await DutchBusApi(hass).async_locate("Voorbeedstad"))[2] == "Voorbeeldstad"


async def test_unknown_place_still_fails(hass, aioclient_mock):
    router(aioclient_mock, {"Qqqqqq": []})
    with pytest.raises(AddressNotFound):
        await DutchBusApi(hass).async_locate("Qqqqqq")


async def test_unrecognised_but_only_result_is_kept(hass, aioclient_mock):
    """No better candidate: behave as before and use what PDOK found for the input."""
    router(aioclient_mock, {"Voorbeeldstad Centrum": [TOWN]})
    assert (await DutchBusApi(hass).async_locate("Voorbeeldstad Centrum"))[2:] == ("Voorbeeldstad Centrum", "woonplaats")


def test_spelling_corrections_shapes():
    assert spelling_corrections("abc", None) == []
    assert spelling_corrections("amstedam", {"collations": ["collation", "amsterdam"]}) == ["amsterdam"]
    assert spelling_corrections("amstedam", {"collations": [{"collationQuery": "amsterdam"}]}) == ["amsterdam"]
    flat = {"suggestions": ["amstedam", {"numFound": 1, "suggestion": [{"word": "amsterdam"}]}]}
    assert spelling_corrections("de amstedam", flat) == ["de amsterdam"]
    assert spelling_corrections("x", {"suggestions": "garbage", "collations": 5}) == []


async def test_flow_shows_the_corrected_spelling(hass, monkeypatch):
    calls = []

    async def fake_locate(text):
        calls.append(text)
        return 52.08, 4.25, "Voorbeeldlaan Voorbeeldstad", "weg"

    monkeypatch.setattr(get_api(hass), "async_locate", fake_locate)
    r = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
    r = await hass.config_entries.flow.async_configure(r["flow_id"], {})
    r = await hass.config_entries.flow.async_configure(r["flow_id"], {"next_step_id": "search"})
    loc = {"latitude": 52.0, "longitude": 4.0, "radius": 250}
    r = await hass.config_entries.flow.async_configure(r["flow_id"], {"place": "Voorbeeldlan Voorbeedstad", "location": loc})
    assert r["type"] is FlowResultType.FORM and r["step_id"] == "search"
    assert "Voorbeeldlan Voorbeedstad" in r["description_placeholders"]["corrected"]
    assert "Voorbeeldlaan Voorbeeldstad" in r["description_placeholders"]["corrected"]
    place = next(k for k in r["data_schema"].schema if str(k) == "place")
    assert place.description["suggested_value"] == "Voorbeeldlaan Voorbeeldstad"
    # submitting the corrected text does not look it up again (and the note is gone)
    r = await hass.config_entries.flow.async_configure(
        r["flow_id"], {"place": "Voorbeeldlaan Voorbeeldstad", "location": {"latitude": 52.08, "longitude": 4.25, "radius": 250}}
    )
    assert calls == ["Voorbeeldlan Voorbeedstad"]
    assert r.get("step_id") != "search"  # on to the stop file download
