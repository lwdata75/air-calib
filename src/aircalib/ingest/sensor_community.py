"""Access to the Sensor.Community (ex-Luftdaten) archive.

Layout observed on the real archive (2026-10-13), see docs/JOURNAL.md:

* past years:   ``/<YYYY>/<YYYY-MM-DD>/<YYYY-MM-DD>_<type>_sensor_<id>[_indoor].csv.gz``
* current year: ``/<YYYY-MM-DD>/...`` (one folder per day at the root)
* one gzip-compressed, semicolon-separated CSV per sensor and per day;
* ``<type>`` may contain ``_`` or ``-`` (e.g. ``radiation_sbm-20``);
* a sensor id is unique across types;
* timestamps are naive ISO strings in UTC.
"""

from __future__ import annotations

import io
import re
import zlib
from datetime import date
from pathlib import Path

import polars as pl

from aircalib.ingest.http import PoliteClient

FILENAME_RE = re.compile(
    r"^(?P<date>\d{4}-\d{2}-\d{2})_(?P<sensor_type>[a-z0-9_-]+?)_sensor_"
    r"(?P<sensor_id>\d+)(?P<indoor>_indoor)?\.csv(?P<gz>\.gz)?$"
)
_HREF_RE = re.compile(r'href="([^"?/][^"]*)"')

# Columns read from the first data row to locate a sensor.
HEAD_COLUMNS = ("sensor_id", "sensor_type", "location", "lat", "lon", "timestamp")

LISTING_SCHEMA = {
    "date": pl.Date,
    "sensor_type": pl.String,
    "sensor_id": pl.Int64,
    "indoor": pl.Boolean,
    "filename": pl.String,
}


def day_url(archive_url: str, day: date, today: date) -> str:
    """URL of an archive day folder (past years are nested under a year folder)."""
    base = archive_url.rstrip("/")
    if day.year < today.year:
        return f"{base}/{day.year}/{day.isoformat()}/"
    return f"{base}/{day.isoformat()}/"


def file_url(
    archive_url: str, *, sensor_type: str, sensor_id: int, indoor: bool, day: date, today: date
) -> str:
    """URL of one sensor-day file (gzip-compressed for past years only)."""
    suffix = "_indoor" if indoor else ""
    gz = ".gz" if day.year < today.year else ""
    name = f"{day.isoformat()}_{sensor_type}_sensor_{sensor_id}{suffix}.csv{gz}"
    return day_url(archive_url, day, today) + name


def parse_listing(html: str) -> list[str]:
    """Extract the CSV file names from an Apache directory index page."""
    return [name for name in _HREF_RE.findall(html) if FILENAME_RE.match(name)]


def listing_frame(names: list[str]) -> pl.DataFrame:
    """Turn archive file names into a typed table (one row per sensor-day file)."""
    rows = []
    for name in names:
        match = FILENAME_RE.match(name)
        if match is None:
            continue
        rows.append(
            {
                "date": date.fromisoformat(match["date"]),
                "sensor_type": match["sensor_type"],
                "sensor_id": int(match["sensor_id"]),
                "indoor": match["indoor"] is not None,
                "filename": name,
            }
        )
    return pl.DataFrame(rows, schema=LISTING_SCHEMA)


def decompress_prefix(blob: bytes) -> bytes:
    """Decompress as much as possible of a (possibly truncated) gzip stream."""
    return zlib.decompressobj(wbits=31).decompress(blob)


def parse_head(text: str) -> dict[str, str] | None:
    """Return the header-mapped first *complete* data row of a CSV prefix.

    The prefix comes from an HTTP Range request, so the last line is usually
    cut; only lines terminated by a newline are trusted.
    """
    lines = text.split("\n")[:-1]  # drop the unterminated tail
    if len(lines) < 2:
        return None
    header = lines[0].strip().split(";")
    values = lines[1].strip().split(";")
    if len(values) != len(header):
        return None
    row = dict(zip(header, values, strict=True))
    return {col: row.get(col, "") for col in HEAD_COLUMNS}


def read_day_csv(content: bytes, gzipped: bool) -> pl.DataFrame:
    """Parse a full sensor-day CSV, keeping every column as raw text (bronze rule)."""
    raw = zlib.decompress(content, wbits=31) if gzipped else content
    if not raw.strip():
        return pl.DataFrame()
    return pl.read_csv(io.BytesIO(raw), separator=";", infer_schema=False)


