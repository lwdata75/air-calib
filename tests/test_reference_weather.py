from datetime import date, datetime

import polars as pl

from aircalib.config import get_settings
from aircalib.transform import reference as ref
from aircalib.transform import weather as wx

S = get_settings()


def _eea(rows):
    return pl.DataFrame(
        rows,
        schema={
            "sampling_point": pl.String,
            "pollutant": pl.Int32,
            "start": pl.Datetime("us"),
            "value": pl.Float64,
            "validity": pl.Int32,
            "verification": pl.Int32,
        },
        orient="row",
    )


def test_reference_rules_and_utc_hour():
    rows = [
        ("P", 6001, datetime(2024, 1, 1, 0), 9.0, 1, 1),  # UTC 2023-12-31 23:00: outside the period
        ("P", 6001, datetime(2024, 1, 1, 1), 9.0, 1, 1),  # UTC 00:00: first hour, ok
        ("P", 6001, datetime(2024, 1, 1, 2), -999.0, -1, 1),  # sentinel
        ("P", 6001, datetime(2024, 1, 1, 3), 7.0, -1, 1),  # value but invalid flag
        ("P", 6001, datetime(2024, 1, 1, 4), 7.0, 1, 2),  # not verified
        ("P", 6001, datetime(2024, 1, 1, 5), -0.8, 1, 1),  # noise around zero: kept as reported
        ("P", 6001, datetime(2024, 1, 1, 6), -6.2, 1, 1),  # beyond the noise floor
        ("P", 5, datetime(2024, 1, 1, 7), 12.0, 2, 1),  # PM10, valid below detection limit
    ]
    out = ref.flag_reference(_eea(rows), S.eea, date(2024, 1, 1), date(2025, 12, 31)).collect()
    assert out["quality_flag"].to_list() == [
        "outside_period",
        "ok",
        "invalid",
        "invalid",
        "not_verified",
        "ok",
        "negative_beyond_uncertainty",
        "ok",
    ]
    assert out["hour"][1] == datetime(2024, 1, 1, 0)  # Start - 1 h
    assert out["value"][5] == -0.8  # unchanged, not clipped
    assert out["pollutant"].to_list()[-2:] == ["pm25", "pm10"]


def test_official_weather_flags():
    dwd = pl.DataFrame(
        {
            "station_id": [1, 1],
            "time_utc": [datetime(2024, 1, 1, 0), datetime(2024, 1, 1, 1)],
            "quality_level": [3, 3],
            "temperature": [2.0, -999.0],
            "humidity": [100.0, -999.0],
        }
    )
    out = wx.official_dwd(dwd, -999.0, 100.0).collect()
    assert out["humidity_flag"].to_list() == ["ok", "missing"]  # 100 % at a station is real fog
    assert out["temperature"].to_list() == [2.0, None]
    mf = pl.DataFrame(
        {
            "station_id": [7, 7, 7],
            "time_utc": [datetime(2024, 1, 1, h) for h in range(3)],
            "temperature": [5.0, 6.0, 7.0],
            "temperature_quality": [1, 2, 9],
            "humidity": [80.0, 81.0, None],
            "humidity_quality": [9, 2, None],
        }
    )
    out = wx.official_meteo_france(mf, 100.0).collect()
    assert out["humidity_flag"].to_list() == ["ok", "doubtful", "missing"]  # code 9 = filtered, kept
    assert out["temperature"].to_list() == [5.0, None, 7.0]


def test_weather_station_nearest_to_sensor_ties_broken_by_completeness():
    candidates = pl.DataFrame(
        {
            "pair_id": ["a", "a", "b", "b"],
            "source": ["dwd"] * 4,
            "weather_station_id": [1, 2, 1, 3],
            "sensor_weather_km": [8.0, 5.0, 6.0, 6.3],
            "weather_rank": [1, 2, 1, 2],
        }
    )
    completeness = pl.DataFrame(
        {"source": ["dwd"] * 3, "weather_station_id": [1, 2, 3], "humidity_hours": [100, 90, 200]}
    )
    out = wx.choose_stations(candidates, completeness, tie_km=0.5)
    main = dict(out.filter(pl.col("choice") == 1).select("pair_id", "weather_station_id").iter_rows())
    assert main["a"] == 2  # rank 2 for the EEA station, but nearest to the sensor
    assert main["b"] == 3  # 0.3 km farther = tie, more complete wins
