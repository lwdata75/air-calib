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
    """Rename ``prediction`` to the metrics' ``sensor`` column."""
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


def summary_rows(oof: pl.DataFrame, cfg: Evaluation) -> list[dict]:
    """One row per resolution (hourly, daily) × pairs (sane, all) × hours (all, with, without neighbours)."""
    data = _as_sensor(oof)
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
                rows.append(
                    {
                        "résolution": resolution,
                        "paires": subset,
                        "voisins": neighbours or "toutes",
                        "n": float(pooled["n"]),
                        **{m: pooled[m] for m in METRICS},
                        **pair_pass_rate(frame, cfg, min_n),
                    }
                )
    return rows


def score(oof: pl.DataFrame, cfg: Evaluation) -> tuple[dict[str, float], dict[str, pl.DataFrame]]:
    """Flat metric dict (for MLflow) and readable tables.

    ``oof`` needs ``pair_id, hour, fold, reference, prediction, sane_pair, has_neighbours``.
    Every figure exists for sane pairs and all pairs, and for all hours, the hours *with*
    citizen neighbours and the hours *without* (a pair's EPA targets are then computed on
    its hours of that kind, if it has enough of them). Names:
    ``<resolution>_<subset>[_<neighbours>]_<metric>``, e.g. ``hourly_saines_rmse``,
    ``daily_toutes_sans_voisins_pct_epa_all``.
    """
    flat: dict[str, float] = {}
    rows = summary_rows(oof, cfg)
    for row in rows:
        nb = "" if row["voisins"] == "toutes" else row["voisins"]
        prefix = (
            f"{row['résolution']}_{row['paires']}_{nb}_" if nb else f"{row['résolution']}_{row['paires']}_"
        )
        for key, value in row.items():
            if isinstance(value, float):
                flat[prefix + key] = value
    tables = {"summary": pl.DataFrame(rows)}
    data = _as_sensor(oof)
    per_fold = overall(data.filter(pl.col("sane_pair")), ["fold"])
    tables["per_fold"] = per_fold
    for rec in per_fold.iter_rows(named=True):
        for m in METRICS:
            flat[f"fold{rec['fold']}_hourly_saines_{m}"] = rec[m]
    return flat, tables


def score_slices(oof: pl.DataFrame, by: pl.Expr, cfg: Evaluation, name: str = "tranche") -> pl.DataFrame:
    """``summary_rows`` for each value of ``by`` (distance band, season, station type...).

    A pair's EPA targets are computed on its hours of the slice (if it has at least
    ``min_hours`` of them, or ``min_days`` complete days): the % of pairs passing is
    therefore a property of the pair *in these conditions*.
    """
    data = oof.with_columns(by.alias(name))
    tables = []
    for value in data[name].drop_nulls().unique().sort().to_list():
        rows = summary_rows(data.filter(pl.col(name) == value), cfg)
        if rows:
            tables.append(pl.DataFrame(rows).with_columns(pl.lit(value).alias(name)))
    if not tables:
        return pl.DataFrame()
    return pl.concat(tables, how="diagonal_relaxed").select(name, pl.exclude(name))


def pair_distribution(oof: pl.DataFrame, cfg: Evaluation) -> dict[str, float]:
    """Distribution over pairs of the per-pair hourly R², slope and RMSE (pairs with ``min_hours``).

    The pooled R² of ``score`` mixes hundreds of sites in one regression; the median
    per-pair R² is the figure comparable with single-site studies.
    """
    per_pair = regression_metrics(_as_sensor(oof), ["pair_id"]).filter(pl.col("n") >= cfg.min_hours)
    out: dict[str, float] = {"pairs": float(per_pair.height)}
    for metric in ("r2", "slope", "rmse"):
        for name, q in (("q25", 0.25), ("median", 0.5), ("q75", 0.75)):
            out[f"{metric}_{name}"] = float(per_pair[metric].quantile(q)) if per_pair.height else float("nan")
    return out


def interval_coverage(oof: pl.DataFrame, lower: str, upper: str) -> dict[str, float]:
    """Share of hours whose reference falls in ``[lower, upper]`` and the median relative width."""
    inside = (pl.col(REFERENCE) >= pl.col(lower)) & (pl.col(REFERENCE) <= pl.col(upper))
    out = oof.select(
        coverage=inside.mean(),
        width_median=(pl.col(upper) - pl.col(lower)).median(),
    ).row(0, named=True)
    return {k: float(v) for k, v in out.items()}
