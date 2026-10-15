from datetime import datetime, timedelta
from typing import ClassVar

import numpy as np
import polars as pl
import pytest

from aircalib.config import get_settings
from aircalib.evaluation.conformal import calibrate_by_fold, conformal_quantile
from aircalib.evaluation.scoring import score_slices
from aircalib.models.cv import Split, run_splits
from aircalib.models.generalisation import slice_expressions

S = get_settings()
T0 = datetime(2024, 1, 1)


def test_conformal_widening_restores_coverage_on_unseen_folds():
    rng = np.random.default_rng(0)
    n = 20_000
    centre = rng.lognormal(2.0, 0.6, n)
    truth = centre * np.exp(rng.normal(0, 0.4, n))
    half = 0.2  # too narrow: the true 80 % half-width in log units is 1.28 * 0.4 = 0.51
    oof = pl.DataFrame(
        {
            "fold": rng.integers(0, 5, n),
            "reference": truth,
            "q10": centre * np.exp(-half),
            "q90": centre * np.exp(half),
            "sane_pair": True,
        }
    )
    out, widening = calibrate_by_fold(oof, "q10", "q90", 0.8, 1.0, calibration_rows=pl.col("sane_pair"))
    before = ((oof["reference"] >= oof["q10"]) & (oof["reference"] <= oof["q90"])).mean()
    inside = (pl.col("reference") >= pl.col("lower_conf")) & (pl.col("reference") <= pl.col("upper_conf"))
    after = out.group_by("fold").agg(inside.mean().alias("coverage"))["coverage"]
    assert before < 0.5
    assert all(0.77 < c < 0.84 for c in after)
    assert (widening["widening_log"] > 0).all()


def test_conformal_never_calibrates_a_fold_on_itself():
    # Fold 0 has huge errors, the others none: fold 0's widening must ignore its own scores.
    oof = pl.DataFrame(
        {
            "fold": [0] * 50 + [1] * 50 + [2] * 50,
            "reference": [100.0] * 50 + [10.0] * 100,
            "q10": [9.0] * 150,
            "q90": [11.0] * 150,
            "sane_pair": True,
        }
    )
    _, widening = calibrate_by_fold(oof, "q10", "q90", 0.8, 1.0, calibration_rows=pl.col("sane_pair"))
    assert widening.filter(pl.col("fold") == 0)["widening_log"][0] < 0  # others are well inside
    assert widening.filter(pl.col("fold") == 1)["widening_log"][0] > 0  # fold 0 is in its calibration set


def test_conformal_quantile_is_finite_sample_corrected():
    values = np.arange(1, 11, dtype=float)  # n = 10, coverage 0.8: k = ceil(11 * 0.8) = 9
    assert conformal_quantile(values, 0.8) == 9.0
    assert np.isnan(conformal_quantile(np.array([]), 0.8))


class _Mean:
    seen: ClassVar[list] = []

    def fit(self, train):
        _Mean.seen.append(sorted(train["country"].unique().to_list()))
        self.mean = float(train["reference"].mean())

    def predict(self, test):
        return {"prediction": np.full(test.height, self.mean)}

    def params(self):
        return {}


def test_transfer_split_trains_on_one_country_and_tests_on_the_other():
    rows = []
    for country, fold, value in (("DE", 0, 10.0), ("DE", 1, 12.0), ("FR", None, 30.0)):
        for h in range(10):
            rows.append(
                {
                    "pair_id": f"{country}{fold}",
                    "hour": T0 + timedelta(hours=h),
                    "fold": fold,
                    "country": country,
                    "reference": value,
                    "pm25_mean": value / 2,
                    "official_humidity": 50.0,
                    "sane_pair": True,
                    "has_neighbours": True,
                    "training_zone": country == "DE",
                    "station_type": "traffic",
                    "method_class": "optical",
                }
            )
    frame = pl.DataFrame(rows)
    _Mean.seen = []
    result = run_splits(
        _Mean, frame, [Split(pl.col("training_zone"), pl.col("country") == "FR", 2.4, 0.06)], S
    )
    assert _Mean.seen == [["DE"]]
    assert result.oof["country"].unique().to_list() == ["FR"]  # optional columns are kept
    assert result.oof["prediction"].to_list() == pytest.approx([11.0] * 10)


def test_slices_score_each_value_and_seasons_follow_the_config():
    hours = [datetime(2024, m, 1) + timedelta(hours=h) for m in (1, 7) for h in range(600)]
    rng = np.random.default_rng(1)
    ref = rng.lognormal(2, 0.5, len(hours))
    oof = pl.DataFrame(
        {
            "pair_id": "p",
            "hour": hours,
            "fold": 0,
            "reference": ref,
            "prediction": ref,
            "sane_pair": True,
            "has_neighbours": True,
        }
    )
    table = score_slices(oof, slice_expressions(S)["saison"], S.evaluation, "saison")
    hourly = table.filter(
        (pl.col("résolution") == "hourly") & (pl.col("paires") == "saines") & (pl.col("voisins") == "toutes")
    )
    assert sorted(hourly["saison"].to_list()) == ["ete", "hiver"]
    assert (hourly["pct_epa_all"] == 100.0).all()  # a perfect prediction passes, pair by pair, in each season
