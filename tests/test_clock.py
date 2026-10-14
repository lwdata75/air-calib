import math
from datetime import datetime, timedelta

import polars as pl

from aircalib.transform.clock import clock_status, hourly_changes, lag_correlations

T0 = datetime(2024, 1, 1)


def _pair(pair_id, shift_h=0, n=600):
    """A slowly drifting signal plus sharp hourly bumps; the reference is shifted by ``shift_h`` hours."""

    def signal(i):
        return 20 + 10 * math.sin(i / 50) + (8 if i % 7 == 0 else 0) + (5 if i % 11 == 0 else 0)

    return pl.DataFrame(
        {
            "pair_id": [pair_id] * n,
            "hour": [T0 + timedelta(hours=i) for i in range(n)],
            "sensor": [signal(i) for i in range(n)],
            "reference": [signal(i - shift_h) for i in range(n)],
        }
    )


def test_aligned_pair_passes_and_shifted_pair_is_caught():
    hours = pl.concat([_pair("good"), _pair("late", shift_h=1)])
    status = clock_status(lag_correlations(hourly_changes(hours), 3), 100, 0.1, 3.0).sort("pair_id")
    assert status["best_lag_h"].to_list() == [0, 1]
    assert status["clock_flag"].to_list() == ["ok", "clock_mismatch"]


def test_too_few_hours_is_unverifiable():
    status = clock_status(lag_correlations(hourly_changes(_pair("short", n=50)), 3), 100, 0.1, 3.0)
    assert status["clock_flag"].item() == "clock_unverifiable"


def test_changes_are_not_computed_across_a_gap():
    hours = _pair("gap", n=5).filter(pl.col("hour") != T0 + timedelta(hours=2))
    changes = hourly_changes(hours)["sensor"].to_list()
    assert changes[0] is None and changes[2] is None  # first hour, and the hour after the hole
    assert changes[1] is not None


def test_noise_is_unverifiable_not_a_mismatch():
    noisy = _pair("noise").with_columns(reference=pl.Series([float((i * 7919) % 13) for i in range(600)]))
    status = clock_status(lag_correlations(hourly_changes(noisy), 3), 100, 0.1, 3.0)
    assert status["clock_flag"].item() == "clock_unverifiable"


def test_station_pooling_groups_pairs():
    hours = pl.concat([_pair("a", shift_h=1), _pair("b", shift_h=1)]).with_columns(station=pl.lit("S1"))
    station = clock_status(
        lag_correlations(hourly_changes(hours), 3, group="station"), 100, 0.1, 3.0, group="station"
    )
    assert station.select("station", "best_lag_h", "clock_flag").row(0) == ("S1", 1, "clock_mismatch")
