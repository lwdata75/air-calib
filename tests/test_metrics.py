from datetime import datetime, timedelta

import polars as pl
import pytest

from aircalib.config import get_settings
from aircalib.evaluation.metrics import bin_label, daily_means, epa_targets, error_profile, regression_metrics

CFG = get_settings().evaluation


def _pair(pair_id, reference, sensor, start=datetime(2024, 1, 1)):
    return pl.DataFrame(
        {
            "pair_id": [pair_id] * len(reference),
            "hour": [start + timedelta(hours=i) for i in range(len(reference))],
            "reference": reference,
            "sensor": sensor,
        }
    )


def test_regression_metrics_follow_epa_conventions():
    ref = [float(v) for v in range(1, 21)]
    out = regression_metrics(_pair("a", ref, [2 * v + 1 for v in ref]), ["pair_id"]).row(0, named=True)
    assert out["slope"] == pytest.approx(2.0)
    assert out["intercept"] == pytest.approx(1.0)
    assert out["r2"] == pytest.approx(1.0)
    # RMSE of sensor - reference (= v + 1), not of the (zero) regression residuals.
    assert out["rmse"] == pytest.approx(sum((v + 1) ** 2 for v in ref) ** 0.5 / len(ref) ** 0.5)


def test_epa_targets_each_criterion():
    metrics = pl.DataFrame(
        {
            "r2": [0.8, 0.8, 0.6],
            "slope": [1.2, 1.5, 1.0],
            "intercept": [2.0, 2.0, 0.0],
            "rmse": [5.0, 5.0, 5.0],
        }
    )
    out = epa_targets(metrics, CFG)
    assert out["epa_all"].to_list() == [True, False, False]  # slope 1.5 fails; R2 0.6 fails
    assert out["epa_slope"].to_list() == [True, False, True]


def test_daily_means_keep_complete_days_only():
    full = _pair("a", [10.0] * 24, [12.0] * 24)
    partial = _pair("a", [10.0] * 10, [12.0] * 10, start=datetime(2024, 1, 2))
    out = daily_means(pl.concat([full, partial]), ["pair_id"], min_hours=18)
    assert out["day"].to_list() == [datetime(2024, 1, 1).date()]
    assert out["sensor"].item() == pytest.approx(12.0)


def test_error_profile_ignores_near_zero_ratios():
    frame = _pair("a", [0.2, 10.0, 20.0], [5.0, 15.0, 30.0])
    out = error_profile(frame, ["pair_id"]).row(0, named=True)
    assert out["ratio_median"] == pytest.approx(1.5)  # 5 / 0.2 = 25 is left out
    assert out["hours"] == 3


def test_bin_labels_sort_in_order():
    frame = pl.DataFrame({"rh": [40.0, 55.0, 97.0]})
    out = frame.select(b=bin_label(pl.col("rh"), [50, 60, 95], " %"))["b"].to_list()
    assert out == ["0. < 50 %", "1. 50–60 %", "3. ≥ 95 %"]