async def fetch_listing(client: PoliteClient, url: str) -> list[str]:
    response = await client.get(url)
    if response.status_code == 404:
        return []
    response.raise_for_status()
    return parse_listing(response.text)


async def fetch_head(client: PoliteClient, url: str, n_bytes: int) -> dict[str, str] | None:
    """Read the first data row of a remote file using a single Range request."""
    response = await client.get(url, headers={"Range": f"bytes=0-{n_bytes - 1}"})
    if response.status_code == 404:
        return None
    response.raise_for_status()
    blob = response.content
    text = (decompress_prefix(blob) if url.endswith(".gz") else blob).decode("utf-8", "replace")
    return parse_head(text)


async def fetch_file(client: PoliteClient, url: str) -> bytes | None:
    response = await client.get(url)
    if response.status_code == 404:
        return None
    response.raise_for_status()
    return response.content


# ------------------------------------------------------------------------ bronze
# Identity columns present in every archive file, with their bronze type.
ID_COLUMNS = {
    "sensor_id": pl.Int64,
    "sensor_type": pl.String,
    "location": pl.Int64,
    "lat": pl.Float64,
    "lon": pl.Float64,
}


def bronze_frame(
    raw: pl.DataFrame, value_columns: list[str], timestamp_format: str
) -> tuple[pl.DataFrame, dict[str, int]]:
    """Type a raw (all-text) sensor-month table without changing its content.

    Returns the typed table and audit counters:

    * ``unparsed_<col>``: non-empty texts that could not be converted (set to null);
    * ``decimal_commas``: numbers written with a decimal comma (``0,8``), read as ``0.8``;
    * ``dropped_nonempty``: non-empty values in columns that bronze does not keep
      (expected 0: those columns are empty in the archive);
    * ``rows``.

    Nothing is filtered: duplicates, outliers and saturated values stay in bronze.
    """
    schema = {**ID_COLUMNS, "timestamp": pl.Datetime("us"), **dict.fromkeys(value_columns, pl.Float64)}
    present = raw.with_columns(
        [pl.lit(None, dtype=pl.String).alias(c) for c in schema if c not in raw.columns]
    )
    floats = [c for c, dtype in schema.items() if dtype == pl.Float64]

    def cast(c: str, dtype: pl.DataType) -> pl.Expr:
        if c == "timestamp":
            return pl.col(c).str.strptime(pl.Datetime("us"), timestamp_format, strict=False)
        text = pl.col(c).str.strip_chars()
        if c in floats:  # some firmwares write a decimal comma
            text = text.str.replace(",", ".", literal=True)
        return text.cast(dtype, strict=False)

    typed = present.select(**{c: cast(c, dtype) for c, dtype in schema.items()})
    stats = {"rows": raw.height}
    stats["decimal_commas"] = int(
        present.select(
            pl.sum_horizontal([pl.col(c).str.contains(",", literal=True).fill_null(False) for c in floats])
        )
        .to_series()
        .sum()
    )
    nonempty = present.select(
        [(pl.col(c).str.strip_chars().str.len_chars() > 0).fill_null(False).alias(c) for c in schema]
    )
    for c in schema:
        failed = nonempty[c] & typed[c].is_null()
        stats[f"unparsed_{c}"] = int(failed.sum())
    dropped = [c for c in raw.columns if c not in schema]
    stats["dropped_nonempty"] = (
        int(
            raw.select(
                pl.sum_horizontal(
                    [(pl.col(c).str.strip_chars().str.len_chars() > 0).fill_null(False) for c in dropped]
                )
            )
            .to_series()
            .sum()
        )
        if dropped
        else 0
    )
    return typed, stats


def raw_unit_dir(raw_root: Path, sensor_id: int, month: date) -> Path:
    """Folder holding the raw files of one sensor-month."""
    return raw_root / str(sensor_id) / month.strftime("%Y-%m")


def bronze_unit_path(bronze_root: Path, kind: str, sensor_id: int, month: date) -> Path:
    return bronze_root / kind / str(sensor_id) / f"{month.strftime('%Y-%m')}.parquet"
