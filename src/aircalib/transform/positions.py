"""Where was a sensor? Detect relocations inside the raw measurements."""

from __future__ import annotations

import polars as pl


def position_history(lf: pl.LazyFrame | pl.DataFrame) -> pl.LazyFrame:
    """One row per (sensor, site, coordinates) with the time span and number of readings.

    A sensor with several rows was moved (or its owner edited the coordinates)
    during the period: its distance to the station is then not constant.
    """
    return (
        lf.lazy()
        .group_by("sensor_id", "location", "lat", "lon")
        .agg(first=pl.col("timestamp").min(), last=pl.col("timestamp").max(), n=pl.len())
        .sort("sensor_id", "first")
    )
