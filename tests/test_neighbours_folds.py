from datetime import datetime, timedelta

import numpy as np
import polars as pl
import pytest

from aircalib.evaluation.folds import station_folds
from aircalib.evaluation.metrics import back_transform, duan_smearing
from aircalib.transform.neighbours import health_scores, neighbour_links, neighbour_median

T0 = datetime(2024, 1, 1)


def _segments():
    return pl.DataFrame(
        {
            "pair_id": ["a-1", "b-1", "c-1", "d-1", "a-2"],
            "sensor_id": [1, 2, 3, 4, 1],
            "lat": [50.0, 50.01, 50.0, 52.0, 50.0],
            "lon": [8.0, 8.0, 8.02, 8.0, 8.0],
            "start": [T0] * 4 + [T0 + timedelta(days=400)],
            "end": [T0 + timedelta(days=30)] * 4 + [T0 + timedelta(days=401)],
        }
    )


def test_links_skip_self_far_sensors_and_other_periods():
    links = neighbour_links(_segments(), radius_km=3.0)
    assert links.filter(pl.col("pair_id") == "a-1")["neighbour_id"].to_list() == ["b-1", "c-1"]
    assert links.filter(pl.col("pair_id") == "a-2").is_empty()  # same place, a year later: nobody around


def test_neighbour_median_excludes_own_value():
    links = neighbour_links(_segments(), radius_km=3.0)
    values = pl.DataFrame({"pair_id": ["a-1", "b-1", "c-1"], "hour": [T0] * 3, "value": [100.0, 10.0, 12.0]})
    out = neighbour_median(values, links, min_neighbours=2, floor=1.0).collect().sort("pair_id")
    row = out.filter(pl.col("pair_id") == "a-1").row(0, named=True)
    assert row["neighbour_median"] == pytest.approx(11.0)  # median of b and c, not of a
    assert row["neighbour_log_ratio"] == pytest.approx(np.log(100 / 11))
    one = neighbour_median(values, links, min_neighbours=3, floor=1.0).collect()
    assert one["neighbour_median"].null_count() == 3  # nobody has 3 valid neighbours


def test_health_separates_a_follower_from_a_broken_sensor():
    rng = np.random.default_rng(1)
    hours = [T0 + timedelta(hours=i) for i in range(600)]
    around = np.exp(rng.normal(2, 0.5, 600))
    healthy = around * np.exp(rng.normal(0, 0.1, 600))
    broken = np.full(600, 600.0) * np.exp(rng.normal(0, 0.3, 600))
    frame = pl.DataFrame(
        {
            "pair_id": ["ok"] * 600 + ["broken"] * 600,
            "hour": hours * 2,
            "value": np.concatenate([healthy, broken]),
            "neighbour_median": np.concatenate([around, around]),
        }
    )
    scores = health_scores(frame, min_hours=500, floor=1.0).sort("pair_id")
    ok, bad = (
        scores.filter(pl.col("pair_id") == "ok").row(0, named=True),
        scores.filter(pl.col("pair_id") == "broken").row(0, named=True),
    )
    assert ok["health_change_corr"] > 0.8 > abs(bad["health_change_corr"])
    assert bad["health_log_offset"] > np.log(10)


def test_station_folds_keep_stations_whole_and_balanced():
    hours = pl.DataFrame(
        {"cv_group": [f"S{i}" for i in range(10)], "hours": [100, 90, 80, 70, 60, 50, 40, 30, 20, 10]}
    )
    folds = station_folds(hours, 3)
    assert folds["cv_group"].is_unique().all()
    load = folds.join(hours, on="cv_group").group_by("fold").agg(pl.col("hours").sum())["hours"]
    assert load.max() - load.min() <= 30


def test_duan_smearing_corrects_back_transform_bias():
    rng = np.random.default_rng(2)
    log_truth = rng.normal(2.0, 0.8, 200_000)
    prediction = np.full_like(log_truth, 2.0)  # perfect model of the log mean
    smearing = duan_smearing(pl.Series(log_truth - prediction))
    naive = float(np.exp(prediction).mean())
    corrected = pl.select(back_transform(pl.lit(2.0), smearing)).item()
    truth_mean = float(np.exp(log_truth).mean())
    assert naive < 0.8 * truth_mean  # exp(mean of logs) under-estimates the mean
    assert corrected == pytest.approx(truth_mean, rel=0.01)
