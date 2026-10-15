"""Silver contracts: cleaned, hourly (UTC, hour labelled by its start), one status per row.

On top of the column set and types, silver contracts check what the cleaning
promises: every status belongs to the known rules, keys are unique, values that
are flagged ``ok`` respect the physical bounds the rules enforce.
"""

from __future__ import annotations

import pandera.polars as pa
import polars as pl

from aircalib.config import get_settings
from aircalib.silver.rules import HOUR_RULES
from aircalib.transform import clock, particles, reference, weather
from aircalib.transform import humidity as hum

OK = "ok"
PM_STATUSES = [OK, *particles.RULES]
HOUR_STATUSES = [OK, particles.NO_VALID_READING, particles.INCOMPLETE_HOUR]
PAIR_STATUSES = [OK, *HOUR_RULES]
CLOCK_STATUSES = [clock.OK, clock.MISMATCH, clock.UNVERIFIABLE, clock.INSTRUMENT_LAG]
PM_MAX = get_settings().particles.max_ugm3


class PmReadings(pa.DataFrameModel):
    """Every SDS011 reading of a sensor with its position segment and quality flag (nothing removed)."""

    sensor_id: pl.Int64 = pa.Field(nullable=False)
    segment: pl.UInt32 = pa.Field(nullable=False, ge=1)
    timestamp: pl.Datetime = pa.Field(nullable=False)
    P1: pl.Float64 = pa.Field(nullable=True)
    P2: pl.Float64 = pa.Field(nullable=True)
    quality_flag: pl.String = pa.Field(nullable=False, isin=PM_STATUSES)

    @pa.dataframe_check
    def ok_readings_are_physical(cls, data: pa.PolarsData) -> pl.LazyFrame:
        """Every reading flagged ``ok`` is physical: 0 <= PM2.5 <= PM10 <= the sensor's range."""
        ok = pl.col("quality_flag") == OK
        return data.lazyframe.select(
            (~ok | ((pl.col("P2") >= 0) & (pl.col("P2") <= pl.col("P1")) & (pl.col("P1") <= PM_MAX))).alias(
                "ok"
            )
        )

    class Config:
        strict = True
        coerce = False


class PmHourly(pa.DataFrameModel):
    """Contract of the hourly particle table: one row per sensor, segment and hour."""

    sensor_id: pl.Int64 = pa.Field(nullable=False)
    segment: pl.UInt32 = pa.Field(nullable=False)
    hour: pl.Datetime = pa.Field(nullable=False)
    pm25_mean: pl.Float64 = pa.Field(nullable=True, ge=0)
    pm25_median: pl.Float64 = pa.Field(nullable=True, ge=0)
    pm25_std: pl.Float64 = pa.Field(nullable=True, ge=0)
    pm10_mean: pl.Float64 = pa.Field(nullable=True, ge=0)
    pm10_median: pl.Float64 = pa.Field(nullable=True, ge=0)
    pm10_std: pl.Float64 = pa.Field(nullable=True, ge=0)
    n_readings: pl.UInt32 = pa.Field(nullable=False, ge=1)
    n_valid: pl.UInt32 = pa.Field(nullable=False)
    main_flag: pl.String = pa.Field(nullable=True, isin=list(particles.RULES))
    cadence_s: pl.Float64 = pa.Field(nullable=True, gt=0)
    expected_readings: pl.Float64 = pa.Field(nullable=True, gt=0)
    completeness: pl.Float64 = pa.Field(nullable=True, ge=0)
    hour_flag: pl.String = pa.Field(nullable=False, isin=HOUR_STATUSES)

    class Config:
        strict = True
        coerce = False
        unique = ["sensor_id", "segment", "hour"]


class HumidityHourly(pa.DataFrameModel):
    """Probe hours; ``reliable`` = usable value in a healthy month and not a faulty hour."""

    sensor_id: pl.Int64 = pa.Field(nullable=False)
    sensor_type: pl.String = pa.Field(nullable=False)
    location: pl.Int64 = pa.Field(nullable=True)
    hour: pl.Datetime = pa.Field(nullable=False)
    saturated_share: pl.Float64 = pa.Field(ge=0, le=1)
    stuck_share: pl.Float64 = pa.Field(ge=0, le=1)
    stuck_low_share: pl.Float64 = pa.Field(ge=0, le=1)
    out_of_range_share: pl.Float64 = pa.Field(ge=0, le=1)
    usable_share: pl.Float64 = pa.Field(ge=0, le=1)
    humidity: pl.Float64 = pa.Field(nullable=True, ge=0, le=100)
    temperature: pl.Float64 = pa.Field(nullable=True)
    raw_humidity: pl.Float64 = pa.Field(nullable=True)
    n: pl.UInt32 = pa.Field(nullable=False, ge=1)
    faulty: pl.Boolean = pa.Field(nullable=False)
    fault: pl.String = pa.Field(nullable=True, isin=list(hum.FAULTS))
    month_status: pl.String = pa.Field(nullable=True, isin=[hum.DEAD, hum.HEALTHY])
    reliable: pl.Boolean = pa.Field(nullable=True)

    class Config:
        strict = True
        coerce = False


