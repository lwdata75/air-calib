"""Station-wise cross-validation: train on the other folds, predict the held-out fold.

For fold ``k``: the physical features of *all* rows are recomputed with the
``a, kappa`` fitted without fold ``k``; the model is trained on the training rows
of the other folds and predicts every test row of fold ``k`` (near band, sane and
unverifiable pairs). The out-of-fold predictions of the 5 folds form one table.

The same machinery runs any list of train / test splits (``run_splits``): the step-7
tests (2024 -> 2025, distance bands, France) are splits with their own masks and
their own physical fit.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field

import polars as pl

from aircalib.config import Settings
from aircalib.models.data import TARGET, with_fold_physics

KEEP = ["pair_id", "hour", "fold", TARGET, "sane_pair", "has_neighbours", "station_type", "method_class"]
# Kept in the predictions when present, for the breakdowns of step 7.
OPTIONAL = ["country", "distance_band", "distance_km", "age_years", "official_humidity"]


@dataclass
class CrossValidation:
    """Out-of-fold predictions of one study and what it cost (fit and predict seconds, importances)."""

    oof: pl.DataFrame
    seconds_fit: list[float] = field(default_factory=list)
    seconds_predict: list[float] = field(default_factory=list)
    importance: pl.DataFrame | None = None
    model_params: dict = field(default_factory=dict)


@dataclass(frozen=True)
class Split:
    """One train / test split: row masks and the physical fit (``a, kappa``) its model uses."""

    train: pl.Expr
    test: pl.Expr
    gain: float
    kappa: float


def run_splits(
    make_model: Callable[[], object], data: pl.DataFrame, splits: list[Split], settings: Settings
) -> CrossValidation:
    """Train one model per split on its training rows and predict its test rows; empty splits are skipped."""
    parts, fit_s, pred_s, importances, params = [], [], [], [], {}
    for index, split in enumerate(splits):
        split_data = with_fold_physics(data, split.gain, split.kappa, settings)
        train = split_data.filter(split.train)
        test = split_data.filter(split.test)
        if train.is_empty() or test.is_empty():  # tiny samples (``--sample``) can leave a fold empty
            continue
        model = make_model()
        start = time.perf_counter()
        model.fit(train)
        fit_s.append(time.perf_counter() - start)
        start = time.perf_counter()
        predicted = model.predict(test)
        pred_s.append(time.perf_counter() - start)
        keep = KEEP + [c for c in OPTIONAL if c in test.columns]
        parts.append(
            test.select(keep).with_columns(**{k: pl.Series(v).cast(pl.Float32) for k, v in predicted.items()})
        )
        if getattr(model, "importance", None):
            importances.append(
                pl.DataFrame(
                    {"feature": list(model.importance), "gain": list(model.importance.values())}
                ).with_columns(split=pl.lit(index))
            )
        params = model.params()
    if not parts:
        raise ValueError("every split is empty")
    importance = (
        pl.concat(importances).group_by("feature").agg(pl.col("gain").mean()).sort("gain", descending=True)
        if importances
        else None
    )
    return CrossValidation(pl.concat(parts), fit_s, pred_s, importance, params)


def fold_splits(
    fits: pl.DataFrame, settings: Settings, *, train_mask: pl.Expr, test_mask: pl.Expr | None = None
) -> list[Split]:
    """The station-wise splits: train on the other folds, test on fold ``k``, physics fitted without it."""
    test_mask = pl.lit(True) if test_mask is None else test_mask
    splits = []
    for fold in range(settings.cv.n_folds):
        p = fits.filter(pl.col("fold") == fold).row(0, named=True)
        in_fold = pl.col("fold") == fold
        splits.append(Split(train_mask & ~in_fold, test_mask & in_fold, p["gain"], p["kappa"]))
    return splits


def cross_validate(
    make_model: Callable[[], object],
    data: pl.DataFrame,
    fits: pl.DataFrame,
    settings: Settings,
    *,
    train_mask: pl.Expr,
    test_mask: pl.Expr | None = None,
) -> CrossValidation:
    """Out-of-fold predictions. ``train_mask`` selects training rows (e.g. ``training_zone``)."""
    splits = fold_splits(fits, settings, train_mask=train_mask, test_mask=test_mask)
    return run_splits(make_model, data, splits, settings)
