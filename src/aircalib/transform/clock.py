"""Automatic clock check on every pair: is the sensor hour the station hour?

For each pair, the hour-to-hour *change* of the sensor's PM2.5 at hour ``h`` is
correlated with the change of the reference at ``h + k`` for ``k`` in
``[-max_lag, max_lag]``. After the EEA clock correction (``Start - 1 h``), the best
lag must be 0.

Why changes and not levels: pollution levels drift slowly (weather, day/night),
so the correlation of levels is almost flat between lags 0 and ±1 (0.920 vs
0.921 for a good pair) and its maximum is decided by noise or by the travel time
of a plume between station and sensor. Hour-to-hour changes have a sharp peak at
the true offset, which is what a clock error moves.

Verdict (thresholds in ``configs/default.yaml``, section ``clock``):

* ``clock_unverifiable``: fewer than ``min_hours`` common hours, or a best
  correlation below ``min_r`` (the changes carry no readable timing);
* ``clock_mismatch``: the best lag is not 0 and beats lag 0 by at least ``min_z``
  (Fisher z of the two correlations);
* ``ok`` otherwise.

The same test is run per **station**, pooling the hours of all its pairs: sensor
timestamps all come from the same server, so a timing problem is a station's
problem, and pooling sees it where a single pair is too noisy.
"""

from __future__ import annotations

import polars as pl

OK, MISMATCH, UNVERIFIABLE = "ok", "clock_mismatch", "clock_unverifiable"


def hourly_changes(hours: pl.DataFrame) -> pl.DataFrame:
    """Replace ``sensor`` and ``reference`` by their change since the previous hour (null after a gap)."""
    previous = pl.col("hour").shift().over("pair_id")
    consecutive = (pl.col("hour") - previous) == pl.duration(hours=1)
    return hours.sort("pair_id", "hour").with_columns(
        [
            pl.when(consecutive).then(pl.col(c) - pl.col(c).shift().over("pair_id")).alias(c)
            for c in ("sensor", "reference")
        ]
    )


def lag_correlations(hours: pl.DataFrame, max_lag_h: int, group: str = "pair_id") -> pl.DataFrame:
    """Correlation per ``group`` (a pair, or a station pooling its pairs) and lag.

    ``hours``: ``pair_id, hour, sensor, reference`` (+ ``group`` if different). For lag
    ``k`` the sensor hour ``h`` of a pair meets the reference of the same pair labelled ``h + k``.
    """
    keep = ["pair_id", "hour"] if group == "pair_id" else ["pair_id", group, "hour"]
    sensor = hours.select(*keep, "sensor").drop_nulls()
    reference = hours.select("pair_id", "hour", "reference").drop_nulls()
    frames = []
    for lag in range(-max_lag_h, max_lag_h + 1):
        shifted = reference.with_columns(hour=pl.col("hour") - pl.duration(hours=lag))
        frames.append(
            sensor.join(shifted, on=["pair_id", "hour"])
            .group_by(group)
            .agg(pearson_r=pl.corr("sensor", "reference"), n_hours=pl.len())
            .with_columns(lag_h=pl.lit(lag, dtype=pl.Int32))
        )
    return pl.concat(frames).sort(group, "lag_h")


def clock_status(
    correlations: pl.DataFrame, min_hours: int, min_r: float, min_z: float, group: str = "pair_id"
) -> pl.DataFrame:
    """One row per group: correlation at lag 0, best lag and its correlation, Fisher z, verdict."""
    at_zero = correlations.filter(pl.col("lag_h") == 0).select(group, r_lag0="pearson_r", n_hours="n_hours")
    best = (
        correlations.drop_nulls("pearson_r")
        .sort([group, "pearson_r", "lag_h"], descending=[False, True, False])
        .unique(group, keep="first")
        .select(group, best_lag_h="lag_h", r_best="pearson_r")
    )
    z = (pl.col("r_best").arctanh() - pl.col("r_lag0").arctanh()) * ((pl.col("n_hours") - 3) / 2).sqrt()
    return (
        at_zero.join(best, on=group, how="left")
        .with_columns(z=z)
        .with_columns(
            clock_flag=pl.when(
                (pl.col("n_hours").fill_null(0) < min_hours) | (pl.col("r_best").fill_null(0) < min_r)
            )
            .then(pl.lit(UNVERIFIABLE))
            .when((pl.col("best_lag_h") != 0) & (pl.col("z") >= min_z))
            .then(pl.lit(MISMATCH))
            .otherwise(pl.lit(OK))
        )
        .sort(group)
    )
