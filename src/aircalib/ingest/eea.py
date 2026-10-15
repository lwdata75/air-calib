"""Reference measurements from the European Environment Agency (EEA).

Facts verified on real downloads (2026-10-13), see docs/JOURNAL.md:

* the download API returns one Parquet file per *sampling point* holding its
  full history (2013 → last verified year), whatever date filter is sent;
* E1a (``dataset=2``) already contains verified 2024 and 2025 data;
* ``Value`` is a ``Decimal(38, 18)``; missing hours are ``-999`` with ``Validity = -1``;
* ``Start``/``End`` are naive timestamps on a fixed-offset clock (no DST gap):
  the offset is checked empirically in notebook 01 (lag cross-correlation);
* the parquet ``Samplingpoint`` is ``"<CC>/<Sampling Point Id>"`` where the id
  matches the metadata column ``Sampling Point Id``.
"""

from __future__ import annotations

import io
import zipfile
from datetime import date, timedelta
from pathlib import Path

import polars as pl

from aircalib.config import Eea
from aircalib.ingest.http import PoliteClient

METADATA_COLUMNS = {
    "Sampling Point Id": "sampling_point",
    "Air Quality Station EoI Code": "station_code",
    "Air Quality Station Name": "station_name",
    "Air Pollutant": "pollutant",
    "Latitude": "lat",
    "Longitude": "lon",
    "Altitude": "altitude_m",
    "Air Quality Station Type": "station_type",
    "Air Quality Station Area": "station_area",
    "Measurement Type": "measurement_type",
    "Measurement Method": "measurement_method",
    "Measurement Equipment": "measurement_equipment",
    "Timezone": "timezone",
    "Year": "metadata_year",
    "Operational Activity Begin": "activity_begin",
    "Operational Activity End": "activity_end",
}


async def fetch_metadata_csv(client: PoliteClient, cfg: Eea) -> bytes:
    """Download the zipped metadata extract and return the raw CSV bytes."""
    response = await client.get(cfg.metadata_url)
    response.raise_for_status()
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        (name,) = archive.namelist()
        return archive.read(name)


def parse_metadata(raw_csv: bytes) -> pl.DataFrame:
    """Read the metadata extract (all columns kept as text, as published)."""
    return pl.read_csv(io.BytesIO(raw_csv), infer_schema=False)


def sampling_points(
    meta: pl.DataFrame, pollutant: str, measurement_types: list[str], timezones: list[str]
) -> pl.DataFrame:
    """One row per sampling point measuring ``pollutant`` with an hourly-capable method.

    Only stations whose declared clock is in ``timezones`` are kept (this drops
    overseas territories, whose timestamps follow another offset).

    The extract repeats a sampling point once per reporting year and per process;
    we keep the most recent declaration. A few declarations have no coordinates
    (19 automatic PM2.5 points on 2026-10-13, one in France): they cannot be paired.
    """
    return (
        meta.select(list(METADATA_COLUMNS))
        .rename(METADATA_COLUMNS)
        .filter(
            pl.col("pollutant") == pollutant,
            pl.col("measurement_type").is_in(measurement_types),
            pl.col("timezone").is_in(timezones),
        )
        .with_columns(
            country=pl.col("station_code").str.slice(0, 2),
            lat=pl.col("lat").cast(pl.Float64, strict=False),
            lon=pl.col("lon").cast(pl.Float64, strict=False),
            altitude_m=pl.col("altitude_m").cast(pl.Float64, strict=False),
            activity_begin=pl.col("activity_begin").str.to_datetime("%d/%m/%Y %H:%M:%S", strict=False),
            activity_end=pl.col("activity_end").str.to_datetime("%d/%m/%Y %H:%M:%S", strict=False),
        )
        .drop_nulls(["lat", "lon"])
        .sort("metadata_year", descending=True)
        .unique(subset="sampling_point", keep="first", maintain_order=True)
        .sort("sampling_point")
    )


