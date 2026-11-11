"""Contract test: the parser must cope with the real shape of an OVapi answer.

``tests/fixtures/ovapi_*.json`` are OVapi answers. ``ovapi_sample.json`` is hand-written from the documented format;
real captures (``scripts/capture_ovapi_fixture.py``) are picked up automatically.
"""
import json
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from custom_components.ovapi_bus_departures.parser import (
    build_view,
    describe_tpc,
    journey_states,
    parse_messages,
    parse_passes,
)

TZ = ZoneInfo("Europe/Amsterdam")
FIXTURES = sorted((Path(__file__).parent / "fixtures").glob("ovapi_*.json"))


def _load(path: Path) -> dict:
    return {k: v for k, v in json.loads(path.read_text(encoding="utf-8")).items() if not k.startswith("_")}


def test_fixtures_exist():
    assert FIXTURES


@pytest.mark.parametrize("path", FIXTURES, ids=lambda p: p.name)
def test_parser_handles_every_timing_point(path):
    payload = _load(path)
    now = datetime(2026, 10, 21, 14, 0, tzinfo=TZ)
    for code in payload:
        info = describe_tpc(code, payload)
        assert info and info["code"] == code
        assert (info["lat"] is None) == (info["lon"] is None)
        departures = parse_passes(payload, code, TZ)
        for d in departures:
            assert d["tpc"] == code and d["journey_id"]
            assert isinstance(d["line"], str) and isinstance(d["destination"], str)
            assert d["expected"].tzinfo is not None and d["planned"].tzinfo is not None
        view = build_view(departures, now)
        assert [d["exp"] for d in view] == sorted(d["exp"] for d in view)
        assert all(d["status"] != "CANCEL" and isinstance(d["live"], bool) for d in view)
        assert all(d["minutes_until"] >= 0 or d["exp"] > now.timestamp() - 30 for d in view)
        states = journey_states(departures, now)
        assert all(key[0] == code for key in states)
        assert len(states) >= len(view)  # the states also contain cancelled journeys
        parse_messages(payload, code)


def test_sample_content():
    payload = _load(Path(__file__).parent / "fixtures" / "ovapi_sample.json")
    now = datetime(2026, 10, 21, 14, 0, tzinfo=TZ)
    departures = parse_passes(payload, "30000001", TZ)
    view = build_view(departures, now)
    # terminus (LAST), a string instead of an object and a pass with only a planned time
    assert {d["line"] for d in departures} == {"74", "34", "N74", "350"} | {"74"}
    assert [d["line"] for d in view] == ["350", "74", "34", "74"]
    assert [d["live"] for d in view] == [True, True, False, False]
    assert view[1]["delay_min"] == 2 and view[1]["time"] == "14:07" and view[1]["planned"] == "14:05"
    # the cancelled journey is not in the view but is reported by journey_states
    states = journey_states(departures, now)
    assert states[("30000001", "ARR_10003_1")]["cancelled"] is True
    # an hour later everything has left
    assert build_view(departures, now + timedelta(hours=2)) == []
    assert describe_tpc("30000002", payload)["lat"] is None
    assert describe_tpc("30000002", payload)["town"] == ""  # placeholder town


def test_blueprint_is_valid_yaml_with_the_documented_inputs():
    import yaml  # noqa: PLC0415

    class Loader(yaml.SafeLoader):
        pass

    Loader.add_constructor("!input", lambda loader, node: {"__input__": loader.construct_scalar(node)})
    path = Path(__file__).parent.parent / "blueprints/automation/ovapi_bus_departures/bus_leaves_soon.yaml"
    bp = yaml.load(path.read_text(encoding="utf-8"), Loader=Loader)  # noqa: S506 - SafeLoader subclass
    assert bp["blueprint"]["domain"] == "automation"
    inputs = set(bp["blueprint"]["input"])
    assert inputs == {"next_departure", "minutes", "notify_action", "extra_conditions"}
    used = {v["__input__"] for v in _walk(bp) if isinstance(v, dict) and "__input__" in v}
    assert used == inputs


def _walk(node):
    yield node
    if isinstance(node, dict):
        for v in node.values():
            yield from _walk(v)
    elif isinstance(node, list):
        for v in node:
            yield from _walk(v)
