"""Citizen neighbours: what do the other sensors around say? (The reference is never used here.)

* ``neighbour_links``: for each pair (sensor segment), the segments of *other* sensors
  within a radius whose periods overlap;
* ``neighbour_median``: at each hour, the median raw PM2.5 of the valid neighbours, and
  the deviation of the sensor from it, ``ln(own) - ln(median)``;
* ``health_scores``: per pair, how well its hour-to-hour changes follow its
  neighbours' median, and its typical offset. A sensor that follows nobody is
  probably broken: this is detectable **without an official station**.
"""

from __future__ import annotations

import polars as pl

from aircalib.transform.pairing import haversine_km


def neighbour_links(segments: pl.DataFrame, radius_km: float) -> pl.DataFrame:
    """``pair_id, neighbour_id``: other sensors' segments within ``radius_km`` and overlapping in time.

    ``segments`` needs ``pair_id, sensor_id, lat, lon, start, end``.
    """
    lat_margin = radius_km / 111.0
    other = segments.select(pl.all().name.suffix("_n"))
    return (
        segments.join(other, how="cross")
        .filter(
            pl.col("sensor_id") != pl.col("sensor_id_n"),
            (pl.col("lat") - pl.col("lat_n")).abs() <= lat_margin,
            pl.col("start") <= pl.col("end_n"),
            pl.col("start_n") <= pl.col("end"),
        )
        .filter(haversine_km(pl.col("lat"), pl.col("lon"), pl.col("lat_n"), pl.col("lon_n")) <= radius_km)
        .select("pair_id", neighbour_id="pair_id_n")
        .sort("pair_id", "neighbour_id")
    )


def neighbour_median(
    values: pl.DataFrame | pl.LazyFrame, links: pl.DataFrame, min_neighbours: int, floor: float
) -> pl.LazyFrame:
    """Per pair and hour: median of the neighbours' valid values and the log deviation from it.

    ``values``: ``pair_id, hour, value`` for valid sensor hours only. The pair's own
    value is never part of its median. ``neighbour_median`` is null below
    ``min_neighbours`` valid neighbours that hour.
    """
    values = values.lazy()
    around = (
        values.select("pair_id", "hour")
        .join(links.lazy(), on="pair_id")
        .join(values.select(neighbour_id="pair_id", hour="hour", other="value"), on=["neighbour_id", "hour"])
        .group_by("pair_id", "hour")
        .agg(neighbour_median=pl.col("other").median(), n_neighbours=pl.len())
    )
    clamp = pl.lit(floor)
    return (
        values.join(around, on=["pair_id", "hour"], how="left")
        .with_columns(pl.col("n_neighbours").fill_null(0).cast(pl.UInt32))
        .with_columns(
            neighbour_median=pl.when(pl.col("n_neighbours") >= min_neighbours).then(
                pl.col("neighbour_median")
            )
        )
        .with_columns(
            neighbour_log_ratio=pl.max_horizontal(pl.col("value"), clamp).log()
            - pl.max_horizontal(pl.col("neighbour_median"), clamp).log()
        )
        .with_columns(
            neighbour_log_ratio=pl.when(pl.col("neighbour_median").is_not_null()).then("neighbour_log_ratio")
        )
        .drop("value")
    )


def health_scores(hours: pl.DataFrame, min_hours: int, floor: float) -> pl.DataFrame:
    """Per pair: correlation of hourly changes of ln PM with the neighbours' median, and median offset.

    ``hours``: ``pair_id, hour, value, neighbour_median`` (valid sensor hours). Changes are
    taken between consecutive hours only, as in the clock check. ``health_hours`` counts
    the hours used; below ``min_hours`` the scores are null (not enough evidence).
    """
    clamp = pl.lit(floor)
    previous = pl.col("hour").shift().over("pair_id")
    consecutive = (pl.col("hour") - previous) == pl.duration(hours=1)
    logs = (
        hours.drop_nulls("neighbour_median")
        .sort("pair_id", "hour")
        .with_columns(
            own=pl.max_horizontal("value", clamp).log(),
            around=pl.max_horizontal("neighbour_median", clamp).log(),
        )
    )
    changes = logs.with_columns(
        d_own=pl.when(consecutive).then(pl.col("own") - pl.col("own").shift().over("pair_id")),
        d_around=pl.when(consecutive).then(pl.col("around") - pl.col("around").shift().over("pair_id")),
    )
    scores = changes.group_by("pair_id").agg(
        health_hours=pl.len(),
        health_change_corr=pl.corr("d_own", "d_around"),
        health_level_corr=pl.corr("own", "around"),
        health_log_offset=(pl.col("own") - pl.col("around")).median(),
    )
    enough = pl.col("health_hours") >= min_hours
    return scores.with_columns(
        [
            pl.when(enough).then(pl.col(c)).alias(c)
            for c in ("health_change_corr", "health_level_corr", "health_log_offset")
        ]
    ).sort("pair_id")
