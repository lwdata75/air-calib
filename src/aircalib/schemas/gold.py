"""Gold contract: usable pair-hours, target and features, ready for the models (step 6).

``FEATURES`` lists the model inputs by family (documented in notebook 05). The
target is ``reference`` (ug/m3) or ``log_reference``; nothing derived from the
reference is ever a feature.
"""

from __future__ import annotations

import pandera.polars as pa
import polars as pl

KEYS = [
    "pair_id", "sensor_id", "segment", "hour", "cv_group", "fold", "country", "distance_band",
    "sane_pair", "training_zone", "clock_flag",
]  # fmt: skip
TARGET = ["reference", "log_reference", "reference_pm10"]
FEATURES = {
    "capteur brut": ["pm25_mean", "pm25_median", "pm25_std", "pm10_mean", "n_valid", "completeness",
                     "pm25_pm10_ratio", "log_pm25"],
    "météo officielle": ["official_humidity", "official_temperature", "dew_point", "dew_point_depression",
                         "sensor_weather_km", "weather_fallback"],
    "sonde locale (secondaire)": ["local_humidity", "local_temperature", "local_reliable"],
    "modèle physique": ["kohler_c", "physics_pm25", "log_physics_pm25"],
    "temps": ["hour_sin", "hour_cos", "weekday_sin", "weekday_cos", "month_sin", "month_cos"],
    "dynamique (passé)": [
        f"{v}_{s}_{w}h" for v in ("pm25", "humidity") for w in (3, 6, 24) for s in ("mean", "std")
    ],
    "capteur": ["age_years", "missing_rate_24h", "missing_rate_168h"],
    "contexte": ["distance_km", "station_type", "station_area", "method_class"],
    "voisinage citoyen": ["neighbour_median", "n_neighbours", "neighbour_log_ratio"],
}  # fmt: skip
COLUMNS = [*KEYS, *TARGET, *[c for family in FEATURES.values() for c in family]]
CATEGORICAL = ["station_type", "station_area", "method_class"]

F32 = pl.Float32


class GoldPairHours(pa.DataFrameModel):
    """Contract of the gold table: keys, target, the 48 features, folds and statuses."""

    pair_id: pl.String = pa.Field(nullable=False)
    sensor_id: pl.Int64 = pa.Field(nullable=False)
    segment: pl.UInt32 = pa.Field(nullable=False)
    hour: pl.Datetime = pa.Field(nullable=False)
    cv_group: pl.String = pa.Field(nullable=False)
    fold: pl.Int32 = pa.Field(nullable=True, description="station fold (main country only)")
    country: pl.String = pa.Field(nullable=False, isin=["DE", "FR"])
    distance_band: pl.String = pa.Field(nullable=False, isin=["near", "far"])
    sane_pair: pl.Boolean = pa.Field(nullable=False)
    training_zone: pl.Boolean = pa.Field(nullable=False)
    clock_flag: pl.String = pa.Field(nullable=True)
    reference: F32 = pa.Field(nullable=False)
    log_reference: F32 = pa.Field(nullable=False)
    reference_pm10: F32 = pa.Field(nullable=True)
    pm25_mean: F32 = pa.Field(nullable=False, ge=0)
    pm25_median: F32 = pa.Field(nullable=False, ge=0)
    pm25_std: F32 = pa.Field(nullable=True, ge=0)
    pm10_mean: F32 = pa.Field(nullable=False, ge=0)
    n_valid: pl.UInt32 = pa.Field(nullable=False, ge=1)
    completeness: F32 = pa.Field(nullable=False)
    pm25_pm10_ratio: F32 = pa.Field(nullable=True, ge=0, le=1)
    log_pm25: F32 = pa.Field(nullable=False)
    official_humidity: F32 = pa.Field(nullable=False, ge=0, le=100)
    official_temperature: F32 = pa.Field(nullable=True)
    dew_point: F32 = pa.Field(nullable=True)
    dew_point_depression: F32 = pa.Field(nullable=True)
    sensor_weather_km: F32 = pa.Field(nullable=False, ge=0)
    weather_fallback: pl.Boolean = pa.Field(nullable=False)
    local_humidity: F32 = pa.Field(nullable=True, ge=0, le=100)
    local_temperature: F32 = pa.Field(nullable=True)
    local_reliable: pl.Boolean = pa.Field(nullable=False)
    kohler_c: F32 = pa.Field(nullable=False, ge=1)
    physics_pm25: F32 = pa.Field(nullable=False, ge=0)
    log_physics_pm25: F32 = pa.Field(nullable=False)
    hour_sin: F32 = pa.Field(nullable=False, ge=-1, le=1)
    hour_cos: F32 = pa.Field(nullable=False, ge=-1, le=1)
    weekday_sin: F32 = pa.Field(nullable=False, ge=-1, le=1)
    weekday_cos: F32 = pa.Field(nullable=False, ge=-1, le=1)
    month_sin: F32 = pa.Field(nullable=False, ge=-1, le=1)
    month_cos: F32 = pa.Field(nullable=False, ge=-1, le=1)
    pm25_mean_3h: F32 = pa.Field(nullable=False)
    pm25_std_3h: F32 = pa.Field(nullable=True)
    pm25_mean_6h: F32 = pa.Field(nullable=False)
    pm25_std_6h: F32 = pa.Field(nullable=True)
    pm25_mean_24h: F32 = pa.Field(nullable=False)
    pm25_std_24h: F32 = pa.Field(nullable=True)
    humidity_mean_3h: F32 = pa.Field(nullable=True)
    humidity_std_3h: F32 = pa.Field(nullable=True)
    humidity_mean_6h: F32 = pa.Field(nullable=True)
    humidity_std_6h: F32 = pa.Field(nullable=True)
    humidity_mean_24h: F32 = pa.Field(nullable=True)
    humidity_std_24h: F32 = pa.Field(nullable=True)
    age_years: F32 = pa.Field(nullable=True, ge=0)
    missing_rate_24h: F32 = pa.Field(nullable=False, ge=0, le=1)
    missing_rate_168h: F32 = pa.Field(nullable=False, ge=0, le=1)
    distance_km: F32 = pa.Field(nullable=False, ge=0)
    station_type: pl.String = pa.Field(nullable=False)
    station_area: pl.String = pa.Field(nullable=False)
    method_class: pl.String = pa.Field(nullable=False)
    neighbour_median: F32 = pa.Field(nullable=True, ge=0)
    n_neighbours: pl.UInt32 = pa.Field(nullable=False)
    neighbour_log_ratio: F32 = pa.Field(nullable=True)

    class Config:
        strict = True
        coerce = False
        unique = ["pair_id", "hour"]
