"""Official hourly temperature and humidity from Meteo-France (open data).

Facts verified on the real files (2026-10-14), see docs/JOURNAL.md:

* one gzip CSV per *department* and period (``H_67_previous-2020-2024.csv.gz``,
  ``H_67_latest-2025-2026.csv.gz``); file names change as years roll over, so
  URLs are read from the data.gouv.fr dataset API (resource titles
  ``HOR_departement_67_periode_2020-2024``);
* ``;`` separated, 200+ columns; we keep the station identity, ``AAAAMMJJHH``,
  ``T`` (instantaneous temperature, °C), ``U`` (relative humidity, %) and their
  quality codes ``QT``/``QU`` (9 filtered, 0 protected, 1 validated, 2 doubtful);
* many stations measure temperature but not humidity (in Paris, 2 of 6);
* Corsica is department ``20`` in the files (``2A``/``2B`` elsewhere).
"""

from __future__ import annotations

import io
import json
import re
from datetime import date
from pathlib import Path

import polars as pl

from aircalib.config import MeteoFrance
from aircalib.ingest.http import PoliteClient
from aircalib.transform.pairing import pairs_within


def parse_resources(dataset: dict, title_regex: str) -> pl.DataFrame:
    """Department files of the dataset, with the years they cover."""
    pattern = re.compile(title_regex)
    rows = []
    for resource in dataset["resources"]:
        match = pattern.match(resource["title"])
        if match:
            rows.append(
                {
                    "department": match["department"],
                    "first_year": int(match["first_year"]),
                    "last_year": int(match["last_year"]),
                    "url": resource["url"],
                    "bytes": resource.get("filesize"),
                }
            )
    schema = {
        "department": pl.String,
        "first_year": pl.Int64,
        "last_year": pl.Int64,
        "url": pl.String,
        "bytes": pl.Int64,
    }
    return pl.DataFrame(rows, schema=schema)


def files_for(resources: pl.DataFrame, departments: list[str], start: date, end: date) -> pl.DataFrame:
    """Files of ``departments`` whose year range intersects the study period."""
    return resources.filter(
        pl.col("department").is_in(departments),
        pl.col("first_year") <= end.year,
        pl.col("last_year") >= start.year,
    ).sort("department", "first_year")


def parse_communes(records: list[dict], aliases: dict[str, str]) -> pl.DataFrame:
    """Commune centres with their department (as numbered in Meteo-France files)."""
    rows = [
        {
            "commune": r["code"],
            "department": aliases.get(r["codeDepartement"], r["codeDepartement"]),
            "lon": r["centre"]["coordinates"][0],
            "lat": r["centre"]["coordinates"][1],
        }
        for r in records
        if r.get("centre")
    ]
    return pl.DataFrame(rows)


def departments_near(points: pl.DataFrame, communes: pl.DataFrame, max_km: float) -> list[str]:
    """Departments having at least one commune centre within ``max_km`` of a point.

    France has ~35,000 communes (~16 km² on average), so their centres sample
    each department finely: a department reaching into the search circle is
    found, including across a border when the nearest weather station lies there.
    """
    near = pairs_within(
        points.select("lat", "lon"), communes.select("department", "lat", "lon"), max_km
    ).collect()
    return sorted(near["department"].unique().to_list())


def read_raw(path: Path, columns: list[str]) -> pl.DataFrame:
    """Read the kept columns of a department file as text."""
    return pl.read_csv(path, separator=";", columns=columns, infer_schema=False)


def bronze_frame(raw: pl.DataFrame, start: date, end: date) -> pl.DataFrame:
    """Typed rows of the study period; values and quality codes unchanged."""
    return (
        raw.select(
            station_id=pl.col("NUM_POSTE").cast(pl.Int64),
            name=pl.col("NOM_USUEL"),
            lat=pl.col("LAT").cast(pl.Float64),
            lon=pl.col("LON").cast(pl.Float64),
            altitude_m=pl.col("ALTI").cast(pl.Float64, strict=False),
            time_utc=(pl.col("AAAAMMJJHH") + "00").str.to_datetime("%Y%m%d%H%M", time_unit="us"),
            temperature=pl.col("T").cast(pl.Float64, strict=False),
            temperature_quality=pl.col("QT").cast(pl.Int64, strict=False),
            humidity=pl.col("U").cast(pl.Float64, strict=False),
            humidity_quality=pl.col("QU").cast(pl.Int64, strict=False),
        )
        .filter(pl.col("time_utc").dt.date().is_between(start, end))
        .sort("station_id", "time_utc")
    )


def station_table(bronze: pl.DataFrame) -> pl.DataFrame:
    """One row per station: position and number of hours with a humidity value."""
    return (
        bronze.group_by("station_id")
        .agg(
            name=pl.col("name").last(),
            lat=pl.col("lat").last(),
            lon=pl.col("lon").last(),
            altitude_m=pl.col("altitude_m").last(),
            humidity_hours=pl.col("humidity").is_not_null().sum(),
        )
        .sort("station_id")
    )


def raw_path(raw_root: Path, url: str) -> Path:
    """Where a raw Météo-France file is stored (its name in the URL)."""
    return raw_root / "meteo_france" / url.rsplit("/", 1)[-1]


def bronze_path(bronze_root: Path, station_id: int) -> Path:
    """Where the bronze table of one Météo-France station is stored."""
    return bronze_root / "meteo_france" / f"{station_id}.parquet"


async def fetch_json(client: PoliteClient, url: str) -> dict | list:
    """GET and parse a JSON document."""
    response = await client.get(url)
    response.raise_for_status()
    return json.load(io.BytesIO(response.content))


async def resources(client: PoliteClient, cfg: MeteoFrance) -> pl.DataFrame:
    """The hourly resources of the dataset (one per department and period)."""
    dataset = await fetch_json(client, cfg.dataset_api_url)
    assert isinstance(dataset, dict)
    return parse_resources(dataset, cfg.resource_title_regex)


async def communes(client: PoliteClient, cfg: MeteoFrance) -> pl.DataFrame:
    """Commune centres with their department (to find the departments near a station)."""
    records = await fetch_json(client, cfg.communes_url)
    assert isinstance(records, list)
    return parse_communes(records, cfg.department_aliases)
