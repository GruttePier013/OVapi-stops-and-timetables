"""NDOV-haltebestand (CHB): RD-conversie, bestandslijst, streaming-uitpakken, zoeken."""
import json

import pytest

from custom_components.ovapi_bustijden import chb

from .chb_data import A_RD, B_RD, FAR_RD, chb_xml, gz, listing_html, psa_xml


def test_rd_to_wgs84_matches_reference_points():
    # referentiepunten (gecontroleerd met een onafhankelijke RD-bibliotheek); tolerantie ±3 m
    for (x, y), (lat, lon) in {
        (155000, 463000): (52.15517440, 5.38720621),  # Zuidstad, het nulpunt van het stelsel
        (136784, 455860): (52.0907, 5.1214),
        (233770, 582065): (53.2194, 6.5665),
        (176395, 317996): (50.8514, 5.6910),
        A_RD: (52.0801, 5.2501),
        B_RD: (52.0793, 5.2488),
        FAR_RD: (52.5, 5.9),
    }.items():
        got_lat, got_lon = chb.rd_to_wgs84(x, y)
        assert got_lat == pytest.approx(lat, abs=3e-5) and got_lon == pytest.approx(lon, abs=5e-5)


def test_compass_label():
    assert chb.compass_label(0) == "N ↑" and chb.compass_label(225) == "ZW ↙" and chb.compass_label(359) == "N ↑"
    assert chb.compass_label(45) == "NO ↗" and chb.compass_label(-1) == "" and chb.compass_label(None) == ""


def test_parse_listing_takes_latest_pair_and_ignores_psa_for_chb():
    chb_url, psa_url, day = chb.parse_listing(listing_html(), "https://data.ndovloket.nl/haltes/")
    assert day == "2026-10-22"
    assert chb_url == "http://data.ndovloket.nl/haltes/ExportCHB_2026-10-22.xml.gz"
    assert psa_url == "http://data.ndovloket.nl/haltes/PassengerStopAssignmentExportCHB_2026-10-22.xml.gz"
    with pytest.raises(ValueError):
        chb.parse_listing("<html>leeg</html>", "http://x/")
    # relatieve links worden t.o.v. de lijst-URL opgelost; de verwijstabel is optioneel
    chb_url, psa_url, _ = chb.parse_listing('<a href="ExportCHB_2026-01-02.xml.gz">x</a>', "http://h/haltes/")
    assert chb_url == "http://h/haltes/ExportCHB_2026-01-02.xml.gz" and psa_url is None


@pytest.mark.parametrize("namespace", ["", "urn:bison:chb"])
def test_parse_chb_keeps_active_bus_quays(tmp_path, namespace):
    path = tmp_path / "chb.xml.gz"
    path.write_bytes(gz(chb_xml(namespace)))
    rows = {r[0]: r for r in chb.parse_chb(path)}
    assert sorted(rows) == ["NL:Q:82000101", "NL:Q:90100411", "NL:Q:90100412", "NL:Q:90100413", "NL:Q:90100777", "NL:Q:90100999"]
    quay, place, name, town, side, lat, lon, bearing, modes = rows["NL:Q:90100411"]
    assert modes == "bus" and rows["NL:Q:90100413"][8] == "tram"
    assert (place, name, town, bearing) == ("NL:S:90100400", "Voorbeeldlaan", "Voorbeeldstad", 225)
    assert lat == pytest.approx(52.0801, abs=3e-5) and lon == pytest.approx(5.2501, abs=5e-5)
    assert rows["NL:Q:82000101"][7] == -1  # onbekende rijrichting


def test_parse_psa_numeric_current_assignments_only(tmp_path):
    path = tmp_path / "psa.xml.gz"
    path.write_bytes(gz(psa_xml()))
    psa = chb.parse_psa(path, today="2026-10-22")
    assert psa["NL:Q:90100411"] == ["90100411", "90100451"]  # twee vervoerders op één paal
    assert psa["NL:Q:90100412"] == ["90100412"]               # verlopen koppeling genegeerd
    assert "NL:Q:82000101" not in psa                          # 'HA2614' is geen numerieke code
    assert psa["NL:Q:90100777"] == ["90100777"]                # koppeling in een kindelement