class EeaHourly(pa.DataFrameModel):
    """Reference hours on UTC (``Start - eea.clock_offset_h``); only ``ok`` values are a target."""

    sampling_point: pl.String = pa.Field(nullable=False)
    pollutant: pl.String = pa.Field(nullable=False, isin=["pm25", "pm10"])
    hour: pl.Datetime = pa.Field(nullable=False)
    value: pl.Float64 = pa.Field(nullable=True)
    validity: pl.Int32 = pa.Field(nullable=False)
    verification: pl.Int32 = pa.Field(nullable=False)
    quality_flag: pl.String = pa.Field(
        nullable=False, isin=[reference.OK, *(r for r in reference.RULES if r != reference.OUTSIDE_PERIOD)]
    )

    class Config:
        strict = True
        coerce = False
        unique = ["sampling_point", "pollutant", "hour"]


class WeatherHourly(pa.DataFrameModel):
    """Contract of the hourly official weather table."""

    source: pl.String = pa.Field(nullable=False, isin=[weather.DWD, weather.METEO_FRANCE])
    station_id: pl.Int64 = pa.Field(nullable=False)
    hour: pl.Datetime = pa.Field(nullable=False)
    temperature: pl.Float64 = pa.Field(nullable=True)
    humidity: pl.Float64 = pa.Field(nullable=True)
    humidity_flag: pl.String = pa.Field(nullable=False, isin=[weather.OK, *weather.RULES])

    class Config:
        strict = True
        coerce = False
        unique = ["source", "station_id", "hour"]


class PairHours(pa.DataFrameModel):
    """One row per sensor segment and hour, with every source joined and a ``status``."""

    sensor_id: pl.Int64 = pa.Field(nullable=False)
    segment: pl.UInt32 = pa.Field(nullable=False)
    hour: pl.Datetime = pa.Field(nullable=False)
    pm25_mean: pl.Float64 = pa.Field(nullable=True, ge=0)
    pm25_median: pl.Float64 = pa.Field(nullable=True, ge=0)
    pm25_std: pl.Float64 = pa.Field(nullable=True, ge=0)
    pm10_mean: pl.Float64 = pa.Field(nullable=True, ge=0)
    pm10_median: pl.Float64 = pa.Field(nullable=True, ge=0)
    pm10_std: pl.Float64 = pa.Field(nullable=True, ge=0)
    n_readings: pl.UInt32 = pa.Field(nullable=False)
    n_valid: pl.UInt32 = pa.Field(nullable=False)
    main_flag: pl.String = pa.Field(nullable=True)
    cadence_s: pl.Float64 = pa.Field(nullable=True)
    expected_readings: pl.Float64 = pa.Field(nullable=True)
    completeness: pl.Float64 = pa.Field(nullable=True)
    hour_flag: pl.String = pa.Field(nullable=False, isin=HOUR_STATUSES)
    pair_id: pl.String = pa.Field(nullable=False)
    location: pl.Int64 = pa.Field(nullable=True)
    sampling_point: pl.String = pa.Field(nullable=True)
    station_code: pl.String = pa.Field(nullable=True)
    cv_group: pl.String = pa.Field(nullable=True, description="group of the station-wise cross-validation")
    country: pl.String = pa.Field(nullable=False, isin=["DE", "FR"])
    station_type: pl.String = pa.Field(nullable=True)
    station_area: pl.String = pa.Field(nullable=True)
    measurement_method: pl.String = pa.Field(nullable=True, description="EEA field, e.g. TEOM, BETA")
    method_class: pl.String = pa.Field(nullable=True, description="instrument family (eea.method_classes)")
    distance_km: pl.Float64 = pa.Field(nullable=True, ge=0)
    distance_band: pl.String = pa.Field(nullable=True, isin=["near", "far"])
    pair_status: pl.String = pa.Field(nullable=False)
    ref_pm25: pl.Float64 = pa.Field(nullable=True)
    ref_pm25_flag: pl.String = pa.Field(nullable=True)
    ref_pm10: pl.Float64 = pa.Field(nullable=True)
    ref_pm10_flag: pl.String = pa.Field(nullable=True)
    official_humidity: pl.Float64 = pa.Field(nullable=True, ge=0, le=100)
    official_temperature: pl.Float64 = pa.Field(nullable=True)
    weather_source: pl.String = pa.Field(nullable=True)
    weather_station_id: pl.Int64 = pa.Field(nullable=True)
    sensor_weather_km: pl.Float64 = pa.Field(nullable=True, ge=0)
    weather_fallback: pl.Boolean = pa.Field(nullable=True)
    local_humidity: pl.Float64 = pa.Field(nullable=True, ge=0, le=100)
    local_temperature: pl.Float64 = pa.Field(nullable=True)
    local_probe_id: pl.Int64 = pa.Field(nullable=True)
    local_probe_type: pl.String = pa.Field(nullable=True)
    local_reliable: pl.Boolean = pa.Field(nullable=False)
    clock_flag: pl.String = pa.Field(nullable=True, isin=CLOCK_STATUSES)
    station_clock_flag: pl.String = pa.Field(nullable=True, isin=CLOCK_STATUSES)
    status: pl.String = pa.Field(nullable=False, isin=PAIR_STATUSES)

    @pa.dataframe_check
    def usable_hours_are_complete(cls, data: pa.PolarsData) -> pl.LazyFrame:
        """An ``ok`` pair-hour has a valid sensor hour, a valid reference and official humidity."""
        ok = pl.col("status") == OK
        complete = (
            (pl.col("hour_flag") == OK)
            & (pl.col("ref_pm25_flag") == OK)
            & pl.col("official_humidity").is_not_null()
            & pl.col("distance_band").is_not_null()
        )
        return data.lazyframe.select((~ok | complete).alias("ok"))

    class Config:
        strict = True
        coerce = False
        unique = ["sensor_id", "segment", "hour"]
