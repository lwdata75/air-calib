from datetime import datetime, timedelta

import polars as pl
import pytest

from aircalib.config import get_settings
from aircalib.transform import particles as pt
from aircalib.transform.positions import assign_segments, segment_table
from aircalib.transform.runs import in_constant_run

CFG = get_settings().particles
T0 = datetime(2024, 1, 1, 0, 0)


def _readings(values, *, step_s=150, sensor_id=1, start=T0):
    """Readings every ``step_s`` seconds; ``values`` = list of (P1, P2) or (P1, P2, seconds offset)."""
    rows = []
    for i, v in enumerate(values):
        offset = v[2] if len(v) == 3 else i * step_s
        rows.append(
            {"sensor_id": sensor_id, "timestamp": start + timedelta(seconds=offset), "P1": v[0], "P2": v[1]}
        )
    return pl.DataFrame(
        rows,
        schema={"sensor_id": pl.Int64, "timestamp": pl.Datetime("us"), "P1": pl.Float64, "P2": pl.Float64},
    )


def _flags(df):
    return pt.flag_readings(df, CFG)["quality_flag"].to_list()


def test_physical_and_coherence_rules_in_order():
    df = _readings(
        [
            (10.0, 5.0),
            (None, 5.0),  # missing
            (float("nan"), 5.0),  # NaN is missing too
            (-1.0, -1.0),  # firmware error code
            (1999.9, 999.9),  # saturation signature
            (1017.2, 700.0),  # PM10 beyond the SDS011 range
            (4.0, 6.0),  # PM2.5 > PM10
            (0.0, 0.0),  # zero is a legal value
            (999.9, 999.8),  # top of the range is still in range
        ]
    )
    assert _flags(df) == [
        "ok",
        "pm_missing",
        "pm_missing",
        "pm_negative",
        "pm_saturated",
        "pm_above_range",
        "pm25_above_pm10",
        "ok",
        "ok",
    ]


def test_no_statistical_outlier_filter():
    # A huge but physically possible peak in the middle of clean air stays ok.
    df = _readings([(5.0, 3.0), (6.0, 4.0), (850.0, 600.0), (5.0, 3.0)])
    assert _flags(df) == ["ok"] * 4


def test_duplicates_identical_keep_first_conflicts_flag_all():
    df = _readings(
        [
            (10.0, 5.0, 0),
            (10.0, 5.0, 0),  # identical copy
            (12.0, 6.0, 150),
            (13.0, 6.0, 150),  # same time, other values
            (None, None, 300),  # empty twin seen in the archive: it is "missing", not a duplicate
            (11.0, 5.5, 300),
        ]
    )
    assert _flags(df) == ["ok", "duplicate", "duplicate_conflict", "duplicate_conflict", "pm_missing", "ok"]


def test_failure_code_plateau_needs_the_minimum_duration():
    one_hour = int(CFG.stuck_min_hours * 3600)
    stuck = [
        (0.0, 0.0, s) for s in range(0, one_hour + 1, 150)
    ]  # 0/0 (failure code) for the minimum duration
    short = [(3.1, 2.0, one_hour + 150 + s) for s in range(0, 1800, 150)]  # identical for 30 min only
    after = [(7.0, 4.0, one_hour + 3000)]
    flags = _flags(_readings(stuck + short + after))
    assert set(flags[: len(stuck)]) == {"pm_stuck"}
    assert set(flags[len(stuck) :]) == {"ok"}


def _plateau_with_reference(reference_values):
    """3.1 / 2.0 repeated for 2 hours, and the station's hourly PM2.5 over those hours."""
    readings = _readings([(3.1, 2.0, s) for s in range(0, 2 * 3600 + 1, 150)])
    reference = pl.DataFrame(
        {
            "sensor_id": [1] * len(reference_values),
            "hour": [T0 + timedelta(hours=h) for h in range(len(reference_values))],
            "reference": reference_values,
        }
    )
    return readings, reference


