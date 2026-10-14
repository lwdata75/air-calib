import math
from datetime import datetime, timedelta

import numpy as np
import polars as pl
import pytest

from aircalib.transform.hourly import hourly_mean, lagged_correlation, offset_correlation, ten_minute_means


def test_hourly_mean_labels_hour_by_its_start():
    raw = pl.DataFrame(
        {
            "ts": [
                datetime(2024, 1, 1, 13, 0, 5),
                datetime(2024, 1, 1, 13, 59, 59),
                datetime(2024, 1, 1, 14, 0),
            ],
            "pm": [10.0, 20.0, 99.0],
        }
    )
    out = hourly_mean(raw, "ts", "pm", by=[]).collect()
    assert out["hour"].to_list() == [datetime(2024, 1, 1, 13), datetime(2024, 1, 1, 14)]
    assert out["mean"].to_list() == [15.0, 99.0]
    assert out["n"].to_list() == [2, 1]


def test_hourly_mean_counts_ignore_nulls():
    raw = pl.DataFrame({"ts": [datetime(2024, 1, 1, 13, 1), datetime(2024, 1, 1, 13, 2)], "pm": [None, 4.0]})
    out = hourly_mean(raw, "ts", "pm", by=[]).collect()
    assert out["n"].item() == 1
    assert out["mean"].item() == 4.0


@pytest.mark.parametrize("true_lag", [-1, 0, 1, 2])
def test_lagged_correlation_recovers_known_clock_offset(true_lag):
    rng = np.random.default_rng(0)
    hours = [datetime(2025, 1, 1) + timedelta(hours=h) for h in range(400)]
    signal = np.cumsum(rng.normal(size=400)) + 50
    sensor = pl.DataFrame({"hour": hours, "value": signal})
    # Reference labels each value ``true_lag`` hours later than the sensor clock.
    reference = pl.DataFrame({"hour": [h + timedelta(hours=true_lag) for h in hours], "value": signal})
    lags = lagged_correlation(sensor, reference, max_lag_h=3)
    best = lags.sort("pearson_r", descending=True)["lag_h"][0]
    assert best == true_lag
    assert lags.height == 7


def test_offset_correlation_finds_a_one_hour_clock_shift():
    # A smooth daily cycle sampled every 10 minutes, and a reference labelled one hour late (UTC+1).
    t0 = datetime(2024, 7, 1)
    times = [t0 + timedelta(minutes=10 * i) for i in range(6 * 24 * 10)]
    series = pl.DataFrame(
        {"time": times, "value": [math.sin(2 * math.pi * i / 144) for i in range(len(times))]}
    )
    hourly = series.filter(pl.col("time").dt.minute() == 0)
    reference = hourly.with_columns(time=pl.col("time") + pl.duration(hours=1))  # local-time labels
    out = offset_correlation(series, reference, list(range(-120, 121, 10)))
    assert out.sort("pearson_r", descending=True)["offset_min"][0] == -60
    same_clock = offset_correlation(series, hourly, list(range(-120, 121, 10)))
    assert same_clock.sort("pearson_r", descending=True)["offset_min"][0] == 0


def test_ten_minute_means_are_centred():
    raw = pl.DataFrame(
        {
            "ts": [datetime(2024, 1, 1, 0, 4, 59), datetime(2024, 1, 1, 0, 5), datetime(2024, 1, 1, 0, 14)],
            "v": [1.0, 3.0, 5.0],
        }
    )
    out = ten_minute_means(raw, "ts", "v", 10).collect()
    assert out["time"].to_list() == [datetime(2024, 1, 1, 0, 0), datetime(2024, 1, 1, 0, 10)]
    assert out["value"].to_list() == [1.0, 4.0]
