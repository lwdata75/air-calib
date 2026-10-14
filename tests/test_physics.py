import math

import polars as pl
import pytest

from aircalib.config import get_settings
from aircalib.transform.physics import kohler_dry_pm, kohler_growth_factor


def _factor(rh_values):
    s = get_settings()
    frame = pl.DataFrame({"rh": rh_values}, schema={"rh": pl.Float64})
    return frame.select(c=kohler_growth_factor(pl.col("rh"), s.physics, s.humidity.saturation_pct))["c"]


def test_growth_factor_matches_formula_at_moderate_humidity():
    s = get_settings().physics
    expected = 1 + (s.kappa / s.density_ratio) / (1 / 0.6 - 1)
    assert _factor([60.0])[0] == pytest.approx(expected)


def test_growth_factor_increases_with_humidity_and_stays_finite():
    values = _factor([20.0, 50.0, 80.0, 94.0]).to_list()
    assert values == sorted(values)
    assert all(v >= 1 and math.isfinite(v) for v in values)


def test_correction_never_applied_to_saturated_or_too_humid_air():
    # 100 % would divide by zero; 99.9 % is a saturated probe; 97 % is above rh_max_pct.
    assert _factor([100.0, 99.95, 99.9, 97.0]).to_list() == [None, None, None, None]


def test_correction_undefined_for_missing_or_impossible_humidity():
    # NaN is a real BME280 output; Polars orders NaN above every number, so it must be caught explicitly.
    assert _factor([None, 0.0, -5.0, float("nan"), 655.35]).to_list() == [None] * 5


def test_dry_pm_is_smaller_than_wet_pm_and_null_when_saturated():
    s = get_settings()
    frame = pl.DataFrame({"pm": [20.0, 20.0], "rh": [85.0, 100.0]})
    out = frame.select(dry=kohler_dry_pm(pl.col("pm"), pl.col("rh"), s.physics, s.humidity.saturation_pct))
    assert 0 < out["dry"][0] < 20.0
    assert out["dry"][1] is None
