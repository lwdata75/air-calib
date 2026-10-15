"""Split conformal calibration of a prediction interval, fold by fold.

The 10-90 % quantile interval of step 6 covers the reference only 62 % of the time
on stations it has never seen. Conformalised quantile regression (Romano et al.,
2019) widens it by the amount that makes it right on *other* stations:

* score of an hour: how far the reference falls outside the interval, in log units
  (the error is multiplicative, notebook 04): ``E = max(ln q_lo - ln y, ln y - ln q_hi)``,
  negative when it falls inside;
* for fold ``k``, ``Q_k`` is the ``(1 - alpha)`` empirical quantile of the scores of the
  **other** folds (finite-sample corrected), and the interval of fold ``k`` becomes
  ``[q_lo / e^Q_k, q_hi * e^Q_k]``.

No fold ever calibrates itself; optionally one ``Q`` per group (hours with / without
neighbours), since the error is not the same in both.
"""

from __future__ import annotations

import math

import numpy as np
import polars as pl

from aircalib.evaluation.metrics import REFERENCE


def scores(lower: pl.Expr, upper: pl.Expr, reference: pl.Expr, floor: float) -> pl.Expr:
    """Signed distance (log units) of the reference outside ``[lower, upper]``; values floored first."""
    ln = [pl.max_horizontal(e, pl.lit(floor)).log() for e in (lower, upper, reference)]
    return pl.max_horizontal(ln[0] - ln[2], ln[2] - ln[1])


def conformal_quantile(values: np.ndarray, coverage: float) -> float:
    """The ``k``-th smallest score, ``k = ceil((n + 1) * coverage)``: guaranteed coverage (exchangeable data).

    (Capped at the largest score when ``k > n``, which only happens for tiny calibration sets.)
    """
    n = len(values)
    if n == 0:
        return float("nan")
    k = min(n, math.ceil((n + 1) * coverage))
    return float(np.sort(values)[k - 1])


def calibrate_by_fold(
    oof: pl.DataFrame,
    lower: str,
    upper: str,
    coverage: float,
    floor: float,
    *,
    calibration_rows: pl.Expr,
    groups: list[str] | None = None,
) -> tuple[pl.DataFrame, pl.DataFrame]:
    """Widened interval (``lower_conf``, ``upper_conf``) for every row, and the widening per fold and group.

    ``calibration_rows`` selects the rows whose scores calibrate (e.g. sane pairs); the
    widening of fold ``k`` uses those rows of the other folds only.
    """
    groups = groups or []
    data = oof.with_columns(_score=scores(pl.col(lower), pl.col(upper), pl.col(REFERENCE), floor))
    keys = data.select("fold", *groups).unique().sort("fold", *groups)
    widenings = []
    for key in keys.iter_rows(named=True):
        same_group = pl.lit(True)
        for g in groups:
            same_group = same_group & (pl.col(g) == key[g])
        calib = data.filter(calibration_rows & same_group & (pl.col("fold") != key["fold"]))
        widenings.append({**key, "widening_log": conformal_quantile(calib["_score"].to_numpy(), coverage)})
    table = pl.DataFrame(widenings)
    out = (
        data.join(table, on=["fold", *groups], how="left")
        .with_columns(
            lower_conf=pl.col(lower) * (-pl.col("widening_log")).exp(),
            upper_conf=pl.col(upper) * pl.col("widening_log").exp(),
        )
        .drop("_score", "widening_log")
    )
    return out, table.with_columns(factor=pl.col("widening_log").exp())
