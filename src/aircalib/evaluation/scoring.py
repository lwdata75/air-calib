"""Scores of out-of-fold predictions: EPA metrics, hourly and daily, sane pairs and all pairs.

Convention (EPA): the corrected prediction is regressed on the reference
(``y = prediction``, ``x = reference``). A model trained to minimise the squared
error shrinks its predictions toward the mean: regressed on the reference, its
slope is close to its R² (``slope ≈ R²`` when the model is well calibrated in the
sense ``E[reference | prediction] = prediction``). This is why the slope is the
hardest EPA target for a regression model.
"""

from __future__ import annotations

import polars as pl

from aircalib.config import Evaluation
from aircalib.evaluation.metrics import REFERENCE, SENSOR, daily_means, epa_targets, regression_metrics

SUBSETS = {"saines": pl.col("sane_pair"), "toutes": pl.lit(True)}
NEIGHBOURS = {
    "": pl.lit(True),
    "avec_voisins": pl.col("has_neighbours"),
    "sans_voisins": ~pl.col("has_neighbours"),
}
METRICS = ("rmse", "mae", "r2", "slope", "intercept", "bias")


def _as_sensor(oof: pl.DataFrame) -> pl.DataFrame:
    return oof.rename({"prediction": SENSOR}) if "prediction" in oof.columns else oof


def overall(frame: pl.DataFrame, by: list[str] | None = None) -> pl.DataFrame:
    """Pooled metrics over all hours (or days) of the frame, optionally per group."""
    by = by or []
    data = frame.with_columns(_all=pl.lit(1)) if not by else frame
    return regression_metrics(data, by or ["_all"]).drop("_all", strict=False)


def pair_pass_rate(frame: pl.DataFrame, cfg: Evaluation, min_n: int) -> dict[str, float]:
    """Share of pairs meeting each EPA target (pairs with at least ``min_n`` hours or days)."""
    per_pair = epa_targets(regression_metrics(frame, ["pair_id"]), cfg).filter(pl.col("n") >= min_n)
    return {
        "pairs": float(per_pair.height),
        **{
            f"pct_{c}": 100 * float(per_pair[c].mean()) if per_pair.height else float("nan")
            for c in ("epa_r2", "epa_slope", "epa_intercept", "epa_rmse", "epa_all")
        },
    }


def score(oof: pl.DataFrame, cfg: Evaluation) -> tuple[dict[str, float], dict[str, pl.DataFrame]]:
    """Flat metric dict (for MLflow) and readable tables.

    ``oof`` needs ``pair_id, hour, fold, reference, prediction, sane_pair, has_neighbours``.
    Every figure exists for sane pairs and all pairs, and for all hours, the hours *with*
    citizen neighbours and the hours *without* (a pair's EPA targets are then computed on
    its hours of that kind, if it has enough of them). Names:
    ``<resolution>_<subset>[_<neighbours>]_<metric>``, e.g. ``hourly_saines_rmse``,
    ``daily_toutes_sans_voisins_pct_epa_all``.
    """
    data = _as_sensor(oof)
    flat: dict[str, float] = {}
    rows = []
    for subset, mask in SUBSETS.items():
        for neighbours, nb_mask in NEIGHBOURS.items():
            part = data.filter(mask & nb_mask)
            if part.is_empty():
                continue
            days = daily_means(part, ["pair_id"], cfg.daily_min_hours)
            for resolution, frame, min_n in (("hourly", part, cfg.min_hours), ("daily", days, cfg.min_days)):
                if frame.is_empty():
                    continue
                pooled = overall(frame).row(0, named=True)
                rates = pair_pass_rate(frame, cfg, min_n)
                row = {
                    "résolution": resolution,
                    "paires": subset,
                    "voisins": neighbours or "toutes",
                    "n": float(pooled["n"]),
                    **{m: pooled[m] for m in METRICS},
                    **rates,
                }
                rows.append(row)
                prefix = f"{resolution}_{subset}_{neighbours}_" if neighbours else f"{resolution}_{subset}_"
                for key, value in row.items():
                    if isinstance(value, float):
                        flat[prefix + key] = value
    tables = {"summary": pl.DataFrame(rows)}
    per_fold = overall(data.filter(pl.col("sane_pair")), ["fold"])
    tables["per_fold"] = per_fold
    for rec in per_fold.iter_rows(named=True):
        for m in METRICS:
            flat[f"fold{rec['fold']}_hourly_saines_{m}"] = rec[m]
    return flat, tables


def interval_coverage(oof: pl.DataFrame, lower: str, upper: str) -> dict[str, float]:
    """Share of hours whose reference falls in ``[lower, upper]`` and the median relative width."""
    inside = (pl.col(REFERENCE) >= pl.col(lower)) & (pl.col(REFERENCE) <= pl.col(upper))
    out = oof.select(
        coverage=inside.mean(),
        width_median=(pl.col(upper) - pl.col(lower)).median(),
    ).row(0, named=True)
    return {k: float(v) for k, v in out.items()}
