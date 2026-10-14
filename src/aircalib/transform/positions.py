"""Where was a sensor? Detect relocations and cut the measurements into position segments."""

from __future__ import annotations

import polars as pl

POSITION = ["location", "lat", "lon"]


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


def assign_segments(readings: pl.DataFrame) -> pl.DataFrame:
    """Add ``segment`` (1, 2, ...): consecutive readings of a sensor at the same site and coordinates.

    The frame must be sorted by sensor and timestamp. Going back to an earlier
    position opens a new segment (each segment is one continuous stay).
    """
    # The first reading of a sensor compares with a missing previous one: it opens segment 1.
    moved = pl.any_horizontal([pl.col(c).ne_missing(pl.col(c).shift().over("sensor_id")) for c in POSITION])
    return readings.with_columns(segment=moved.cast(pl.UInt32).cum_sum().over("sensor_id"))


def segment_table(readings: pl.DataFrame | pl.LazyFrame) -> pl.LazyFrame:
    """One row per (sensor, segment): position, first and last reading, number of readings."""
    return (
        readings.lazy()
        .group_by("sensor_id", "segment")
        .agg(
            *[pl.col(c).first() for c in POSITION],
            start=pl.col("timestamp").min(),
            end=pl.col("timestamp").max(),
            n_readings=pl.len(),
        )
        .sort("sensor_id", "segment")
    )
