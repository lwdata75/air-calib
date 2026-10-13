"""Hourly aggregation and clock alignment between sources.

Convention used everywhere in the project: an hour is labelled by its
**start** in **UTC** (``2024-01-01 13:00`` covers ``[13:00, 14:00[`` UTC).
"""

from __future__ import annotations

import polars as pl


def hourly_mean(
    lf: pl.LazyFrame | pl.DataFrame, time_col: str, value_col: str, by: list[str]
) -> pl.LazyFrame:
    """Mean and count of ``value_col`` per hour (labelled by its start) and group."""
    return (
        lf.lazy()
        .group_by(*by, hour=pl.col(time_col).dt.truncate("1h"))
        .agg(mean=pl.col(value_col).mean(), n=pl.col(value_col).count())
        .sort(*by, "hour")
    )


def lagged_correlation(
    sensor: pl.DataFrame,
    reference: pl.DataFrame,
    max_lag_h: int,
) -> pl.DataFrame:
    """Pearson correlation between two hourly series for integer lags.

    Both frames have columns ``hour`` and ``value``. For a lag ``k`` the sensor
    hour ``h`` is compared with the reference row labelled ``h + k``. If the
    reference clock is UTC+1, the best lag is ``+1``.
    """
    rows = []
    for lag in range(-max_lag_h, max_lag_h + 1):
        shifted = reference.with_columns(hour=pl.col("hour") - pl.duration(hours=lag))
        joined = sensor.join(shifted, on="hour", suffix="_ref").drop_nulls()
        r = joined.select(pl.corr("value", "value_ref")).item() if joined.height > 2 else None
        rows.append({"lag_h": lag, "pearson_r": r, "n_hours": joined.height})
    return pl.DataFrame(rows, schema={"lag_h": pl.Int64, "pearson_r": pl.Float64, "n_hours": pl.Int64})
