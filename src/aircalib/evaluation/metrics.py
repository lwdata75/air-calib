"""Sensor-vs-reference metrics, the US EPA targets, and error breakdowns.

Conventions (US EPA, Duvall et al. 2021): the **sensor** is regressed on the
**reference** by ordinary least squares, ``sensor = slope × reference + intercept``;
``R²`` is the squared Pearson correlation; ``RMSE`` is the root mean square of
``sensor − reference`` (not of the regression residuals). Every function is pure
and works on any grouping (pair, station type, humidity bin...).
"""

from __future__ import annotations

import itertools

import polars as pl

from aircalib.config import Evaluation

SENSOR, REFERENCE = "sensor", "reference"


def regression_metrics(frame: pl.DataFrame | pl.LazyFrame, by: list[str]) -> pl.DataFrame:
    """Per group: n, means, OLS slope and intercept of sensor on reference, R², RMSE, bias, NRMSE."""
    x, y = pl.col(REFERENCE), pl.col(SENSOR)
    slope = pl.cov(x, y) / x.var()
    return (
        frame.lazy()
        .drop_nulls([SENSOR, REFERENCE])
        .group_by(by)
        .agg(
            n=pl.len(),
            mean_reference=x.mean(),
            mean_sensor=y.mean(),
            slope=slope,
            intercept=y.mean() - slope * x.mean(),
            r2=pl.corr(x, y) ** 2,
            rmse=((y - x) ** 2).mean().sqrt(),
            bias=(y - x).mean(),
        )
        .with_columns(nrmse=pl.col("rmse") / pl.col("mean_reference"))
        .sort(by)
        .collect()
    )


def epa_targets(metrics: pl.DataFrame, cfg: Evaluation) -> pl.DataFrame:
    """Add one boolean per EPA target and ``epa_all`` (all four met)."""
    lo_s, hi_s = cfg.epa_slope_range
    lo_i, hi_i = cfg.epa_intercept_range
    return metrics.with_columns(
        epa_r2=pl.col("r2") >= cfg.epa_r2_min,
        epa_slope=pl.col("slope").is_between(lo_s, hi_s),
        epa_intercept=pl.col("intercept").is_between(lo_i, hi_i),
        epa_rmse=pl.col("rmse") <= cfg.epa_rmse_max,
    ).with_columns(epa_all=pl.all_horizontal("epa_r2", "epa_slope", "epa_intercept", "epa_rmse"))


def daily_means(hours: pl.DataFrame | pl.LazyFrame, by: list[str], min_hours: int) -> pl.DataFrame:
    """Daily (UTC) means of sensor and reference over hours valid on both sides; days with fewer
    than ``min_hours`` such hours are dropped (the 75 % rule of daily averages)."""
    return (
        hours.lazy()
        .drop_nulls([SENSOR, REFERENCE])
        .group_by(*by, day=pl.col("hour").dt.date())
        .agg(pl.col(SENSOR).mean(), pl.col(REFERENCE).mean(), hours=pl.len())
        .filter(pl.col("hours") >= min_hours)
        .collect()
    )


def bin_label(value: pl.Expr, edges: list[float], unit: str = "") -> pl.Expr:
    """Readable bin label for ``value`` (e.g. ``< 50``, ``50–60``, ``≥ 95``), prefixed for sorting."""
    expr = pl.when(value < edges[0]).then(pl.lit(f"0. < {edges[0]:g}{unit}"))
    for i, (low, high) in enumerate(itertools.pairwise(edges), 1):
        expr = expr.when(value < high).then(pl.lit(f"{i}. {low:g}–{high:g}{unit}"))
    return expr.when(value >= edges[-1]).then(pl.lit(f"{len(edges)}. ≥ {edges[-1]:g}{unit}"))


def error_profile(frame: pl.DataFrame | pl.LazyFrame, by: list[str]) -> pl.DataFrame:
    """Per group: how wrong is the raw sensor? Median ratio and error, MAE, RMSE, spread of the log ratio.

    The log ratio ``ln(sensor / reference)`` is computed on hours where both are
    above 1 ug/m3 (a ratio of two near-zero values means nothing).
    """
    x, y = pl.col(REFERENCE), pl.col(SENSOR)
    positive = (x > 1) & (y > 1)
    log_ratio = (y / x).log().filter(positive)
    return (
        frame.lazy()
        .drop_nulls([SENSOR, REFERENCE])
        .group_by(by)
        .agg(
            hours=pl.len(),
            pairs=pl.col("pair_id").n_unique(),
            reference_median=x.median(),
            ratio_median=(y / x).filter(positive).median(),
            error_median=(y - x).median(),
            mae=(y - x).abs().mean(),
            rmse=((y - x) ** 2).mean().sqrt(),
            log_ratio_std=log_ratio.std(),
        )
        .sort(by)
        .collect()
    )


def duan_smearing(log_residuals: pl.Series) -> float:
    """Duan's smearing factor ``mean(exp(residual))`` for a model fitted on ``ln`` values.

    ``exp(prediction)`` estimates the *median* of the target, not its mean: when the
    errors are spread in log space, it under-estimates the mean (``E[exp(e)] > 1``).
    Multiplying by this factor, computed on the **training** residuals, removes that
    bias without assuming normal errors (Duan, 1983).
    """
    return float(log_residuals.drop_nulls().exp().mean())


def back_transform(log_prediction: pl.Expr, smearing: float) -> pl.Expr:
    """Prediction in ug/m3 from a log-scale prediction (EPA metrics are always computed in ug/m3)."""
    return log_prediction.exp() * smearing
