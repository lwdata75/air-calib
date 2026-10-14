"""Funnel: what each rule removes, computed from the per-row statuses.

A table carries one status per row (``ok`` or the first rule it failed). For
each rule in order, the funnel counts the rows it removes and the entities
(sensors, pairs...) that lose their last remaining row at that step.
"""

from __future__ import annotations

import polars as pl

OK = "ok"


def funnel(
    frame: pl.DataFrame | pl.LazyFrame,
    status: str,
    rules: list[str],
    entities: dict[str, str | list[str]],
    weight: str | None = None,
) -> pl.DataFrame:
    """One row per step: ``start``, then each rule, then ``usable``.

    ``entities`` maps an output name to the column(s) identifying an entity
    (e.g. ``{"capteurs": "sensor_id", "paires": ["sensor_id", "segment"]}``); rows whose
    identifier is missing do not count as an entity.
    ``weight`` names a column of row counts when ``frame`` is pre-aggregated.
    Columns: ``step``, ``removed`` and ``remaining`` (rows), and for each entity
    ``<name>_lost`` (entities whose last row went at this step) and ``<name>_remaining``.
    """
    unknown = set(frame.lazy().select(pl.col(status).unique()).collect()[status].to_list()) - {OK, *rules}
    if unknown:
        raise ValueError(f"statuses missing from the rule list: {sorted(unknown)}")
    order = {rule: i for i, rule in enumerate(rules)}
    order[OK] = len(rules)
    keys = {name: [cols] if isinstance(cols, str) else cols for name, cols in entities.items()}
    n_rows = pl.col(weight).sum() if weight else pl.len()
    ranked = frame.lazy().with_columns(_rank=pl.col(status).replace_strict(order, return_dtype=pl.Int32))

    # Per entity, the highest rank reached by any of its rows: the entity survives every step before it.
    # Rows with a missing identifier (e.g. no station) do not form an entity.
    survival = {
        name: ranked.drop_nulls(cols).group_by(cols).agg(_best=pl.col("_rank").max()).collect()["_best"]
        for name, cols in keys.items()
    }
    rows_by_rank = ranked.group_by("_rank").agg(n=n_rows).collect()
    count = dict(zip(rows_by_rank["_rank"].to_list(), rows_by_rank["n"].to_list(), strict=True))

    total = sum(count.values())
    out = []
    remaining = total
    steps = [("start", -1), *[(rule, i) for i, rule in enumerate(rules)], ("usable", len(rules))]
    for step, rank in steps:
        removed = 0 if step in ("start", "usable") else count.get(rank, 0)
        remaining -= removed
        row = {"step": step, "removed": removed, "remaining": remaining}
        for name, best in survival.items():
            alive_after = int((best > rank).sum()) if step != "usable" else int((best == rank).sum())
            alive_before = int((best >= rank).sum()) if step not in ("start", "usable") else alive_after
            row[f"{name}_lost"] = alive_before - alive_after
            row[f"{name}_remaining"] = alive_after
        out.append(row)
    return pl.DataFrame(out)
