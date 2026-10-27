"""Pure helpers: parse OVapi payloads and build the departure view.

This module deliberately imports nothing from Home Assistant so the logic
can be tested in isolation. The rules come from the proven template sensors
(ovapi_bushalte_sensoren.yaml):

* OVapi delivers local times without a timezone ("2026-10-21T14:05:00").
* Only departure times count (``ExpectedDepartureTime``); passes without a
  departure time (terminus, arrival only) are ignored.
* ``TripStopStatus == CANCEL`` is not shown.
* A bus stays visible for 30 seconds after departure.
* The line filter compares line numbers as text.
"""
from __future__ import annotations

import math
import re
from collections.abc import Iterable
from datetime import datetime, tzinfo
from typing import Any

from .texts import Texts

STATUS_CANCEL = "CANCEL"


def distance_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Distance in metres between two points (haversine)."""
    r = 6371000
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def parse_time(value: Any, tz: tzinfo) -> datetime | None:
    """Parse an OVapi timestamp; naive values are local time in ``tz``."""
    if not value or not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=tz)
    return parsed


def round_half_up(value: float) -> int:
    """Round like Jinja's ``round(0)`` (0.5 -> 1), not banker's rounding."""
    return int(math.floor(value + 0.5))


def parse_codes(text: str | Iterable[str] | None) -> list[str]:
    """'12345678, 12345679' -> ['12345678', '12345679'] (unique, in order)."""
    if not text:
        return []
    if not isinstance(text, str):
        text = " ".join(str(t) for t in text)
    seen: list[str] = []
    for code in re.findall(r"\d+", text):
        if code not in seen:
            seen.append(code)
    return seen


def parse_lines(text: str | Iterable[str] | None) -> list[str]:
    """'73, 74 350' -> ['73', '74', '350'] (unique, in order)."""
    if not text:
        return []
    if not isinstance(text, str):
        text = " ".join(str(t) for t in text)
    seen: list[str] = []
    for token in re.split(r"[,;\s]+", text):
        if token and token.casefold() not in (s.casefold() for s in seen):
            seen.append(token)
    return seen


_NO_TOWN = {"", "unknown", "onbekend", "null", "none", "n/a", "-"}


def clean_town(value: Any) -> str:
    """Place name, or '' if OVapi/NDOV gives a placeholder (e.g. 'unknown')."""
    text = str(value).strip() if value is not None else ""
    return "" if text.casefold() in _NO_TOWN else text


def describe_tpc(code: str, payload: Any) -> dict[str, Any] | None:
    """Return basic info for a timing point, or None if OVapi doesn't know it."""
    obj = payload.get(code) if isinstance(payload, dict) else None
    if not isinstance(obj, dict):
        return None
    raw_stop = obj.get("Stop")
    stop: dict[str, Any] = raw_stop if isinstance(raw_stop, dict) else {}
    lat: float | None
    lon: float | None
    try:
        lat = float(stop["Latitude"])
        lon = float(stop["Longitude"])
    except (KeyError, TypeError, ValueError):
        lat = lon = None
    return {
        "code": code,
        "area": stop.get("StopAreaCode"),
        "name": stop.get("TimingPointName"),
        "town": clean_town(stop.get("TimingPointTown")),
        "lat": lat,
        "lon": lon,
    }


def parse_passes(payload: Any, tpc: str, tz: tzinfo) -> list[dict[str, Any]]:
    """Flatten ``payload[tpc]['Passes']`` into departure dicts."""
    obj = payload.get(tpc) if isinstance(payload, dict) else None
    passes = obj.get("Passes") if isinstance(obj, dict) else None
    if not isinstance(passes, dict):
        return []
    out: list[dict[str, Any]] = []
    for journey_id, p in passes.items():
        if not isinstance(p, dict):
            continue
        if p.get("JourneyStopType") == "LAST":  # terminus: boarding is not possible
            continue
        planned = parse_time(p.get("TargetDepartureTime"), tz)
        expected = parse_time(p.get("ExpectedDepartureTime"), tz) or planned
        if expected is None:  # terminus / arrival only
            continue
        out.append(
            {
                "journey_id": str(journey_id),
                "tpc": tpc,
                "expected": expected,
                "planned": planned or expected,
                "line": str(p.get("LinePublicNumber") or ""),
                "destination": p.get("DestinationName50") or p.get("DestinationName") or "",
                "status": p.get("TripStopStatus") or "",
                "linename": p.get("LineName") or "",
                "direction": str(p.get("LineDirection") or ""),
            }
        )
    return out


