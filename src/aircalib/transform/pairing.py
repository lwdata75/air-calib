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
