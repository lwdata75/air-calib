"""Append-only manifests: what was downloaded or converted, and how it went.

A manifest is a JSON-lines file with one record per finished *work unit*
(e.g. one sensor-month). It serves two purposes:

* **resume**: a re-run skips the units already recorded with ``status == "ok"``;
* **audit**: volumes, absent files and failures are read back to build the
  ingestion report (nothing is silently dropped).

Appending one line per unit is crash-safe: an interrupted run loses at most the
unit in progress, which is simply redone.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import polars as pl

OK = "ok"
FAILED = "failed"


class Manifest:
    """JSON-lines log of finished work units, identified by ``key`` fields."""

    def __init__(self, path: Path, key: Sequence[str]) -> None:
        self.path = path
        self.key = tuple(key)

    def append(self, record: dict[str, Any]) -> None:
        missing = [k for k in self.key if k not in record]
        if missing:
            raise KeyError(f"manifest record lacks key field(s) {missing}")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        stamped = {**record, "recorded_at": datetime.now(UTC).isoformat(timespec="seconds")}
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(stamped, default=str) + "\n")

    def frame(self) -> pl.DataFrame:
        """Latest record per key (a failed unit that later succeeded counts as ok)."""
        if not self.path.exists() or self.path.stat().st_size == 0:
            return pl.DataFrame()
        records = pl.read_ndjson(self.path, infer_schema_length=None)
        return records.unique(subset=list(self.key), keep="last", maintain_order=True)

    def done(self) -> set[tuple]:
        """Keys of the units whose latest record is a success."""
        frame = self.frame()
        if frame.is_empty():
            return set()
        ok = frame.filter(pl.col("status") == OK).select(self.key)
        return {tuple(str(v) for v in row) for row in ok.iter_rows()}

    def key_of(self, record: dict[str, Any]) -> tuple:
        return tuple(str(record[k]) for k in self.key)
