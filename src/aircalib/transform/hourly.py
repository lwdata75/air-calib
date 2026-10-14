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


def ten_minute_means(
    lf: pl.LazyFrame | pl.DataFrame, time_col: str, value_col: str, step_min: int
) -> pl.LazyFrame:
    """Means over windows of ``step_min`` minutes *centred* on multiples of ``step_min``."""
    half = pl.duration(minutes=step_min / 2)
    return (
        lf.lazy()
        .drop_nulls([time_col, value_col])
        .group_by(time=(pl.col(time_col) + half).dt.truncate(f"{step_min}m"))
        .agg(value=pl.col(value_col).mean())
        .sort("time")
    )


def offset_correlation(series: pl.DataFrame, reference: pl.DataFrame, offsets_min: list[int]) -> pl.DataFrame:
    """Correlation between an instantaneous reference and a finer series, for clock offsets in minutes.

    ``reference`` (columns ``time``, ``value``) is sampled at instants; ``series``
    (same columns) is a fine series (e.g. 10-minute means). For an offset ``d``,
    the reference at ``t`` is compared with the series at ``t + d``. If both
    clocks agree, the best offset is close to 0 (plus the probe's thermal lag);
    a reference labelled in UTC+1 would peak near -60 min.
    """
    rows = []
    for offset in offsets_min:
        shifted = series.with_columns(time=pl.col("time") - pl.duration(minutes=offset))
        joined = reference.join(shifted, on="time", suffix="_series").drop_nulls()
        r = joined.select(pl.corr("value", "value_series")).item() if joined.height > 2 else None
        rows.append({"offset_min": offset, "pearson_r": r, "n": joined.height})
    return pl.DataFrame(rows, schema={"offset_min": pl.Int64, "pearson_r": pl.Float64, "n": pl.Int64})
