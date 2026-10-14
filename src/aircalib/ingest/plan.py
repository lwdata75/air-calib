"""What to download: the final pairs, their humidity probes and the sensor-months.

Inputs are the step-1 reference tables in ``data/meta`` (inventory, stations,
monthly availability). Outputs are small reference tables written to
``<layers>/bronze/reference`` so that every later layer knows exactly which
sensors, stations and months the bronze layer was built from.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

import polars as pl

from aircalib.config import Settings
from aircalib.ingest.discovery import pair_table
from aircalib.logging import get_logger

log = get_logger(__name__)

NEAR, FAR = "near", "far"


def reference_dir(settings: Settings) -> Path:
    path = settings.paths.bronze / "reference"
    path.mkdir(parents=True, exist_ok=True)
    return path


# ------------------------------------------------------------------- pure helpers
def select_pairs(pairs: pl.DataFrame, settings: Settings) -> pl.DataFrame:
    """Pairs kept for the study, tagged ``near`` (training zone) or ``far`` (distance test).

    ``pairs`` already holds a single nearest station per sensor (see ``pair_table``).
    """
    cfg = settings.pairing
    return pairs.filter(
        pl.col("sensor_type").is_in(cfg.sensor_types),
        pl.col("distance_km") <= cfg.radius_download_km,
    ).with_columns(
        distance_band=pl.when(pl.col("distance_km") <= cfg.radius_train_km)
        .then(pl.lit(NEAR))
        .otherwise(pl.lit(FAR))
    )


def humidity_companions(inventory: pl.DataFrame, pairs: pl.DataFrame) -> pl.DataFrame:
    """Outdoor humidity probes installed on the same site (``location_id``) as a paired sensor."""
    sites = pairs.select("location_id").unique()
    return (
        inventory.filter(pl.col("kind") == "humidity", ~pl.col("indoor"))
        .join(sites, on="location_id", how="semi")
        .sort("sensor_id")
    )


def _add_months(day: date, months: int) -> date:
    index = day.year * 12 + day.month - 1 + months
    return date(index // 12, index % 12 + 1, 1)


def activity_window(months: list[date], start: date, end: date, sample_day: int) -> tuple[date, date]:
    """Days worth requesting for a sensor seen on ``sample_day`` of each month in ``months``.

    The sensor may have started the day after the previous sampled day and may
    have stopped the day before the next one, so the window runs from
    ``sample_day + 1`` of the month before the first sighting to ``sample_day - 1``
    of the month after the last one, clipped to the study period.
    """
    first, last = min(months), max(months)
    lo = _add_months(first, -1).replace(day=sample_day) + timedelta(days=1)
    hi = _add_months(last, 1).replace(day=sample_day) - timedelta(days=1)
    return max(lo, start), min(hi, end)


def month_units(sensors: pl.DataFrame, start: date, end: date, sample_day: int) -> pl.DataFrame:
    """One row per (sensor, calendar month) to download, with the day range inside the month."""
    rows = []
    for rec in sensors.select("sensor_id", "sensor_type", "kind", "indoor", "months").iter_rows(named=True):
        lo, hi = activity_window(rec["months"], start, end, sample_day)
        month = lo.replace(day=1)
        while month <= hi:
            following = _add_months(month, 1)
            rows.append(
                {
                    "sensor_id": rec["sensor_id"],
                    "sensor_type": rec["sensor_type"],
                    "kind": rec["kind"],
                    "indoor": rec["indoor"],
                    "month": month,
                    "first_day": max(lo, month),
                    "last_day": min(hi, following - timedelta(days=1)),
                }
            )
            month = following
    schema = {
        "sensor_id": pl.Int64,
        "sensor_type": pl.String,
        "kind": pl.String,
        "indoor": pl.Boolean,
        "month": pl.Date,
        "first_day": pl.Date,
        "last_day": pl.Date,
    }
    return pl.DataFrame(rows, schema=schema).with_columns(
        n_days=(pl.col("last_day") - pl.col("first_day")).dt.total_days() + 1
    )


def sample_pairs(pairs: pl.DataFrame, n_per_country: int, month: date) -> pl.DataFrame:
    """A small subset spread over the distance range, active during ``month``.

    Per country, sensors active that month are sorted by distance and picked at
    evenly spaced ranks, so the sample covers both the near and far bands.
    """
    active = pairs.filter(pl.col("months").list.contains(month)).sort("country", "distance_km", "sensor_id")
    picked = []
    for _, group in active.group_by("country", maintain_order=True):
        n = min(n_per_country, group.height)
        ranks = sorted({round(i * (group.height - 1) / max(n - 1, 1)) for i in range(n)})
        picked.append(group[ranks])
    return pl.concat(picked) if picked else active.clear()


# ---------------------------------------------------------------------- the plan
@dataclass(frozen=True)
class Plan:
    pairs: pl.DataFrame  # one row per paired PM sensor
    humidity: pl.DataFrame  # humidity probes on the same sites
    units: pl.DataFrame  # sensor-months to download (PM + humidity)


def build_plan(settings: Settings) -> Plan:
    """Select pairs, companions and sensor-months; write them as reference tables."""
    meta = settings.paths.meta
    inventory = pl.read_parquet(meta / "sc_inventory.parquet")
    stations = pl.read_parquet(meta / "eea_stations_pm25.parquet")
    availability = pl.read_parquet(meta / "eea_pm25_monthly_valid_hours.parquet")

    nearest = pair_table(inventory, stations, availability, settings.pairing.radius_download_km)
    pairs = select_pairs(nearest, settings).join(
        inventory.select("sensor_id", "indoor", "months"), on="sensor_id", how="left"
    )
    start, end = settings.period.start, settings.period.end
    if settings.paths.sample:
        # ``for_sample`` already narrowed the period to the sample month.
        pairs = sample_pairs(pairs, settings.sample.n_sensors_per_country, start)

    humidity = humidity_companions(inventory, pairs)
    if settings.paths.sample:
        humidity = humidity.filter(pl.col("months").list.contains(start))
    sensors = pl.concat(
        [
            pairs.select("sensor_id", "sensor_type", "indoor", "months").with_columns(kind=pl.lit("pm")),
            humidity.select("sensor_id", "sensor_type", "indoor", "months", "kind"),
        ],
        how="diagonal",
    )
    units = month_units(sensors, start, end, settings.discovery.sample_day_of_month)

    out = reference_dir(settings)
    pairs.write_parquet(out / "pairs.parquet")
    humidity.write_parquet(out / "humidity_sensors.parquet")
    units.write_parquet(out / "sensor_months.parquet")
    log.info(
        "plan: %d pairs (%d near, %d far), %d humidity probes, %d sensor-months, %d sensor-days",
        pairs.height,
        pairs.filter(pl.col("distance_band") == NEAR).height,
        pairs.filter(pl.col("distance_band") == FAR).height,
        humidity.height,
        units.height,
        units["n_days"].sum(),
    )
    return Plan(pairs=pairs, humidity=humidity, units=units)


def load_plan(settings: Settings) -> Plan:
    out = reference_dir(settings)
    return Plan(
        pairs=pl.read_parquet(out / "pairs.parquet"),
        humidity=pl.read_parquet(out / "humidity_sensors.parquet"),
        units=pl.read_parquet(out / "sensor_months.parquet"),
    )
