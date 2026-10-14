"""Constant runs: a measuring instrument that repeats the exact same value for too long is stuck."""

from __future__ import annotations

import polars as pl


def in_constant_run(
    eligible: pl.Expr, values: list[str], *, by: str, time_col: str, min_hours: float
) -> pl.Expr:
    """True for eligible readings inside a run of identical ``values`` lasting at least ``min_hours``.

    The frame must be sorted by ``by`` then ``time_col``. A run is a sequence of
    consecutive eligible readings of one ``by`` group with identical values and no
    gap longer than ``min_hours`` between them; its duration is last minus first
    timestamp. A non-eligible reading always breaks a run.
    """
    t = pl.col(time_col)
    prev_eligible = eligible.shift().over(by)
    same_values = pl.all_horizontal([pl.col(c) == pl.col(c).shift().over(by) for c in values])
    close = (t - t.shift().over(by)) <= pl.duration(seconds=min_hours * 3600)
    continues = (eligible & prev_eligible & same_values & close).fill_null(False)
    run = (~continues).cum_sum()
    span = (t.max() - t.min()).over(run)
    return eligible & (span >= pl.duration(seconds=min_hours * 3600))