def test_build_index_reports_streaming_progress(tmp_path, monkeypatch):
    monkeypatch.setattr(chb, "CHUNK", 256)  # veel kleine brokken -> echte tussenstanden
    big = chb_xml().replace("</stopplaces>", "".join(
        f"<stopplace><stopplacecode>NL:S:{i}</stopplacecode><stopplacename><publicname>P{i}</publicname>"
        f"<town>T</town></stopplacename><quays></quays></stopplace>" for i in range(400)) + "</stopplaces>")
    (tmp_path / "chb.xml.gz").write_bytes(gz(big))
    (tmp_path / "psa.xml.gz").write_bytes(gz(psa_xml()))
    seen: list[float] = []
    n = chb.build_index(tmp_path / "chb.xml.gz", tmp_path / "psa.xml.gz", tmp_path / "index.json", seen.append, "2026-10-22")
    assert n == 6 and seen[-1] == 1.0
    assert seen == sorted(seen) and any(0.05 < x < 0.99 for x in seen)  # monotoon, met tussenstanden
    index = json.loads((tmp_path / "index.json").read_text())
    assert index["source_date"] == "2026-10-22" and len(index["quays"]) == 6
    assert "NL:Q:90100411" in index["psa"] and "NL:Q:90100999" not in index["psa"]
    assert not (tmp_path / "index.part").exists()


def test_build_index_without_bus_quays_fails(tmp_path):
    (tmp_path / "chb.xml.gz").write_bytes(gz("<export><stopplaces></stopplaces></export>"))
    with pytest.raises(ValueError):
        chb.build_index(tmp_path / "chb.xml.gz", None, tmp_path / "i.json")


@pytest.fixture
def index(tmp_path):
    (tmp_path / "chb.xml.gz").write_bytes(gz(chb_xml()))
    (tmp_path / "psa.xml.gz").write_bytes(gz(psa_xml()))
    chb.build_index(tmp_path / "chb.xml.gz", tmp_path / "psa.xml.gz", tmp_path / "i.json")
    return json.loads((tmp_path / "i.json").read_text())


def test_find_quays_radius_and_other_side_of_the_street(index):
    # kleine straal: 411 en 777 liggen erin; 412 (overkant, zelfde StopPlace als 411) gaat mee, 82000101 niet
    found = chb.find_quays(index, 52.08, 5.25, 60)
    assert [q["quay"] for q in found] == ["NL:Q:90100411", "NL:Q:90100777", "NL:Q:90100412"]
    assert found[0]["distance_m"] < 30 and found[-1]["distance_m"] > 60
    # grotere straal: ook de andere StopPlaces in de buurt, maar niet de verre
    wide = chb.find_quays(index, 52.08, 5.25, 250)
    assert {q["quay"] for q in wide} == {"NL:Q:90100411", "NL:Q:90100412", "NL:Q:82000101", "NL:Q:90100777"}
    assert chb.find_quays(index, 52.08, 5.25, 250, limit=2) == wide[:2]


def test_tpc_candidates_use_user_stop_codes_and_quay_number(index):
    psa = index["psa"]
    assert chb.tpc_candidates("NL:Q:90100411", psa) == ["90100411", "90100451"]
    assert chb.tpc_candidates("NL:Q:82000101", psa) == ["82000101"]   # alleen het quaynummer
    assert chb.tpc_candidates("NL:Q:90100412", psa) == ["90100412"]   # geen dubbele code


def test_find_quays_filters_on_transport_mode(tmp_path):
    import json  # noqa: PLC0415
    path = tmp_path / "chb.xml.gz"
    path.write_bytes(gz(chb_xml("")))
    chb.build_index(path, None, tmp_path / "i.json")
    index = json.loads((tmp_path / "i.json").read_text())
    bus = {q["quay"] for q in chb.find_quays(index, 52.0801, 5.2501, 100)}
    both = {q["quay"] for q in chb.find_quays(index, 52.0801, 5.2501, 100, modes=("bus", "tram"))}
    assert "NL:Q:90100413" not in bus and "NL:Q:90100413" in both and "NL:Q:90100411" in bus
    # oude index (8 kolommen) telt als bus
    index["quays"] = [r[:8] for r in index["quays"]]
    assert {q["quay"] for q in chb.find_quays(index, 52.0801, 5.2501, 100)} >= {"NL:Q:90100411"}