def pollutant_uri(cfg: Eea, code: int) -> str:
    """Vocabulary URI of a pollutant code (the API filters on it)."""
    return f"{cfg.pollutant_vocabulary}/{code}"


async def parquet_urls(client: PoliteClient, cfg: Eea, country: str, pollutant_code: int) -> list[str]:
    """List the Parquet URLs (one per sampling point) for a country and pollutant."""
    payload = {
        "countries": [country],
        "cities": [],
        "pollutants": [pollutant_uri(cfg, pollutant_code)],
        "dataset": cfg.dataset,
        "source": "API",
        "aggregationType": cfg.aggregation,
    }
    response = await client.post_json(f"{cfg.api_url}/ParquetFile/urls", payload)
    # The body is a one-column CSV whose header carries a UTF-8 BOM.
    lines = response.text.lstrip("﻿").splitlines()
    return [line.strip() for line in lines[1:] if line.strip().startswith("http")]


async def download_parquet(client: PoliteClient, url: str, dest: Path) -> Path:
    """Download one Parquet file atomically (skip if already present)."""
    if dest.exists() and dest.stat().st_size > 0:
        return dest
    response = await client.get(url)
    response.raise_for_status()
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(".part")
    tmp.write_bytes(response.content)
    tmp.replace(dest)
    return dest


def raw_path(raw_root: Path, pollutant: str, country: str, sampling_point: str) -> Path:
    """Where the raw file of a sampling point is stored."""
    return raw_root / "eea" / pollutant / country / f"{sampling_point}.parquet"


def bronze_path(bronze_root: Path, pollutant: str, country: str, sampling_point: str) -> Path:
    """Where the bronze file of a sampling point is stored."""
    return bronze_root / "eea" / pollutant / country / f"{sampling_point}.parquet"


def scan_observations(paths: list[Path] | Path) -> pl.LazyFrame:
    """Lazily read EEA observation files with analysis-friendly names and types.

    Timestamps go from ns to µs (the project-wide unit, lossless for hours) and
    the ``Decimal(38, 18)`` value becomes a float. ``DataCapture`` (always empty),
    ``ResultTime`` and ``FkObservationLog`` (bookkeeping) are not carried over.
    """
    return pl.scan_parquet(paths).select(
        sampling_point=pl.col("Samplingpoint").str.split("/").list.last(),
        pollutant=pl.col("Pollutant"),
        start=pl.col("Start").cast(pl.Datetime("us")),
        end=pl.col("End").cast(pl.Datetime("us")),
        value=pl.col("Value").cast(pl.Float64),
        unit=pl.col("Unit"),
        agg_type=pl.col("AggType"),
        validity=pl.col("Validity"),
        verification=pl.col("Verification"),
    )


def monthly_valid_hours(lf: pl.LazyFrame, cfg: Eea, start: date, end: date) -> pl.LazyFrame:
    """Valid *and* verified hours per sampling point and month over ``[start, end]``."""
    return (
        lf.filter(
            pl.col("start").dt.date().is_between(start, end),
            pl.col("validity").is_in(cfg.valid_codes),
            pl.col("verification").is_in(cfg.verified_codes),
            pl.col("value") != cfg.missing_sentinel,
        )
        .group_by("sampling_point", month=pl.col("start").dt.truncate("1mo").dt.date())
        .agg(valid_hours=pl.len())
    )


def bronze_frame(lf: pl.LazyFrame, start: date, end: date, margin_days: int) -> pl.LazyFrame:
    """Rows of the study period (plus a margin for the clock shift), values unchanged.

    ``Start`` is still on the station clock here (UTC+1 for Germany, see the
    journal): the margin keeps the hours that move across the period bounds
    once converted to UTC in silver. Sentinels (-999) and flags are kept.
    """
    lo = start - timedelta(days=margin_days)
    hi = end + timedelta(days=margin_days)
    return lf.filter(pl.col("start").dt.date().is_between(lo, hi)).sort("start")
