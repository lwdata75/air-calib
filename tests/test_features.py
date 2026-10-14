from datetime import datetime, timedelta

import polars as pl
import pytest

from aircalib.config import get_settings
from aircalib.transform.features import (
    cyclic,
    dew_point,
    log_floor,
    missing_rate_past,
    rolling_past,
    time_features,
)

CFG = get_settings().features
T0 = datetime(2024, 3, 1, 0)


def _series(values, valid=None, start=T0, pair="p"):
    return pl.DataFrame(
        {
            "pair_id": [pair] * len(values),
            "hour": [start + timedelta(hours=i) for i in range(len(values))],
            "pm": values,
            "valid": valid if valid is not None else [True] * len(values),
        }
    )


def _dynamic(frame):
    return frame.sort("pair_id", "hour").with_columns(
        *rolling_past("pm", CFG.rolling_hours), *missing_rate_past("valid", CFG.missing_window_hours)
    )


def test_no_feature_uses_the_future():
    """Changing every value after hour t must leave all features at hours <= t unchanged."""
    base = _series([float(i % 7) for i in range(200)], valid=[i % 5 != 0 for i in range(200)])
    t = 120
    altered = base.with_columns(
        pm=pl.when(pl.int_range(pl.len()) > t).then(pl.lit(999.0)).otherwise(pl.col("pm")),
        valid=pl.when(pl.int_range(pl.len()) > t).then(pl.lit(False)).otherwise(pl.col("valid")),
    )
    before = _dynamic(base).head(t + 1)
    after = _dynamic(altered).head(t + 1)
    assert before.equals(after)
    # Sanity check: the alteration does change the future rows, so the test can fail.
    assert not _dynamic(base).tail(10).equals(_dynamic(altered).tail(10))


def test_rolling_window_includes_current_hour_and_stops_at_gaps():
    frame = _series([1.0, 2.0, 3.0, 4.0])
    gap = frame.filter(pl.col("hour") != T0 + timedelta(hours=2))  # hour 2 missing
    out = _dynamic(gap)
    # At hour 3 the 3-hour window (h-3, h] holds hours 1 and 3 only (hour 2 is absent, hour 0 is too old).
    assert out["pm_mean_3h"].to_list()[-1] == pytest.approx((2.0 + 4.0) / 2)


def test_missing_rate_counts_absent_and_invalid_hours_in_the_past():
    frame = _series([1.0] * 30, valid=[True] * 24 + [False] * 6)
    out = _dynamic(frame)
    assert out["missing_rate_24h"][24] == pytest.approx(0.0)  # hours 0-23 all valid
    assert out["missing_rate_24h"][29] == pytest.approx(5 / 24)  # hours 24-28 invalid, 29 not counted
    assert out["missing_rate_24h"][0] == pytest.approx(1.0)  # no history yet: everything missing


def test_cyclic_encoding_joins_the_ends():
    frame = pl.DataFrame({"h": [0.0, 23.0, 12.0]})
    out = frame.select(cyclic(pl.col("h"), 24, "hour"))
    distance_23_0 = abs(out["hour_sin"][1] - out["hour_sin"][0]) + abs(
        out["hour_cos"][1] - out["hour_cos"][0]
    )
    distance_12_0 = abs(out["hour_sin"][2] - out["hour_sin"][0]) + abs(
        out["hour_cos"][2] - out["hour_cos"][0]
    )
    assert distance_23_0 < distance_12_0


def test_time_features_use_local_time():
    frame = pl.DataFrame({"hour": [datetime(2024, 7, 1, 10)]})  # 10 h UTC = 12 h in Paris in summer
    out = frame.select(time_features(pl.col("hour"), "Europe/Paris"))
    noon = frame.select(cyclic(pl.lit(12), 24, "hour"))
    assert out["hour_sin"][0] == pytest.approx(noon["hour_sin"][0])


def test_dew_point_reference_values():
    frame = pl.DataFrame({"t": [20.0, 10.0], "rh": [100.0, 50.0]})
    out = frame.select(td=dew_point(pl.col("t"), pl.col("rh"), CFG))["td"].to_list()
    assert out[0] == pytest.approx(20.0, abs=0.01)  # saturated air: dew point = temperature
    assert out[1] == pytest.approx(0.06, abs=0.2)  # 10 C, 50 % -> about 0 C


def test_log_floor():
    frame = pl.DataFrame({"pm": [0.0, 0.3, 5.0, None]})
    out = frame.select(log_floor(pl.col("pm"), 1.0))["pm"].to_list()
    assert out[:3] == pytest.approx([0.0, 0.0, 1.6094379], abs=1e-6)
    assert out[3] is None
