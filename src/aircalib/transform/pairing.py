"""Spatial matching of citizen sensors with reference stations."""

from __future__ import annotations

import polars as pl

EARTH_RADIUS_KM = 6371.0088  # mean Earth radius (IUGG)


def haversine_km(lat1: pl.Expr, lon1: pl.Expr, lat2: pl.Expr, lon2: pl.Expr) -> pl.Expr:
    """Great-circle distance in km between two points given in decimal degrees."""
    phi1, phi2 = lat1.radians(), lat2.radians()
    dphi = phi2 - phi1
    dlmb = (lon2 - lon1).radians()
    a = (dphi / 2).sin() ** 2 + phi1.cos() * phi2.cos() * (dlmb / 2).sin() ** 2
    return 2 * EARTH_RADIUS_KM * a.sqrt().arcsin()


def pairs_within(
    sensors: pl.LazyFrame | pl.DataFrame,
    stations: pl.LazyFrame | pl.DataFrame,
    max_km: float,
) -> pl.LazyFrame:
    """All (sensor, station) couples closer than ``max_km``, with their distance.

    ``sensors`` needs ``sensor_id, lat, lon``; ``stations`` needs
    ``sampling_point, lat, lon``. A cheap latitude band pre-filter keeps the
    cross join small before the exact haversine distance is computed.
    """
    lat_margin = max_km / 111.0  # 1° of latitude ≈ 111 km
    s = sensors.lazy().rename({"lat": "sensor_lat", "lon": "sensor_lon"})
    r = stations.lazy().rename({"lat": "station_lat", "lon": "station_lon"})
    return (
        s.join(r, how="cross")
        .filter((pl.col("sensor_lat") - pl.col("station_lat")).abs() <= lat_margin)
        .with_columns(
            distance_km=haversine_km(
                pl.col("sensor_lat"), pl.col("sensor_lon"), pl.col("station_lat"), pl.col("station_lon")
            )
        )
        .filter(pl.col("distance_km") <= max_km)
    )


def nearest_k(
    points: pl.DataFrame,
    candidates: pl.DataFrame,
    *,
    point_id: str,
    candidate_id: str,
    k: int,
    max_km: float,
) -> pl.DataFrame:
    """The ``k`` nearest candidates of each point within ``max_km`` (rank 1 = nearest).

    Ties are broken by candidate id so that the result is reproducible.
    """
    couples = pairs_within(
        points.select(point_id, "lat", "lon"), candidates.select(candidate_id, "lat", "lon"), max_km
    ).collect()
    return (
        couples.sort(point_id, "distance_km", candidate_id)
        .with_columns(rank=pl.int_range(1, pl.len() + 1).over(point_id))
        .filter(pl.col("rank") <= k)
        .select(point_id, candidate_id, "distance_km", "rank")
    )


def weather_links(pairs: pl.DataFrame, weather: pl.DataFrame) -> pl.DataFrame:
    """Official weather stations of each pair, with the sensor → weather-station distance.

    ``pairs`` has ``sensor_id, sampling_point, sensor_lat, sensor_lon``; ``weather`` has
    ``sampling_point, source, station_id, rank, lat, lon, distance_km`` (distance to
    the EEA station, used to choose it). The humidity fallback is measured at the
    weather station, so the distance that matters for the sensor is recomputed.
    """
    return (
        pairs.select("sensor_id", "sampling_point", "sensor_lat", "sensor_lon")
        .join(weather, on="sampling_point", how="inner")
        .with_columns(
            sensor_weather_km=haversine_km(
                pl.col("sensor_lat"), pl.col("sensor_lon"), pl.col("lat"), pl.col("lon")
            )
        )
        .select(
            "sensor_id",
            "sampling_point",
            "source",
            weather_station_id="station_id",
            weather_rank="rank",
            sensor_weather_km="sensor_weather_km",
            station_weather_km="distance_km",
        )
        .sort("sensor_id", "weather_rank")
    )
