"""Station-wise cross-validation: train on the other folds, predict the held-out fold.

For fold ``k``: the physical features of *all* rows are recomputed with the
``a, kappa`` fitted without fold ``k``; the model is trained on the training rows
of the other folds and predicts every test row of fold ``k`` (near band, sane and
unverifiable pairs). The out-of-fold predictions of the 5 folds form one table.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field

import polars as pl

from aircalib.config import Settings
from aircalib.models.data import TARGET, with_fold_physics

KEEP = ["pair_id", "hour", "fold", TARGET, "sane_pair", "has_neighbours", "station_type", "method_class"]


@dataclass
class CrossValidation:
    oof: pl.DataFrame
    seconds_fit: list[float] = field(default_factory=list)
    seconds_predict: list[float] = field(default_factory=list)
    importance: pl.DataFrame | None = None
    model_params: dict = field(default_factory=dict)


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
    test_mask = pl.lit(True) if test_mask is None else test_mask
    parts, fit_s, pred_s, importances, params = [], [], [], [], {}
    for fold in range(settings.cv.n_folds):
        p = fits.filter(pl.col("fold") == fold).row(0, named=True)
        fold_data = with_fold_physics(data, p["gain"], p["kappa"], settings)
        train = fold_data.filter(train_mask & (pl.col("fold") != fold))
        test = fold_data.filter(test_mask & (pl.col("fold") == fold))
        if train.is_empty() or test.is_empty():  # tiny samples (``--sample``) can leave a fold empty
            continue
        model = make_model()
        start = time.perf_counter()
        model.fit(train)
        fit_s.append(time.perf_counter() - start)
        start = time.perf_counter()
        predicted = model.predict(test)
        pred_s.append(time.perf_counter() - start)
        parts.append(
            test.select(KEEP).with_columns(**{k: pl.Series(v).cast(pl.Float32) for k, v in predicted.items()})
        )
        if getattr(model, "importance", None):
            importances.append(
                pl.DataFrame(
                    {"feature": list(model.importance), "gain": list(model.importance.values())}
                ).with_columns(fold=pl.lit(fold))
            )
        params = model.params()
    importance = (
        pl.concat(importances).group_by("feature").agg(pl.col("gain").mean()).sort("gain", descending=True)
        if importances
        else None
    )
    return CrossValidation(pl.concat(parts), fit_s, pred_s, importance, params)
