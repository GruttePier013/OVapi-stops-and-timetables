"""Synthetische NDOV-haltebestanden (zelfde structuur als ExportCHB / PassengerStopAssignment) voor de tests."""
from __future__ import annotations

import gzip

# RD-coördinaten (EPSG:28992) van vaste testpunten, berekend met een onafhankelijke bibliotheek
A_RD = (145602, 454656)   # 52.0801, 5.2501  (±12 m van de zoekpin 52.08, 5.25)
B_RD = (145512, 454567)   # 52.0793, 5.2488  (overkant van de straat, ±110 m)
NEAR_RD = (145650, 454700)  # ±65 m van A, andere StopPlace
NODATA_RD = (145620, 454640)  # quay zonder live gegevens bij OVapi
FAR_RD = (189821, 501490)  # 52.5, 5.9: ver weg


def _quay(code, rd, modes=("bus",), status="available", bearing=None, town="Voorbeeldstad", name="Voorbeeldlaan"):
    modes_xml = "".join(
        f"<transportmodedata><validfrom>2020-01-01</validfrom><transportmode>{m}</transportmode></transportmodedata>"
        for m in modes
    )
    bearing_xml = (
        f"<quaybearing><validfrom>2020-01-01</validfrom><compassdirection>{bearing}</compassdirection></quaybearing>"
        if bearing is not None
        else ""
    )
    return (
        f"<quay><ID>1</ID><quaycode>{code}</quaycode><quaytransportmodes>{modes_xml}</quaytransportmodes>"
        f"<quaystatusdata><validfrom>2020-01-01</validfrom><quaystatus>{status}</quaystatus></quaystatusdata>"
        f"<quaylocationdata><rd-x>{rd[0]}</rd-x><rd-y>{rd[1]}</rd-y><town>{town}</town><level>0</level></quaylocationdata>"
        f"{bearing_xml}<quaynamedata><quayname>{name}</quayname></quaynamedata></quay>"
    )


def _place(code, name, town, quays, ptype="onstreetBus"):
    return (
        f"<stopplace><ID>1</ID><stopplacecode>{code}</stopplacecode><stopplacetype>{ptype}</stopplacetype>"
        f"<stopplacename><publicname>{name}</publicname><town>{town}</town></stopplacename>"
        f"<quays>{''.join(quays)}</quays></stopplace>"
    )


def chb_xml(namespace: str = "") -> str:
    places = [
        _place(
            "NL:S:90100400", "Voorbeeldlaan", "Voorbeeldstad",
            [
                _quay("NL:Q:90100411", A_RD, bearing=225),
                _quay("NL:Q:90100412", B_RD, bearing=45),
                _quay("NL:Q:90100413", A_RD, modes=("tram",)),          # geen bus -> overslaan
                _quay("NL:Q:90100414", A_RD, status="expired"),          # niet meer in gebruik -> overslaan
            ],
        ),
        _place("NL:S:82000100", "Voorbeeldweg", "Voorbeeldstad", [_quay("NL:Q:82000101", NEAR_RD, name="Voorbeeldweg")]),
        _place("NL:S:90100777", "Geen Data", "Voorbeeldstad", [_quay("NL:Q:90100777", NODATA_RD, name="Geen Data")]),
        _place("NL:S:90100999", "Verweg", "Elders", [_quay("NL:Q:90100999", FAR_RD, town="Elders", name="Verweg")]),
    ]
    ns = f' xmlns="{namespace}"' if namespace else ""
    return (
        f'<?xml version="1.0" encoding="UTF-8"?><export{ns}><stopplaces>{"".join(places)}</stopplaces>'
        "<places><place><placecode>x</placecode></place></places></export>"
    )


def psa_xml() -> str:
    def rec(owner, user, quay, thru=""):
        thru_xml = f"<Validthru>{thru}</Validthru>" if thru else ""
        return (
            f"<PassengerStopAssignment><DataOwnerCode>{owner}</DataOwnerCode><UserStopCode>{user}</UserStopCode>"
            f"<Validfrom>2014-01-01</Validfrom>{thru_xml}<Quaycode>{quay}</Quaycode></PassengerStopAssignment>"
        )

    nested = (
        "<PassengerStopAssignment><DataOwnerCode>ARR</DataOwnerCode><UserStopCode>90100777</UserStopCode>"
        "<Quaykoppeling><Validfrom>2014-01-01</Validfrom><Quaycode>NL:Q:90100777</Quaycode></Quaykoppeling>"
        "</PassengerStopAssignment>"
    )
    records = [
        rec("ARR", "90100411", "NL:Q:90100411"),
        rec("QBZ", "90100451", "NL:Q:90100411"),                 # tweede vervoerder op dezelfde paal
        rec("ARR", "90100412", "NL:Q:90100412"),
        rec("ARR", "90109999", "NL:Q:90100412", thru="2020-01-01"),  # verlopen koppeling -> negeren
        rec("RET", "HA2614", "NL:Q:82000101"),                   # niet-numeriek -> geen TPC
        nested,
    ]
    return f'<?xml version="1.0" encoding="UTF-8"?><export>{"".join(records)}</export>'


def gz(text: str) -> bytes:
    return gzip.compress(text.encode("utf-8"))


def listing_html(date: str = "2026-10-22", older: str = "2026-10-14") -> str:
    base = "http://data.ndovloket.nl/haltes"
    return (
        "<html><body><table>"
        f'<tr><td><a href="{base}/ExportCHB_{older}.xml.gz">ExportCHB_{older}.xml.gz</a></td></tr>'
        f'<tr><td><a href="{base}/PassengerStopAssignmentExportCHB_{older}.xml.gz">psa</a></td></tr>'
        f'<tr><td><a href="{base}/ExportCHB_{date}.xml.gz">ExportCHB_{date}.xml.gz</a></td><td>13M</td></tr>'
        f'<tr><td><a href="{base}/PassengerStopAssignmentExportCHB_{date}.xml.gz">psa</a></td><td>1.0M</td></tr>'
        "</table></body></html>"
    )
