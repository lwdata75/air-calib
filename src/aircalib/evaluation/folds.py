"""Station-wise cross-validation folds (GroupKFold).

A station and **all** its pairs (both distance bands) belong to one fold: a station
is never in training and test at once. Stations are assigned greedily, heaviest
first, to the fold with the fewest hours so far (the GroupKFold rule), which keeps
the folds balanced and the assignment deterministic.
"""

from __future__ import annotations

import polars as pl


def station_folds(station_hours: pl.DataFrame, n_folds: int) -> pl.DataFrame:
    """``cv_group, fold``: ``station_hours`` has ``cv_group, hours`` (one row per station)."""
    load = [0] * n_folds
    rows = []
    for group, hours in station_hours.sort(["hours", "cv_group"], descending=[True, False]).iter_rows():
        fold = min(range(n_folds), key=lambda f: (load[f], f))
        load[fold] += hours
        rows.append({"cv_group": group, "fold": fold})
    return pl.DataFrame(rows, schema={"cv_group": pl.String, "fold": pl.Int32})
