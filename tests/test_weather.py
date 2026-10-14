"""DWD and Meteo-France parsers, on lines copied from the real files."""

import io
import zipfile
from datetime import date, datetime

import polars as pl

from aircalib.config import get_settings
from aircalib.ingest import dwd, meteo_france
from aircalib.transform.pairing import nearest_k

# Fixed-width lines copied verbatim (closed stations have a blank licence column).
STATION_LIST = "\n".join(
    [
        "Stations_id von_datum bis_datum Stationshoehe geoBreite geoLaenge Stationsname Bundesland Abgabe",
        "----------- --------- --------- ------------- --------- --------- "
        + "-" * 41
        + " ---------- ------",
        "00044 20070401 20261012             44     52.9336    8.2370 Großenkneten"
        + " " * 29
        + "Niedersachsen"
        + " " * 28
        + "Frei",
        "00106 19990101 20061201            500     51.7976   10.4429 Altenau"
        + " " * 34
        + "Niedersachsen"
        + " " * 25,
        "04350 19710101 20081229            339     47.5619    7.9399 Säckingen, Bad (Bergseestr.)"
        + " " * 13
        + "Baden-Württemberg"
        + " " * 24
        + "Frei",
    ]
)


def test_parse_station_list_handles_spaces_umlauts_and_missing_licence():
    st = dwd.parse_station_list(STATION_LIST)
    assert st["station_id"].to_list() == [44, 106, 4350]
    assert st["name"].to_list() == ["Großenkneten", "Altenau", "Säckingen, Bad (Bergseestr.)"]
    assert st["state"][2] == "Baden-Württemberg"
    assert st["end"][0] == date(2026, 10, 12)


def test_parse_file_listing_and_period_cover():
    html = (
        '<a href="stundenwerte_TU_00003_19500401_20110331_hist.zip">x</a>'
        '<a href="stundenwerte_TU_00044_20070401_20251231_hist.zip">x</a>'
        '<a href="TU_Stundenwerte_Beschreibung_Stationen.txt">x</a>'
    )
    files = dwd.parse_file_listing(html)
    assert files["station_id"].to_list() == [3, 44]
    covering = dwd.files_covering(files, date(2024, 1, 1), date(2025, 12, 31))
    assert covering["station_id"].to_list() == [44]


def test_dwd_product_is_read_from_zip_and_typed_in_utc():
    product = (
        "STATIONS_ID;MESS_DATUM;QN_9;TT_TU;RF_TU;eor\n"
        "         44;2023123123;    3;   8.1;  80.0;eor\n"
        "         44;2024010100;    3;   8.0;  79.0;eor\n"
        "         44;2024010101;    3;-999.0;-999.0;eor\n"
    )
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("Metadaten_Geographie_00044.txt", "ignored")
        archive.writestr("produkt_tu_stunde_20070401_20251231_00044.txt", product)
    frame = dwd.bronze_frame(dwd.read_product(buffer.getvalue()), date(2024, 1, 1), date(2025, 12, 31))
    assert frame["time_utc"].to_list() == [datetime(2024, 1, 1, 0), datetime(2024, 1, 1, 1)]
    assert frame["humidity"].to_list() == [79.0, -999.0]  # sentinel kept in bronze


def test_meteo_france_resources_are_matched_by_title_and_period():
    cfg = get_settings().meteo_france
    dataset = {
        "resources": [
            {"title": "HOR_departement_67_periode_2020-2024", "url": "u1", "filesize": 10},
            {"title": "HOR_departement_67_periode_2025-2026", "url": "u2", "filesize": 5},
            {"title": "HOR_departement_67_periode_2010-2019", "url": "u3", "filesize": 9},
            {"title": "H_descriptif_champs.csv", "url": "u4", "filesize": 1},
        ]
    }
    resources = meteo_france.parse_resources(dataset, cfg.resource_title_regex)
    assert resources.height == 3
    files = meteo_france.files_for(resources, ["67"], date(2024, 1, 1), date(2025, 12, 31))
    assert files["url"].to_list() == ["u1", "u2"]


def test_departments_near_crosses_borders_and_maps_corsica():
    cfg = get_settings().meteo_france
    communes = meteo_france.parse_communes(
        [
            {
                "code": "67482",
                "codeDepartement": "67",
                "centre": {"coordinates": [7.75, 48.58]},
            },  # Strasbourg
            {
                "code": "68224",
                "codeDepartement": "68",
                "centre": {"coordinates": [7.34, 48.08]},
            },  # Colmar, 60 km
            {"code": "2A004", "codeDepartement": "2A", "centre": {"coordinates": [8.74, 41.92]}},  # Ajaccio
            {"code": "x", "codeDepartement": "99"},  # no centre
        ],
        cfg.department_aliases,
    )
    assert communes["department"].to_list() == ["67", "68", "20"]
    station = pl.DataFrame({"lat": [48.58], "lon": [7.75]})
    assert meteo_france.departments_near(station, communes, 30.0) == ["67"]
    assert meteo_france.departments_near(station, communes, 70.0) == ["67", "68"]


def test_nearest_k_ranks_candidates_and_respects_max_distance():
    points = pl.DataFrame({"sampling_point": ["A", "B"], "lat": [50.0, 51.0], "lon": [8.0, 8.0]})
    candidates = pl.DataFrame(
        {"station_id": [1, 2, 3, 4], "lat": [50.01, 50.1, 50.0, 53.0], "lon": [8.0, 8.0, 8.01, 8.0]}
    )
    out = nearest_k(
        points, candidates, point_id="sampling_point", candidate_id="station_id", k=2, max_km=30.0
    )
    assert out.filter(pl.col("sampling_point") == "A")["station_id"].to_list() == [3, 1]
    assert out.filter(pl.col("sampling_point") == "B").is_empty()  # nothing within 30 km
