from datetime import datetime, timedelta

import numpy as np
import polars as pl
import pytest

from aircalib.transform.hourly import hourly_mean, lagged_correlation


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