def test_repeated_small_value_in_calm_air_is_not_stuck():
    readings, reference = _plateau_with_reference([3.0, 3.5, 4.0])  # station barely moves
    assert set(pt.flag_readings(readings, CFG, reference)["quality_flag"]) == {"ok"}
    assert set(pt.flag_readings(readings, CFG)["quality_flag"]) == {
        "ok"
    }  # no reference: benefit of the doubt


def test_repeated_value_while_the_station_moves_is_stuck():
    readings, reference = _plateau_with_reference([3.0, 12.0, 6.0])  # +9 ug/m3 meanwhile
    assert set(pt.flag_readings(readings, CFG, reference)["quality_flag"]) == {"pm_stuck"}


def test_constant_run_is_broken_by_a_long_gap():
    df = _readings([(2.0, 1.0, 0), (2.0, 1.0, 600), (2.0, 1.0, 600 + 4 * 3600)])  # 4 h outage in between
    out = df.with_columns(
        s=in_constant_run(pl.lit(True), ["P1", "P2"], by="sensor_id", time_col="timestamp", min_hours=1.0)
    )
    assert out["s"].to_list() == [False, False, False]


def test_hourly_completeness_uses_each_sensor_cadence():
    fast = _readings([(4.0 + i % 3, 2.0) for i in range(60)], step_s=60, sensor_id=1)
    slow = _readings([(4.0 + i % 3, 2.0) for i in range(12)], step_s=300, sensor_id=2)
    half = _readings([(4.0 + i % 3, 2.0) for i in range(30)], step_s=60, sensor_id=3)
    flagged = pt.flag_readings(pl.concat([fast, slow, half]), CFG)
    hourly = pt.hourly_particles(flagged, CFG, ["sensor_id"]).collect().sort("sensor_id")
    assert hourly["cadence_s"].to_list() == [60.0, 300.0, 60.0]
    assert hourly["completeness"].to_list() == pytest.approx([1.0, 1.0, 0.5])
    assert hourly["hour_flag"].to_list() == ["ok", "ok", "incomplete_hour"]  # 12 readings can be a full hour


def test_hourly_statistics_only_use_valid_readings():
    df = _readings([(10.0, 4.0), (10.0, 6.0), (-1.0, -1.0), (12.0, 8.0)], step_s=900)
    hourly = pt.hourly_particles(pt.flag_readings(df, CFG), CFG, ["sensor_id"]).collect().row(0, named=True)
    assert (hourly["n_readings"], hourly["n_valid"]) == (4, 3)
    assert hourly["pm25_mean"] == pytest.approx(6.0)
    assert hourly["pm25_median"] == pytest.approx(6.0)
    assert hourly["pm25_std"] == pytest.approx(2.0)
    assert hourly["main_flag"] == "pm_negative"


def test_hour_without_valid_reading_says_why():
    df = _readings([(1999.9, 999.9), (1999.9, 999.9)], step_s=900)
    hourly = pt.hourly_particles(pt.flag_readings(df, CFG), CFG, ["sensor_id"]).collect().row(0, named=True)
    assert (hourly["hour_flag"], hourly["main_flag"]) == ("no_valid_reading", "pm_saturated")


def test_segments_follow_consecutive_positions():
    df = pl.DataFrame(
        {
            "sensor_id": [1, 1, 1, 1, 2],
            "timestamp": [T0 + timedelta(hours=h) for h in range(4)] + [T0],
            "location": [10, 10, 11, 10, 20],
            "lat": [50.0, 50.0, 50.2, 50.0, 48.0],
            "lon": [8.0, 8.0, 8.1, 8.0, 2.0],
        }
    )
    out = assign_segments(df)
    assert out["segment"].to_list() == [1, 1, 2, 3, 1]  # going back opens a new stay
    table = segment_table(out).collect()
    assert table.filter(pl.col("sensor_id") == 1)["n_readings"].to_list() == [2, 1, 1]
