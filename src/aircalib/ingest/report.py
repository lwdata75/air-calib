"""Ingestion report (``reports/ingestion.md``), rebuilt from the manifests at each run."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import polars as pl

from aircalib.config import Settings
from aircalib.ingest.download import sc_manifests
from aircalib.ingest.manifest import Manifest


def folder_bytes(path: Path) -> int:
    """Total size of the files under ``path`` (0 if absent)."""
    if not path.exists():
        return 0
    return sum(p.stat().st_size for p in path.rglob("*") if p.is_file())


def sensor_community_summary(raw: pl.DataFrame, bronze: pl.DataFrame) -> pl.DataFrame:
    """Per sensor kind: planned work, what the server had, failures and volumes."""
    if raw.is_empty():
        return pl.DataFrame()
    dl = raw.group_by("kind").agg(
        units_ok=(pl.col("status") == "ok").sum(),
        units_failed=(pl.col("status") == "failed").sum(),
        days_requested=pl.col("days_requested").sum(),
        days_found=pl.col("days_found").sum(),
        days_absent=pl.col("days_absent").sum(),
        downloaded_mb=pl.col("bytes").sum() / 1e6,
    )
    if bronze.is_empty():
        return dl
    audit = [c for c in bronze.columns if c.startswith("unparsed_")]
    optional = [c for c in ("decimal_commas",) if c in bronze.columns]
    conv = bronze.group_by("kind").agg(
        *[pl.col(c).sum() for c in optional],
        units_converted=(pl.col("status") == "ok").sum(),
        conversion_failed=(pl.col("status") == "failed").sum(),
        rows=pl.col("rows").sum(),
        corrupt_files=pl.col("corrupt_files").sum(),
        unparsed_values=pl.sum_horizontal(audit).sum() if audit else pl.lit(0),
        dropped_nonempty=pl.col("dropped_nonempty").sum(),
        foreign_sensor_rows=pl.col("foreign_sensor_rows").sum(),
        bronze_mb=pl.col("bronze_bytes").sum() / 1e6,
    )
    return dl.join(conv, on="kind", how="left").sort("kind")


def _table(frame: pl.DataFrame) -> str:
    if frame.is_empty():
        return "_(rien)_\n"
    head = "| " + " | ".join(frame.columns) + " |"
    sep = "|" + "---|" * frame.width
    rows = []
    for row in frame.iter_rows():
        cells = [
            f"{v:,.1f}" if isinstance(v, float) else f"{v:,}" if isinstance(v, int) else str(v) for v in row
        ]
        rows.append("| " + " | ".join(cells) + " |")
    return "\n".join([head, sep, *rows]) + "\n"


def build(settings: Settings) -> str:
    paths = settings.paths
    raw_m, bronze_m = sc_manifests(settings)
    sc = sensor_community_summary(raw_m.frame(), bronze_m.frame())
    others = []
    for source, key in (
        ("eea", ("pollutant", "sampling_point")),
        ("dwd", ("filename",)),
        ("meteo_france", ("filename",)),
    ):
        for layer, root in (("raw", paths.raw), ("bronze", paths.bronze)):
            frame = Manifest(root / source / "_manifest.jsonl", key).frame()
            if not frame.is_empty():
                others.append(
                    {
                        "source": source,
                        "layer": layer,
                        "ok": int((frame["status"] == "ok").sum()),
                        "failed": int((frame["status"] == "failed").sum()),
                    }
                )
    disk = pl.DataFrame(
        {
            "folder": ["raw", "bronze", "meta"],
            "gb": [
                folder_bytes(paths.raw) / 1e9,
                folder_bytes(paths.bronze) / 1e9,
                folder_bytes(paths.meta) / 1e9,
            ],
        }
    )
    stamp = datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC")
    mode = "échantillon (`--sample`)" if paths.sample else "complet"
    return (
        f"# Rapport d'ingestion — mode {mode}\n\n"
        f"_Généré automatiquement le {stamp} à partir des manifestes._\n\n"
        "## Sensor.Community (unités = capteur × mois)\n\n"
        + _table(sc)
        + "\n## EEA, DWD, Météo-France (unités = fichiers)\n\n"
        + _table(pl.DataFrame(others))
        + "\n## Espace disque\n\n"
        + _table(disk)
    )


def write(settings: Settings) -> Path:
    name = "ingestion_sample.md" if settings.paths.sample else "ingestion.md"
    dest = settings.paths.reports_dir / name
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(build(settings), encoding="utf-8")
    return dest
