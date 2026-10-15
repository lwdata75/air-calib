"""Official weather (DWD, Meteo-France): quality rules and the station chosen for each pair.

The official relative humidity is the **main** humidity input of the model (the
sensors' own probes are reliable only 39 % of the time). Each hour gets a
``humidity_flag``:

* ``missing``: empty, or the DWD ``-999`` sentinel;
* ``doubtful``: Meteo-France quality code 2 ("douteuse");
* ``out_of_range``: outside 0–100 %;
* ``ok``. A value of 100 % is kept: at an official station it is real fog or dew.
"""

from __future__ import annotations

import polars as pl

OK, MISSING, DOUBTFUL, OUT_OF_RANGE = "ok", "missing", "doubtful", "out_of_range"
RULES = (MISSING, DOUBTFUL, OUT_OF_RANGE)
DWD, METEO_FRANCE = "dwd", "meteo_france"
COLUMNS = ["source", "station_id", "hour", "temperature", "humidity", "humidity_flag"]

# Meteo-France quality codes: 9 filtered (automatic checks only), 0 protected, 1 validated, 2 doubtful.
MF_DOUBTFUL_CODE = 2


def _humidity_flag(rh: pl.Expr, doubtful: pl.Expr, max_pct: float) -> pl.Expr:
    """Status of an official humidity value: missing, doubtful, out of range or ok."""
    return (
        pl.when(rh.is_null() | rh.is_nan())
        .then(pl.lit(MISSING))
        .when(doubtful)
        .then(pl.lit(DOUBTFUL))
        .when((rh < 0) | (rh > max_pct))
        .then(pl.lit(OUT_OF_RANGE))
        .otherwise(pl.lit(OK))
    )


def official_dwd(lf: pl.LazyFrame | pl.DataFrame, sentinel: float, max_pct: float) -> pl.LazyFrame:
    """DWD bronze rows → common hourly columns; ``-999`` becomes missing."""
    clean = {
        c: pl.when(pl.col(c) == sentinel).then(None).otherwise(pl.col(c)) for c in ("temperature", "humidity")
    }
    return (
        lf.lazy()
        .with_columns(**clean)
        .with_columns(
            source=pl.lit(DWD),
            hour=pl.col("time_utc"),
            humidity_flag=_humidity_flag(pl.col("humidity"), pl.lit(False), max_pct),
        )
        .select(COLUMNS)
    )


def official_meteo_france(lf: pl.LazyFrame | pl.DataFrame, max_pct: float) -> pl.LazyFrame:
    """Meteo-France bronze rows → common hourly columns; doubtful values are flagged."""
    doubtful = (pl.col("humidity_quality") == MF_DOUBTFUL_CODE).fill_null(False)
    temperature_doubtful = (pl.col("temperature_quality") == MF_DOUBTFUL_CODE).fill_null(False)
    return (
        lf.lazy()
        .with_columns(
            source=pl.lit(METEO_FRANCE),
            hour=pl.col("time_utc"),
            temperature=pl.when(temperature_doubtful).then(None).otherwise(pl.col("temperature")),
            humidity_flag=_humidity_flag(pl.col("humidity"), doubtful, max_pct),
        )
        .select(COLUMNS)
    )


def choose_stations(candidates: pl.DataFrame, completeness: pl.DataFrame, tie_km: float) -> pl.DataFrame:
    """Rank the official stations of each pair: the one nearest to the **sensor** first.

    ``candidates``: ``pair_id, source, weather_station_id, sensor_weather_km``;
    ``completeness``: ``source, weather_station_id, humidity_hours``. Stations whose
    distance to the sensor is within ``tie_km`` of the nearest are a tie, broken by
    the number of hours with humidity (then by distance and id). Returns the
    candidates with ``choice`` = 1 (main station), 2 (hourly fallback), ...
    """
    nearest = pl.col("sensor_weather_km").min().over("pair_id")
    tied = pl.col("sensor_weather_km") <= nearest + tie_km
    return (
        candidates.join(completeness, on=["source", "weather_station_id"], how="left")
        .with_columns(pl.col("humidity_hours").fill_null(0), _tied=tied)
        .sort(
            ["pair_id", "_tied", "humidity_hours", "sensor_weather_km", "weather_station_id"],
            descending=[False, True, True, False, False],
        )
        .with_columns(choice=pl.int_range(1, pl.len() + 1).over("pair_id").cast(pl.UInt32))
        .drop("_tied")
    )
