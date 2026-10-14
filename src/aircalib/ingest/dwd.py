"""Official hourly temperature and humidity from the German weather service (DWD).

Facts verified on the real open-data server (2026-10-14), see docs/JOURNAL.md:

* ``historical/`` holds one zip per station and period, e.g.
  ``stundenwerte_TU_00044_20070401_20251231_hist.zip``: quality-controlled data
  up to the end of the previous year, so 2024-2025 are complete;
* the station list ``TU_Stundenwerte_Beschreibung_Stationen.txt`` is a
  fixed-width, Latin-1 text file;
* inside each zip, ``produkt_tu_stunde_*.txt`` is a ``;`` separated table
  ``STATIONS_ID;MESS_DATUM;QN_9;TT_TU;RF_TU;eor`` with padded values,
  ``MESS_DATUM`` = ``YYYYMMDDHH`` **in UTC** ("Stundenwerte in UTC" in the
  parameter metadata) and missing values coded ``-999``.
"""

from __future__ import annotations

import io
import re
import zipfile
from datetime import date
from pathlib import Path

import polars as pl

from aircalib.ingest.http import PoliteClient

FILE_RE = re.compile(r"stundenwerte_TU_(?P<station_id>\d{5})_(?P<begin>\d{8})_(?P<end>\d{8})_hist\.zip")
# Fixed-width station list: id, from, to, altitude, lat, lon, name, state, licence
# (the licence column is blank for some closed stations).
STATION_RE = re.compile(
    r"^\s*(?P<station_id>\d+)\s+(?P<begin>\d{8})\s+(?P<end>\d{8})\s+(?P<altitude_m>-?\d+)\s+"
    r"(?P<lat>-?[\d.]+)\s+(?P<lon>-?[\d.]+)\s+(?P<name>.+?)\s{2,}(?P<state>\S.*?)(?:\s{2,}\S+)?\s*$"
)


def parse_station_list(text: str) -> pl.DataFrame:
    """Typed station list (lines that do not match the fixed-width layout are skipped)."""
    rows = [m.groupdict() for line in text.splitlines() if (m := STATION_RE.match(line))]
    return pl.DataFrame(rows, schema=dict.fromkeys(STATION_RE.groupindex, pl.String)).select(
        station_id=pl.col("station_id").cast(pl.Int64),
        name=pl.col("name"),
        state=pl.col("state"),
        lat=pl.col("lat").cast(pl.Float64),
        lon=pl.col("lon").cast(pl.Float64),
        altitude_m=pl.col("altitude_m").cast(pl.Float64),
        begin=pl.col("begin").str.to_date("%Y%m%d"),
        end=pl.col("end").str.to_date("%Y%m%d"),
    )


def parse_file_listing(html: str) -> pl.DataFrame:
    """Data files of the ``historical/`` directory index, one row per zip."""
    rows = [
        {"filename": m.group(0), "station_id": int(m["station_id"]), "begin": m["begin"], "end": m["end"]}
        for m in FILE_RE.finditer(html)
    ]
    schema = {"filename": pl.String, "station_id": pl.Int64, "begin": pl.String, "end": pl.String}
    return (
        pl.DataFrame(rows, schema=schema)
        .unique("filename")
        .with_columns(pl.col("begin").str.to_date("%Y%m%d"), pl.col("end").str.to_date("%Y%m%d"))
        .sort("station_id", "begin")
    )


def files_covering(files: pl.DataFrame, start: date, end: date) -> pl.DataFrame:
    """Zips whose time span intersects ``[start, end]``."""
    return files.filter(pl.col("begin") <= end, pl.col("end") >= start)


def read_product(zip_bytes: bytes) -> pl.DataFrame:
    """Read the ``produkt_*`` table of a station zip as text (bronze types it)."""
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as archive:
        (name,) = [n for n in archive.namelist() if n.startswith("produkt_")]
        content = archive.read(name)
    return pl.read_csv(io.BytesIO(content), separator=";", infer_schema=False).select(
        pl.all().str.strip_chars()
    )


def bronze_frame(product: pl.DataFrame, start: date, end: date) -> pl.DataFrame:
    """Typed rows of the study period; ``-999`` sentinels are kept as published."""
    return (
        product.select(
            station_id=pl.col("STATIONS_ID").cast(pl.Int64),
            time_utc=(pl.col("MESS_DATUM") + "00").str.to_datetime("%Y%m%d%H%M", time_unit="us"),
            quality_level=pl.col("QN_9").cast(pl.Int64),
            temperature=pl.col("TT_TU").cast(pl.Float64),
            humidity=pl.col("RF_TU").cast(pl.Float64),
        )
        .filter(pl.col("time_utc").dt.date().is_between(start, end))
        .sort("time_utc")
    )


def raw_path(raw_root: Path, filename: str) -> Path:
    return raw_root / "dwd" / filename


def bronze_path(bronze_root: Path, station_id: int) -> Path:
    return bronze_root / "dwd" / f"{station_id:05d}.parquet"


async def fetch_text(client: PoliteClient, url: str, encoding: str = "utf-8") -> str:
    response = await client.get(url)
    response.raise_for_status()
    return response.content.decode(encoding)
