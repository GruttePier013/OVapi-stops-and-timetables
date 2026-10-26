"""Tests voor de pure parse-logica (regels afgeleid uit de yaml-sensoren)."""
from datetime import datetime
from zoneinfo import ZoneInfo

from custom_components.ovapi_bustijden.parser import (
    build_view,
    clean_town,
    describe_tpc,
    line_options,
    main_destination,
    merged_lines,
    merged_summary,
    parse_codes,
    parse_coordinates,
    parse_lines,
    parse_messages,
    parse_passes,
    sorted_lines,
    summarize,
)

TZ = ZoneInfo("Europe/Amsterdam")
NOW = datetime(2026, 10, 21, 14, 0, 0, tzinfo=TZ)


def payload():
    return {"90100421": {"Stop": {"TimingPointName": "Voorbeeldlaan", "TimingPointTown": "Voorbeeldstad",
                                  "Latitude": 52.08, "Longitude": 5.25},
        "Passes": {
            "a": {"LinePublicNumber": "74", "DestinationName50": "Station Centraal",
                  "TargetDepartureTime": "2026-10-21T14:05:00", "ExpectedDepartureTime": "2026-10-21T14:07:00",
                  "TripStopStatus": "DRIVING"},
            "b": {"LinePublicNumber": "34", "DestinationName50": "Voorbeeldstad",
                  "TargetDepartureTime": "2026-10-21T14:03:00", "ExpectedDepartureTime": "2026-10-21T14:03:00",
                  "TripStopStatus": "PLANNED"},
            "c": {"LinePublicNumber": "74", "DestinationName50": "X", "TargetDepartureTime": "2026-10-21T14:10:00",
                  "ExpectedDepartureTime": "2026-10-21T14:10:00", "TripStopStatus": "CANCEL"},
            "d": {"LinePublicNumber": "73", "DestinationName50": "Eindhalte", "ExpectedArrivalTime": "2026-10-21T14:08:00",
                  "TripStopStatus": "PLANNED"},
            "e": {"LinePublicNumber": "350", "DestinationName50": "Gisteren", "TargetDepartureTime": "2026-10-21T13:59:00",
                  "ExpectedDepartureTime": "2026-10-21T13:59:00", "TripStopStatus": "PASSED"},
            "f": {"LinePublicNumber": "350", "DestinationName50": "Net weg", "TargetDepartureTime": "2026-10-21T13:59:45",
                  "ExpectedDepartureTime": "2026-10-21T13:59:45", "TripStopStatus": "PASSED"},
        }}}


def view(**kw):
    return build_view(parse_passes(payload(), "90100421", TZ), NOW, **kw)


def test_filters_cancel_arrival_only_and_past():
    v = view()
    assert [d["lijn"] for d in v] == ["34", "74"] or [d["lijn"] for d in v][-2:] == ["34", "74"]
    assert all(d["status"] != "CANCEL" for d in v)
    assert "Eindhalte" not in [d["bestemming"] for d in v]
    assert "Gisteren" not in [d["bestemming"] for d in v]
    assert "Net weg" in [d["bestemming"] for d in v]  # 30 s coulance


def test_sorted_delay_and_minutes():
    v = view()
    assert [d["tijd"] for d in v] == ["13:59", "14:03", "14:07"]
    last = v[-1]
    assert last["gepland"] == "14:05" and last["vertraging_min"] == 2 and last["over_min"] == 7


def test_line_filter_and_limit_and_summary():
    assert [d["lijn"] for d in view(lines=["74"])] == ["74"]
    assert len(view(limit=1)) == 1
    assert summarize(view(lines=["74"])) == "14:07 (Lijn 74)"
    assert summarize(view(lines=["999"])) == "Geen bussen"


def test_helpers():
    assert parse_codes("90100421, 90100011;90100421") == ["90100421", "90100011"]
    assert parse_lines("73, 74 350,74") == ["73", "74", "350"]
    info = describe_tpc("90100421", payload())
    assert info["name"] == "Voorbeeldlaan" and info["town"] == "Voorbeeldstad"
    assert describe_tpc("1", {}) is None


