"""Variants of the gold inputs for the sensitivity studies (step 6)."""

from __future__ import annotations

import polars as pl

from aircalib.config import Settings
from aircalib.silver.pairs import local_humidity_by_site
from aircalib.transform import humidity as hum


def local_humidity_for(settings: Settings, dead_month_share: float) -> pl.DataFrame:
    """``pair_id, hour, local_humidity, local_temperature, local_reliable`` with another threshold X.

    The probe hours of silver are re-classified (a month is "dead" above X faulty
    hours), then the site's reliable probe is picked as in the ``pair`` stage.
    """
    silver = settings.paths.silver
    probes = pl.read_parquet(silver / "sensor_community" / "humidity_hourly.parquet").drop(
        "month_status", "reliable"
    )
    status = (
        hum.classify_months(hum.monthly_quality(probes), dead_month_share)
        .select("sensor_id", "month", month_status="status")
        .collect()
    )
    probes = (
        probes.with_columns(month=pl.col("hour").dt.truncate("1mo").dt.date())
        .join(status, on=["sensor_id", "month"], how="left")
        .with_columns(
            reliable=(pl.col("month_status") == hum.HEALTHY)
            & ~pl.col("faulty")
            & pl.col("humidity").is_not_null()
        )
    )
    sites = local_humidity_by_site(settings, probes)
    pairs = pl.read_parquet(silver / "pairs" / "segment_pairs.parquet").select("pair_id", "location")
    return pairs.join(sites, on="location").select(
        "pair_id",
        "hour",
        pl.col("local_humidity").cast(pl.Float32),
        pl.col("local_temperature").cast(pl.Float32),
        local_reliable=pl.lit(True),
    )
