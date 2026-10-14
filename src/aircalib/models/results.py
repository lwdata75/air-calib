"""Read the MLflow runs back as tables (for the notebook and the reports)."""

from __future__ import annotations

import os

import polars as pl

from aircalib.config import Settings


def _client(settings: Settings):
    os.environ.setdefault("MLFLOW_DISABLE_AGENT_HINT", "1")
    import mlflow  # noqa: PLC0415

    from aircalib.models.tracking import setup  # noqa: PLC0415

    setup(settings)
    return mlflow


def children(settings: Settings, parent: str, scope: str | None = None) -> pl.DataFrame:
    """Latest run named ``parent`` (optionally of a scope) and its child runs, one row each."""
    mlflow = _client(settings)
    runs = mlflow.search_runs(experiment_names=[settings.models.experiment], output_format="list")
    parents = [
        r for r in runs if r.info.run_name == parent and (scope is None or r.data.tags.get("scope") == scope)
    ]
    if not parents:
        return pl.DataFrame()
    top = max(parents, key=lambda r: r.info.start_time)
    kids = [r for r in runs if r.data.tags.get("mlflow.parentRunId") == top.info.run_id]
    rows = [
        {
            "run": r.info.run_name,
            "run_id": r.info.run_id,
            **r.data.metrics,
            **{f"param_{k}": v for k, v in r.data.params.items()},
        }
        for r in sorted(kids, key=lambda r: r.info.start_time)
    ]
    return pl.DataFrame(rows, infer_schema_length=None)


def summary(table: pl.DataFrame, subset: str = "saines") -> pl.DataFrame:
    """Key metrics side by side: hourly and daily RMSE, MAE, R², slope, intercept, EPA pass rate."""
    columns = ["run"]
    for res in ("hourly", "daily"):
        for metric in ("rmse", "mae", "r2", "slope", "intercept", "pct_epa_all"):
            name = f"{res}_{subset}_{metric}"
            if name in table.columns:
                columns.append(name)
    return table.select(columns).with_columns(pl.col(pl.Float64).round(3))


def artifact(settings: Settings, run_id: str, path: str) -> str:
    mlflow = _client(settings)
    return mlflow.artifacts.download_artifacts(run_id=run_id, artifact_path=path)
