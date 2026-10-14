import math

import numpy as np
import polars as pl
import pytest

from aircalib.config import get_settings
from aircalib.transform.physics import fit_gain_kappa, growth_factor, physical_estimate

CFG = get_settings().physics


def _factor(rh_values, kappa=0.3):
    frame = pl.DataFrame({"rh": rh_values}, schema={"rh": pl.Float64})
    return frame.select(c=growth_factor(pl.col("rh"), kappa, CFG))["c"]


def test_growth_factor_matches_formula_at_moderate_humidity():
    expected = 1 + (0.3 / CFG.density_ratio) / (1 / 0.6 - 1)
    assert _factor([60.0])[0] == pytest.approx(expected)


def test_growth_factor_increases_with_humidity_and_stays_finite_near_saturation():
    values = _factor([20.0, 50.0, 80.0, 94.0, 99.0, 99.99, 100.0]).to_list()
    assert values == sorted(values)
    assert all(v >= 1 and math.isfinite(v) for v in values)
    # Above the cap the factor no longer grows: 100 % (fog at an official station) gives C(cap), not infinity.
    capped = _factor([CFG.rh_cap_pct])[0]
    assert values[-1] == pytest.approx(capped)
    assert values[-2] == pytest.approx(capped)


def test_growth_factor_undefined_for_missing_or_impossible_humidity():
    # NaN is a real BME280 output; Polars orders NaN above every number, so it must be caught explicitly.
    assert _factor([None, 0.0, -5.0, float("nan")]).to_list() == [None] * 4


def test_no_growth_without_hygroscopicity():
    assert _factor([30.0, 90.0, 100.0], kappa=0.0).to_list() == [1.0, 1.0, 1.0]


def test_physical_estimate_applies_gain_then_removes_water():
    frame = pl.DataFrame({"pm": [10.0, 10.0], "rh": [0.0001, 90.0]})
    out = frame.select(est=physical_estimate(pl.col("pm"), pl.col("rh"), 2.0, 0.3, CFG))["est"].to_list()
    assert out[0] == pytest.approx(20.0, rel=1e-3)  # dry: gain only
    assert out[1] < out[0]  # humid: the water-swollen reading is reduced


def test_fit_recovers_known_gain_and_kappa():
    rng = np.random.default_rng(0)
    rh = rng.uniform(20, 99.5, 20_000)
    ref = rng.lognormal(2.0, 0.6, rh.size)
    terms = np.minimum(rh, CFG.rh_cap_pct) / 100
    growth = 1 + (0.12 / CFG.density_ratio) * terms / (1 - terms)
    raw = ref * growth / 2.4 * rng.lognormal(0, 0.05, rh.size)  # sensor = ref * C / a, small noise
    fit = fit_gain_kappa(np.log(raw), np.log(ref), rh, CFG)
    assert fit.gain == pytest.approx(2.4, rel=0.01)
    assert fit.kappa == pytest.approx(0.12, abs=0.005)
    assert fit.mse_log < fit.mse_log_no_growth
