"""NDOV Loket-haltebestand (CHB): bestandslijst, uitpakken met voortgang en zoeken.

Bron: https://data.ndovloket.nl/haltes/ (open data van het Centraal Halte Bestand).

* ``ExportCHB_<datum>.xml.gz`` (±13 MB, ±300 MB uitgepakt): StopPlaces met Quays. Een *quay* is één
  fysieke haltepaal en dus één rijrichting; hij heeft een naam, RD-coördinaten en een windrichting.
* ``PassengerStopAssignmentExportCHB_<datum>.xml.gz`` (±1 MB): koppelt de haltecode van de vervoerder
  (``UserStopCode``) aan de quay. Daaruit komen de OVapi TPC-codes.

Het grote bestand wordt gestreamd uitgepakt en verwerkt (nooit volledig op schijf of in het geheugen);
er blijft alleen een compacte index van bus-, tram-, metro- en veerbootquays over. Geen Home Assistant-imports.
"""
from __future__ import annotations

import json
import math
import re
import xml.etree.ElementTree as ET
import zlib
from collections.abc import Callable, Iterable, Iterator
from datetime import date
from pathlib import Path
from typing import Any
from urllib.parse import urljoin

from .parser import distance_m

LISTING_URLS = ("https://data.ndovloket.nl/haltes/", "http://data.ndovloket.nl/haltes/")
INDEX_VERSION = 2  # 2: bevat ook tram, metro en veerboot (kolom 9 = vervoerstypen)
MODES = ("bus", "tram", "metro", "boat")
CHUNK = 1 << 18

_CHB_LINK = re.compile(r'href="([^"]*?(?<![A-Za-z])ExportCHB_(\d{4}-\d{2}-\d{2})\.xml\.gz)"')
_PSA_LINK = re.compile(r'href="([^"]*?PassengerStopAssignmentExportCHB_(\d{4}-\d{2}-\d{2})\.xml\.gz)"')
# Quays die niet (meer) in gebruik zijn slaan we over
_INACTIVE = {"plan", "expired", "unavailable", "outofuse", "deleted"}

Progress = Callable[[float], None]

# rijrichting (graden, 0 = noord) -> (pijl, Nederlandse windrichting)
_COMPASS = (
    ("↑", "N"), ("↗", "NO"), ("→", "O"), ("↘", "ZO"),
    ("↓", "Z"), ("↙", "ZW"), ("←", "W"), ("↖", "NW"),
)