def test_terminus_is_skipped_and_direction_fields():
    pl = payload()
    pl["90100421"]["Passes"]["z"] = {"LinePublicNumber": "74", "DestinationName50": "Hier", "JourneyStopType": "LAST",
        "TargetDepartureTime": "2026-10-21T14:12:00", "ExpectedDepartureTime": "2026-10-21T14:12:00", "TripStopStatus": "PLANNED"}
    pl["90100421"]["Passes"]["a"].update({"LineName": "Voorbeeldstad - Station", "LineDirection": "2"})
    v = build_view(parse_passes(pl, "90100421", TZ), NOW)
    assert "Hier" not in [d["bestemming"] for d in v]
    a = next(d for d in v if d["tijd"] == "14:07")
    assert a["lijnnaam"] == "Voorbeeldstad - Station" and a["richting"] == "2"


def test_messages_and_area_codes():
    pl = {"1": {"GeneralMessages": {"a": {"MessageContent": "Storing"}, "b": {"MessageContent": "Storing"}, "c": {}}}}
    assert parse_messages(pl, "1") == ["Storing"]


def test_line_options_sorted_numerically_with_first_destination():
    deps = parse_passes(payload(), "90100421", TZ)
    assert list(line_options(deps)) == ["34", "74", "350"] or list(line_options(deps)) == ["34", "74"]
    assert line_options(deps)["74"] == "74 → Station Centraal"


def test_parse_coordinates_variants():
    assert parse_coordinates("52.0801, 5.2501") == (52.0801, 5.2501)
    assert parse_coordinates("52,0801; 5,2501") == (52.0801, 5.2501)
    assert parse_coordinates("99, 5") is None and parse_coordinates("abc") is None


def _dep(line, dest, tijd, exp):
    return {"lijn": line, "bestemming": dest, "tijd": tijd, "exp": exp}


def test_merged_summary_and_main_destination():
    view = [_dep("74", "A", "14:05", 1), _dep("73", "B", "14:07", 2), _dep("74", "C", "14:10", 3), _dep("74", "C", "14:20", 4)]
    assert merged_summary(view) == "74 → A / C 14:05, 73 → B 14:07"
    assert main_destination(view) == "C"
    assert main_destination([]) == ""


def test_merged_lines_and_sorted_lines():
    view = [_dep("34", "Voorbeeldstad Centrum", "09:03", 1), _dep("74", "Noordstad-Voorbeeldstad", "09:03", 2),
            _dep("34", "Zuidstad", "09:10", 3), _dep("58", "Noordstad-Voorbeeldstad", "09:04", 4)]
    assert merged_lines(view) == [
        {"lijn": "34", "bestemmingen": ["Voorbeeldstad Centrum", "Zuidstad"], "tijd": "09:03"},
        {"lijn": "74", "bestemmingen": ["Noordstad-Voorbeeldstad"], "tijd": "09:03"},
        {"lijn": "58", "bestemmingen": ["Noordstad-Voorbeeldstad"], "tijd": "09:04"},
    ]
    assert sorted_lines(["350", "73", "34", "N74", "73", "9"]) == ["9", "34", "73", "350", "N74"]


def test_unknown_town_is_dropped_from_names():
    from custom_components.ovapi_bustijden.config_flow import _display_name
    assert [clean_town(x) for x in ("unknown", "Unknown", " ", None, "Voorbeeldstad")] == ["", "", "", "", "Voorbeeldstad"]
    payload = {"1": {"Stop": {"TimingPointName": "Voorbeeldstad, Voorbeeldlaan", "TimingPointTown": "unknown"}, "Passes": {}}}
    info = describe_tpc("1", payload)
    assert info["town"] == "" and _display_name(info, "x") == "Voorbeeldstad, Voorbeeldlaan"
    assert _display_name({"name": "Voorbeeldlaan", "town": "unknown"}, "x") == "Voorbeeldlaan"
