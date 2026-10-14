from datetime import datetime, timedelta
from typing import ClassVar

import numpy as np
import polars as pl
import pytest

from aircalib.config import get_settings
from aircalib.evaluation.health import best_threshold, confusion, threshold_cv
from aircalib.evaluation.scoring import score
from aircalib.models import data as mdata
from aircalib.models.cv import cross_validate
from aircalib.models.ladder import LogLinear, Recalibrated

S = get_settings()
T0 = datetime(2024, 1, 1)


def _frame(n_pairs=10, hours=60, seed=0):
    """Synthetic gold-like rows: 2 pairs per station, one station per fold pair."""
    rng = np.random.default_rng(seed)
    rows = []
    for p in range(n_pairs):
        ref = rng.lognormal(2.0, 0.5, hours)
        rh = rng.uniform(30, 99, hours)
        raw = ref * 0.42 * (1 + 0.04 * rh / (100 - rh)) * rng.lognormal(0, 0.1, hours)
        for h in range(hours):
            rows.append(
                {
                    "pair_id": f"p{p}",
                    "hour": T0 + timedelta(hours=h),
                    "fold": p % 5,
                    "cv_group": f"S{p // 2}",
                    "reference": ref[h],
                    "pm25_mean": raw[h],
                    "official_humidity": rh[h],
                    "official_temperature": 10.0,
                    "sane_pair": p != 0,
                    "has_neighbours": h % 2 == 0,
                    "training_zone": p != 0,
                    "station_type": "traffic",
                    "method_class": "optical",
                }
            )
    return pl.DataFrame(rows).with_columns(
        log_reference=pl.col("reference").log(), log_pm25=pl.col("pm25_mean").log()
    )


def _fits():
    return pl.DataFrame({"fold": list(range(5)), "gain": [2.4] * 5, "kappa": [0.06] * 5})


class _Probe:
    """A model that remembers what it was trained on and predicts the physical estimate."""

    seen: ClassVar[list] = []

    def fit(self, train):
        _Probe.seen.append((set(train["fold"].unique()), set(train["pair_id"].unique())))

    def predict(self, test):
        return {"prediction": test["physics_pm25"].to_numpy()}

    def params(self):
        return {}


def test_cross_validation_never_trains_on_the_test_fold_and_recomputes_physics():
    _Probe.seen = []
    frame = _frame()
    result = cross_validate(_Probe, frame, _fits(), S, train_mask=pl.col("training_zone"))
    for fold, (train_folds, train_pairs) in enumerate(_Probe.seen):
        assert fold not in train_folds
        test_pairs = set(result.oof.filter(pl.col("fold") == fold)["pair_id"])
        assert not train_pairs & test_pairs
    assert "p0" not in {p for _, pairs in _Probe.seen for p in pairs}  # unverifiable pair never trains
    assert result.oof.filter(pl.col("pair_id") == "p0").height > 0  # but is evaluated
    # Physics recomputed with the fold's parameters: a * PM / C(RH).
    row = result.oof.join(frame, on=["pair_id", "hour"]).row(0, named=True)
    aw = min(row["official_humidity"], S.physics.rh_cap_pct) / 100
    expected = 2.4 * row["pm25_mean"] / (1 + 0.06 / S.physics.density_ratio / (1 / aw - 1))
    assert row["prediction"] == pytest.approx(expected, rel=1e-4)


def test_log_linear_recovers_a_power_law():
    frame = _frame(seed=1)
    model = LogLinear(["log_pm25"])
    model.fit(frame)
    # A noisy predictor gives a slope below 1 (regression dilution), the same effect as in notebook 05.
    assert 0.6 < model.coef[1] < 1.0
    assert model.smearing > 1.0
    predicted = model.predict(frame)["prediction"]
    truth = frame["reference"].to_numpy()
    assert np.mean(np.abs(np.log(predicted / truth))) < np.mean(
        np.abs(np.log(frame["pm25_mean"].to_numpy() / truth))
    )


class _Shrunk:
    """Predicts a shrunken version of the truth (what a squared-error model does)."""

    def fit(self, train):
        self.mean = float(train["reference"].mean())

    def predict(self, test):
        return {"prediction": self.mean + 0.6 * (test["reference"].to_numpy() - self.mean)}

    def params(self):
        return {}


def test_recalibration_restores_a_unit_slope():
    frame = _frame(seed=2)
    model = Recalibrated(_Shrunk)
    model.fit(frame)
    assert model.slope == pytest.approx(0.6, abs=0.05)
    out = model.predict(frame)
    slope = np.polyfit(frame["reference"].to_numpy(), out["prediction"], 1)[0]
    assert slope == pytest.approx(1.0, abs=0.05)


def test_epa_slope_regresses_prediction_on_reference():
    frame = _frame(seed=3).with_columns(prediction=pl.col("reference") * 0.5 + 2)
    flat, _ = score(frame, S.evaluation)
    assert flat["hourly_toutes_slope"] == pytest.approx(0.5, abs=1e-6)  # y = prediction, x = reference
    assert flat["hourly_toutes_intercept"] == pytest.approx(2.0, abs=1e-5)
    assert "hourly_saines_avec_voisins_rmse" in flat and "hourly_saines_sans_voisins_rmse" in flat


def test_health_threshold_is_chosen_without_the_test_fold():
    rng = np.random.default_rng(4)
    n = 400
    broken = rng.random(n) < 0.2
    score_ = np.where(broken, rng.normal(0.05, 0.05, n), rng.normal(0.6, 0.15, n))
    pairs = pl.DataFrame(
        {
            "fold": rng.integers(0, 5, n),
            "country": np.where(rng.random(n) < 0.8, "DE", "FR"),
            "sane_pair": ~broken,
            "health_change_corr": score_,
        }
    ).with_columns(fold=pl.when(pl.col("country") == "DE").then("fold"))
    per_fold, transfer = threshold_cv(pairs, np.linspace(0, 0.5, 51), "DE")
    assert per_fold.height == 5
    assert (per_fold["precision"] > 0.8).all()
    assert transfer["recall"] > 0.8
    assert best_threshold(score_, broken, np.array([0.0, 0.2, 0.9])) == 0.2
    assert confusion(score_, broken, 0.0)["flagged"] == int((score_ < 0).sum())


def test_subsample_is_deterministic():
    frame = _frame(n_pairs=30)
    a = mdata.subsample_pairs(frame, 0.2, 42)
    b = mdata.subsample_pairs(frame, 0.2, 42)
    assert a == b and len(a) == 6


def test_matrix_codes_categories_and_keeps_nan():
    frame = pl.DataFrame({"x": [1.0, None], "station_type": ["traffic", "background"]})
    cats = mdata.categories(frame)
    m = mdata.matrix(frame, ["x", "station_type"], cats)
    assert np.isnan(m[1, 0])
    assert m[:, 1].tolist() == [1.0, 0.0]  # sorted categories: background = 0, traffic = 1


def test_optuna_space_tunes_known_lightgbm_parameters():
    space = S.models.optuna_space
    assert set(space) - {"num_boost_round"} <= set(S.models.lightgbm)
    assert {name for name, r in space.items() if r.integer} == {
        "num_leaves",
        "min_data_in_leaf",
        "num_boost_round",
    }
    assert all(r.low < r.high and (not r.log or r.low > 0) for r in space.values())
