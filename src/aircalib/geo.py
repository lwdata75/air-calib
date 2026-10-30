"""Distances sur la Terre."""

import polars as pl

RAYON_TERRE_KM = 6371.0


def distance_km(lat1: pl.Expr, lon1: pl.Expr, lat2: pl.Expr, lon2: pl.Expr) -> pl.Expr:
    """Distance à vol d'oiseau entre deux points (formule de haversine), en km."""
    phi1, phi2 = lat1.radians(), lat2.radians()
    dphi, dlambda = (lat2 - lat1).radians(), (lon2 - lon1).radians()
    a = (dphi / 2).sin() ** 2 + phi1.cos() * phi2.cos() * (dlambda / 2).sin() ** 2
    return 2 * RAYON_TERRE_KM * a.sqrt().arcsin()


def plus_proche(
    points: pl.DataFrame, cibles: pl.DataFrame, cle: str, rayon_km: float | None = None
) -> pl.DataFrame:
    """Pour chaque point (colonnes lat, lon), la cible la plus proche (colonnes `cle`, lat, lon).

    Renvoie les colonnes des points, plus `cle` et `distance_km`. Avec `rayon_km`, les points trop loin
    de toute cible sont retirés.
    """
    paires = points.join(cibles.rename({"lat": "lat_c", "lon": "lon_c"}), how="cross").with_columns(
        distance_km=distance_km(pl.col("lat"), pl.col("lon"), pl.col("lat_c"), pl.col("lon_c"))
    )
    if rayon_km is not None:
        paires = paires.filter(pl.col("distance_km") <= rayon_km)
    return paires.sort("distance_km").unique(points.columns, keep="first").drop("lat_c", "lon_c")