# ---------------------------------------------------------------------- hulpfuncties
def compass_label(bearing: int | float | None) -> str:
    """'ZW ↙' voor de rijrichting direct na de halte; leeg als die onbekend is."""
    if bearing is None or bearing < 0:
        return ""
    arrow, name = _COMPASS[int(((bearing % 360) + 22.5) // 45) % 8]
    return f"{name} {arrow}"


def compass_name(bearing: int | float | None) -> str:
    """'ZW' (zonder pijl), voor in een naam."""
    return compass_label(bearing).split(" ")[0]


def rd_to_wgs84(x: float, y: float) -> tuple[float, float]:
    """Rijksdriehoek (EPSG:28992) -> (lat, lon) in graden; benadering (Schreutelkamp/Strang van Hees), ~1 m."""
    dx, dy = (x - 155000) / 100000, (y - 463000) / 100000
    lat_terms = (
        (0, 1, 3235.65389), (2, 0, -32.58297), (0, 2, -0.24750), (2, 1, -0.84978), (0, 3, -0.06550),
        (2, 2, -0.01709), (1, 0, -0.00738), (4, 0, 0.00530), (2, 3, -0.00039), (4, 1, 0.00033), (1, 1, -0.00012),
    )
    lon_terms = (
        (1, 0, 5260.52916), (1, 1, 105.94684), (1, 2, 2.45656), (3, 0, -0.81885), (1, 3, 0.05594),
        (3, 1, -0.05607), (0, 1, 0.01199), (3, 2, -0.00256), (1, 4, 0.00128), (0, 2, 0.00022),
        (2, 0, -0.00022), (5, 0, 0.00026),
    )
    lat = 52.15517440 + sum(k * dx**p * dy**q for p, q, k in lat_terms) / 3600
    lon = 5.38720621 + sum(k * dx**p * dy**q for p, q, k in lon_terms) / 3600
    return lat, lon


def parse_listing(html: str, base: str) -> tuple[str, str | None, str]:
    """(url ExportCHB, url PassengerStopAssignment of None, datum) uit de mappenlijst; de nieuwste datum."""
    chb = max(_CHB_LINK.findall(html), key=lambda m: m[1], default=None)
    if chb is None:
        raise ValueError("Geen ExportCHB-bestand gevonden in de lijst van NDOV Loket")
    psa_all = _PSA_LINK.findall(html)
    psa = max([m for m in psa_all if m[1] == chb[1]] or psa_all, key=lambda m: m[1], default=None)
    return urljoin(base, chb[0]), (urljoin(base, psa[0]) if psa else None), chb[1]


def _ln(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _text(el: ET.Element, path: str) -> str:
    return (el.findtext(path) or "").strip()


def _iter_xml(path: Path, progress: Progress | None) -> Iterator[ET.Element]:
    """Pak een .xml.gz gestreamd uit en lever elk afgesloten element; voortgang = gelezen bytes / bestandsgrootte."""
    size = max(path.stat().st_size, 1)
    parser = ET.XMLPullParser(events=("end",))
    inflater = zlib.decompressobj(zlib.MAX_WBITS | 16)  # gzip
    done, last = 0, -1.0
    with path.open("rb") as fh:
        while chunk := fh.read(CHUNK):
            done += len(chunk)
            parser.feed(inflater.decompress(chunk))
            for _event, el in parser.read_events():
                yield el
            fraction = done / size
            if progress and fraction - last >= 0.005:
                last = fraction
                progress(min(fraction, 0.999))
    parser.feed(inflater.flush())
    parser.close()
    for _event, el in parser.read_events():
        yield el
    if progress:
        progress(1.0)


# ---------------------------------------------------------------------- ExportCHB
def _stopplace_rows(sp: ET.Element) -> list[list[Any]]:
    code = _text(sp, "{*}stopplacecode")
    place_name = _text(sp, "{*}stopplacename/{*}publicname")
    place_town = _text(sp, "{*}stopplacename/{*}town")
    rows: list[list[Any]] = []
    for quay in sp.iterfind("{*}quays/{*}quay"):
        modes = {
            (m.text or "").strip().lower()
            for m in quay.iterfind("{*}quaytransportmodes/{*}transportmodedata/{*}transportmode")
        }
        wanted = sorted(modes & set(MODES))
        if not wanted or _text(quay, "{*}quaystatusdata/{*}quaystatus").lower() in _INACTIVE:
            continue
        loc = quay.find("{*}quaylocationdata")
        if loc is None:
            continue
        try:
            lat, lon = rd_to_wgs84(float(_text(loc, "{*}rd-x")), float(_text(loc, "{*}rd-y")))
        except ValueError:
            continue
        quay_name = _text(quay, "{*}quaynamedata/{*}quayname")
        try:
            bearing = int(_text(quay, "{*}quaybearing/{*}compassdirection"))
        except ValueError:
            bearing = -1
        rows.append(
            [
                _text(quay, "{*}quaycode"),
                code,
                place_name or quay_name,
                _text(loc, "{*}town") or place_town,
                _text(quay, "{*}quaynamedata/{*}stopsidecode"),
                round(lat, 6),
                round(lon, 6),
                bearing,
                ",".join(wanted),
            ]
        )
    return [r for r in rows if r[0]]


def parse_chb(path: Path, progress: Progress | None = None) -> list[list[Any]]:
    """Busquays uit ExportCHB: ``[quaycode, stopplacecode, naam, plaats, perron, lat, lon, rijrichting]``."""
    rows: list[list[Any]] = []
    for el in _iter_xml(path, progress):
        tag = el.tag
        if tag == "stopplace" or tag.endswith("}stopplace"):
            rows.extend(_stopplace_rows(el))
            el.clear()  # het grote bestand nooit als geheel in het geheugen
        elif tag in ("place", "dataowner") or tag.endswith(("}place", "}dataowner")):
            el.clear()
    return rows


# ---------------------------------------------------------------------- PassengerStopAssignment
def _flat(el: ET.Element) -> dict[str, str]:
    out = {k.lower(): (v or "").strip() for k, v in el.attrib.items()}
    for child in el:
        out[_ln(child.tag).lower()] = (child.text or "").strip()
    return out


def parse_psa(path: Path, progress: Progress | None = None, today: str | None = None) -> dict[str, list[str]]:
    """``{quaycode: [numerieke UserStopCodes]}`` van de halteverwijstabel (alleen nu geldige koppelingen)."""
    today = today or date.today().isoformat()
    out: dict[str, set[str]] = {}
    for el in _iter_xml(path, progress):
        if not 2 <= len(el) <= 30:
            continue
        base = _flat(el)
        user_stop = base.get("userstopcode", "")
        if not user_stop:
            continue
        # De koppeling (quaycode, geldigheid) kan direct in het record staan of in een kindelement.
        groups = [_flat(c) for c in el if len(c) and "quaycode" in _flat(c)] or [base]
        for g in groups:
            quay, thru = g.get("quaycode", ""), g.get("validthru", "")
            if quay and user_stop.isdigit() and not (thru and thru < today):
                out.setdefault(quay, set()).add(user_stop)
        el.clear()
    return {quay: sorted(codes) for quay, codes in out.items()}


# ---------------------------------------------------------------------- index
def build_index(
    chb_path: Path,
    psa_path: Path | None,
    out_path: Path,
    progress: Progress | None = None,
    source_date: str = "",
) -> int:
    """Pak beide bestanden uit en schrijf de compacte index; geeft het aantal busquays terug.

    Voortgang: eerst de kleine verwijstabel (0–5 %), dan het grote bestand (5–100 %).
    """
    def scaled(lo: float, hi: float) -> Progress | None:
        return (lambda f: progress(lo + (hi - lo) * f)) if progress else None

    psa = parse_psa(psa_path, scaled(0.0, 0.05)) if psa_path and psa_path.exists() else {}
    rows = parse_chb(chb_path, scaled(0.05, 1.0))
    if not rows:
        raise ValueError("Geen busquays gevonden in het CHB-bestand")
    known = {r[0] for r in rows}
    index = {
        "version": INDEX_VERSION,
        "source_date": source_date,
        "built": date.today().isoformat(),
        "quays": rows,
        "psa": {q: c for q, c in psa.items() if q in known},
    }
    out_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = out_path.with_suffix(".part")
    tmp.write_text(json.dumps(index, separators=(",", ":")), encoding="utf-8")
    tmp.replace(out_path)
    # Klein metadata-bestand: zo is de ouderdom van het haltebestand te tonen zonder de hele index te laden
    meta = {"source_date": source_date, "built": index["built"], "quays": len(rows), "version": INDEX_VERSION}
    out_path.with_name("chb_meta.json").write_text(json.dumps(meta), encoding="utf-8")
    return len(rows)


def tpc_candidates(quay_code: str, psa: dict[str, list[str]]) -> list[str]:
    """Mogelijke OVapi TPC-codes van een quay: de numerieke UserStopCodes en het quaynummer zelf.

    Veel vervoerders gebruiken hun UserStopCode als TPC, andere (bijv. RET/GVB) het landelijke
    quaynummer; daarom worden beide live bij OVapi gecontroleerd.
    """
    codes = list(psa.get(quay_code, []))
    number = quay_code.rsplit(":", 1)[-1]
    if number.isdigit() and number not in codes:
        codes.append(number)
    return codes


def find_quays(
    index: dict[str, Any], lat: float, lon: float, radius_m: float, limit: int = 60, modes: Iterable[str] = ("bus",)
) -> list[dict[str, Any]]:
    """Busquays binnen ``radius_m`` plus de andere quays (de overkant) van dezelfde StopPlace, dichtstbij eerst."""
    dlat = radius_m / 111_320
    dlon = radius_m / (111_320 * max(math.cos(math.radians(lat)), 0.01))
    psa: dict[str, list[str]] = index.get("psa", {})
    hits: dict[str, float] = {}
    places: set[str] = set()
    wanted = set(modes) or {"bus"}
    rows = [r for r in index["quays"] if wanted & set((r[8] if len(r) > 8 else "bus").split(","))]
    for quay, place, _name, _town, _side, qlat, qlon, _bearing, *_rest in rows:
        if abs(qlat - lat) <= dlat and abs(qlon - lon) <= dlon:
            d = distance_m(lat, lon, qlat, qlon)
            if d <= radius_m:
                hits[quay] = d
                if place:
                    places.add(place)
    found: list[dict[str, Any]] = []
    for quay, place, name, town, side, qlat, qlon, bearing, *_rest in rows:
        if quay not in hits and not (place and place in places):
            continue
        d = hits.get(quay)
        if d is None:
            d = distance_m(lat, lon, qlat, qlon)
        found.append(
            {
                "quay": quay,
                "place": place,
                "name": name,
                "town": town,
                "side": side,
                "lat": qlat,
                "lon": qlon,
                "bearing": bearing,
                "distance_m": round(d),
                "tpc_candidates": tpc_candidates(quay, psa),
            }
        )
    found.sort(key=lambda q: (q["distance_m"], q["quay"]))
    return found[:limit]
