"""Constant runs: a measuring instrument that repeats the exact same value for too long is stuck."""

from __future__ import annotations

import polars as pl


def run_id(eligible: pl.Expr, values: list[str], *, by: str, time_col: str, max_gap_hours: float) -> pl.Expr:
    """Identifier of runs of identical ``values`` among consecutive eligible readings.

    The frame must be sorted by ``by`` then ``time_col``. A run continues while the
    reading is eligible, the previous one too, the values are identical and the gap
    is at most ``max_gap_hours``. A non-eligible reading is a run of its own.
    """
    t = pl.col(time_col)
    prev_eligible = eligible.shift().over(by)
    same_values = pl.all_horizontal([pl.col(c) == pl.col(c).shift().over(by) for c in values])
    close = (t - t.shift().over(by)) <= pl.duration(seconds=max_gap_hours * 3600)
    continues = (eligible & prev_eligible & same_values & close).fill_null(False)
    return (~continues).cum_sum()


def in_constant_run(
    eligible: pl.Expr, values: list[str], *, by: str, time_col: str, min_hours: float
) -> pl.Expr:
    """True for eligible readings inside a run of identical ``values`` lasting at least ``min_hours``.

    Duration = last minus first timestamp of the run; runs break on gaps longer than ``min_hours``.
    """
    t = pl.col(time_col)
    run = run_id(eligible, values, by=by, time_col=time_col, max_gap_hours=min_hours)
    span = (t.max() - t.min()).over(run)
    return eligible & (span >= pl.duration(seconds=min_hours * 3600))