def build_view(
    departures: Iterable[dict[str, Any]],
    now: datetime,
    lines: Iterable[str] = (),
    limit: int | None = None,
    grace: int = 30,
    destinations: Iterable[str] = (),
) -> list[dict[str, Any]]:
    """Upcoming departures, same shape as the ``vertrekken`` attribute of the yaml sensors."""
    now_ts = now.timestamp()
    wanted = {str(line).casefold() for line in lines}
    dests = [str(x).casefold() for x in destinations if str(x).strip()]
    items: list[dict[str, Any]] = []
    for d in departures:
        if d["status"] == STATUS_CANCEL:
            continue
        exp = d["expected"].timestamp()
        if exp <= now_ts - grace:
            continue
        if wanted and d["line"].casefold() not in wanted:
            continue
        if dests and not any(x in d["destination"].casefold() for x in dests):
            continue
        planned = d["planned"].timestamp()
        items.append(
            {
                "exp": exp,
                "time": d["expected"].astimezone(now.tzinfo).strftime("%H:%M"),
                "planned": d["planned"].astimezone(now.tzinfo).strftime("%H:%M"),
                "minutes_until": round_half_up((exp - now_ts) / 60),
                "delay_min": round_half_up((exp - planned) / 60),
                "line": d["line"],
                "destination": d["destination"],
                "status": d["status"],
                "tpc": d["tpc"],
                "line_name": d.get("linename", ""),
                "direction": d.get("direction", ""),
            }
        )
    items.sort(key=lambda x: x["exp"])
    return items[:limit] if limit else items


def summarize(view: list[dict[str, Any]], t: Texts | None = None) -> str:
    """'14:05 (Line 74)' or 'No departures' (texts are translated through ``t``)."""
    t = t or Texts()
    if not view:
        return t("no_buses")
    return f"{view[0]['time']} ({t('line_short', line=view[0]['line'])})"


def next_departure_text(view: list[dict[str, Any]], t: Texts | None = None) -> str:
    """'Line 74 at 16:01 (+2 min)' or 'No departures'; the delay is only shown when there is one."""
    t = t or Texts()
    if not view:
        return t("no_buses")
    d = view[0]
    delay = d["delay_min"]
    suffix = f" ({delay:+d} min)" if delay else ""
    return f"{t('line_at', line=d['line'], time=d['time'])}{suffix}"


def direction_summary(view: list[dict[str, Any]], max_items: int = 3) -> str:
    """Next bus per line+destination: '74 → Station 14:07, 34 → Voorbeeldstad 14:20'.

    This shows in the config flow which direction a stop (TPC code) has.
    """
    seen: set[tuple[str, str]] = set()
    parts: list[str] = []
    for d in view:
        ident = (d["line"], d["destination"])
        if ident in seen:
            continue
        seen.add(ident)
        parts.append(f"{d['line']} → {d['destination']} {d['time']}")
        if len(parts) >= max_items:
            break
    return ", ".join(parts)


def count_passes(payload: Any, tpc: str) -> int:
    """Number of passes in the OVapi response (for filtering)."""
    obj = payload.get(tpc) if isinstance(payload, dict) else None
    passes = obj.get("Passes") if isinstance(obj, dict) else None
    return len(passes) if isinstance(passes, dict) else 0


