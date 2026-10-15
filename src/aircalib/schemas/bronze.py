"""Bronze contracts: columns and types of each source, content unchanged.

Bronze only guarantees *structure* (exact column set, types, identifiers
present). Values are not judged here: duplicates, outliers, saturated
humidity and ``-999`` sentinels are kept and handled in silver, where every
removal is counted in the funnel report.
"""

from __future__ import annotations

import pandera.polars as pa
import polars as pl


class SensorCommunityPm(pa.DataFrameModel):
    """One row per raw measurement of a particle sensor (SDS011)."""

    sensor_id: pl.Int64 = pa.Field(nullable=False)
    sensor_type: pl.String = pa.Field(nullable=False)
    location: pl.Int64 = pa.Field(nullable=True)
    lat: pl.Float64 = pa.Field(nullable=True)
    lon: pl.Float64 = pa.Field(nullable=True)
    timestamp: pl.Datetime = pa.Field(nullable=True, description="UTC, null if unparsable")
    P1: pl.Float64 = pa.Field(nullable=True, description="PM10, µg/m³")
    P2: pl.Float64 = pa.Field(nullable=True, description="PM2.5, µg/m³")

    class Config:
        strict = True  # no unexpected column
        coerce = False  # types must already be right: a mismatch is a bug, not data


class SensorCommunityHumidity(pa.DataFrameModel):
    """One row per raw measurement of a temperature/humidity probe."""

    sensor_id: pl.Int64 = pa.Field(nullable=False)
    sensor_type: pl.String = pa.Field(nullable=False)
    location: pl.Int64 = pa.Field(nullable=True)
    lat: pl.Float64 = pa.Field(nullable=True)
    lon: pl.Float64 = pa.Field(nullable=True)
    timestamp: pl.Datetime = pa.Field(nullable=True)
    temperature: pl.Float64 = pa.Field(nullable=True, description="°C")
    humidity: pl.Float64 = pa.Field(nullable=True, description="relative humidity, %")
    pressure: pl.Float64 = pa.Field(nullable=True, description="Pa (BME280 only)")

    class Config:
        strict = True  # no unexpected column
        coerce = False  # types must already be right: a mismatch is a bug, not data


class Eea(pa.DataFrameModel):
    """Hourly E1a observations; ``start`` is on the station clock (see journal)."""

    sampling_point: pl.String = pa.Field(nullable=False)
    pollutant: pl.Int32 = pa.Field(nullable=False)
    start: pl.Datetime = pa.Field(nullable=False)
    end: pl.Datetime = pa.Field(nullable=False)
    value: pl.Float64 = pa.Field(nullable=True, description="µg/m³, -999 = missing")
    unit: pl.String = pa.Field(nullable=True)
    agg_type: pl.String = pa.Field(nullable=True)
    validity: pl.Int32 = pa.Field(nullable=False)
    verification: pl.Int32 = pa.Field(nullable=False)

    class Config:
        strict = True  # no unexpected column
        coerce = False  # types must already be right: a mismatch is a bug, not data


class Dwd(pa.DataFrameModel):
    """Contract of a DWD station table in bronze."""

    station_id: pl.Int64 = pa.Field(nullable=False)
    time_utc: pl.Datetime = pa.Field(nullable=False)
    quality_level: pl.Int64 = pa.Field(nullable=True)
    temperature: pl.Float64 = pa.Field(nullable=True, description="°C, -999 = missing")
    humidity: pl.Float64 = pa.Field(nullable=True, description="%, -999 = missing")

    class Config:
        strict = True  # no unexpected column
        coerce = False  # types must already be right: a mismatch is a bug, not data


class MeteoFrance(pa.DataFrameModel):
    """Contract of a Météo-France station table in bronze."""

    station_id: pl.Int64 = pa.Field(nullable=False)
    name: pl.String = pa.Field(nullable=True)
    lat: pl.Float64 = pa.Field(nullable=False)
    lon: pl.Float64 = pa.Field(nullable=False)
    altitude_m: pl.Float64 = pa.Field(nullable=True)
    time_utc: pl.Datetime = pa.Field(nullable=False)
    temperature: pl.Float64 = pa.Field(nullable=True, description="°C")
    temperature_quality: pl.Int64 = pa.Field(nullable=True)
    humidity: pl.Float64 = pa.Field(nullable=True, description="%")
    humidity_quality: pl.Int64 = pa.Field(nullable=True)

    class Config:
        strict = True  # no unexpected column
        coerce = False  # types must already be right: a mismatch is a bug, not data