def parse_messages(payload: Any, tpc: str, limit: int = 5) -> list[str]:
    """Passenger messages (``GeneralMessages``) of a timing point, de-duplicated."""
    obj = payload.get(tpc) if isinstance(payload, dict) else None
    msgs = obj.get("GeneralMessages") if isinstance(obj, dict) else None
    out: list[str] = []
    if isinstance(msgs, dict):
        for m in msgs.values():
            text = (m or {}).get("MessageContent") if isinstance(m, dict) else None
            if text and text not in out:
                out.append(str(text))
    return out[:limit]


def line_options(departures: Iterable[dict[str, Any]]) -> dict[str, str]:
    """Lines currently at the stop: {'74': '74 → Noord'} (first destination)."""
    out: dict[str, str] = {}
    for d in sorted(departures, key=lambda x: x["expected"]):
        if d["line"] and d["line"] not in out:
            out[d["line"]] = f"{d['line']} → {d['destination']}" if d["destination"] else d["line"]

    return sort_lines(out)


def sort_lines(options: dict[str, str]) -> dict[str, str]:
    """Lijnnummers numeriek sorteren (34, 73, 74, 350), daarna letters (N74)."""
    def key(item: str) -> tuple[int, Any]:
        return (0, int(item)) if item.isdigit() else (1, item)

    return {k: options[k] for k in sorted(options, key=key)}


def parse_coordinates(text: str | None) -> tuple[float, float] | None:
    """'52.1000, 5.1000' / '52.1000 5.1000' / '52,1000; 5,1000' -> (lat, lon), of None."""
    if not text:
        return None
    nums = re.findall(r"-?\d+(?:[.,]\d+)?", text.replace(";", " "))
    if len(nums) != 2:
        return None
    lat, lon = (float(n.replace(",", ".")) for n in nums)
    if -90 <= lat <= 90 and -180 <= lon <= 180:
        return lat, lon
    return None


def merged_lines(view: list[dict[str, Any]], max_lines: int = 6, max_destinations: int = 3) -> list[dict[str, Any]]:
    """Next bus per line (in departure order), with all destinations of that line merged.

    ``[{"line": "34", "destinations": ["Voorbeeldstad Centrum", "Station Zuid"], "time": "09:03"}, ...]``:
    a direction can have several final destinations (shorter routes); they belong to one device.
    """
    per_line: dict[str, dict[str, Any]] = {}
    for d in view:
        entry = per_line.setdefault(d["line"], {"line": d["line"], "destinations": [], "time": d["time"]})
        if d["destination"] and d["destination"] not in entry["destinations"]:
            entry["destinations"].append(d["destination"])
    lines = list(per_line.values())[:max_lines]
    for entry in lines:
        entry["destinations"] = entry["destinations"][:max_destinations]
    return lines


def merged_summary(view: list[dict[str, Any]], max_lines: int = 3, max_destinations: int = 3) -> str:
    """'74 → Noord / Voorbeeldstad Centrum 14:07, 73 → Voorbeeldstad 14:20' (kort, voor één regel)."""
    parts = []
    for entry in merged_lines(view, max_lines, max_destinations):
        dest = " / ".join(entry["destinations"])
        parts.append(f"{entry['line']} → {dest} {entry['time']}" if dest else f"{entry['line']} {entry['time']}")
    return ", ".join(parts)


def sorted_lines(lines: Iterable[str]) -> list[str]:
    """Unieke lijnnummers, numeriek gesorteerd (34, 73, 350), daarna letters (N74)."""
    return sorted(set(lines), key=lambda x: (0, int(x), x) if x.isdigit() else (1, 0, x))


def main_destination(view: list[dict[str, Any]]) -> str:
    """Most common destination in the upcoming departures (the first one on a tie)."""
    counts: dict[str, int] = {}
    for d in view:
        if d["destination"]:
            counts[d["destination"]] = counts.get(d["destination"], 0) + 1
    return max(counts, key=lambda k: counts[k]) if counts else ""
