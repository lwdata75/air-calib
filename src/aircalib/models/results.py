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


def run_table(settings: Settings, parent: str, run: str, name: str, scope: str = "complet") -> pl.DataFrame:
    """A CSV table logged by a run (``summary``, ``per_fold``, ``slices_<breakdown>``)."""
    runs = children(settings, parent, scope)
    run_id = runs.filter(pl.col("run") == run)["run_id"][0]
    return pl.read_csv(artifact(settings, run_id, f"{name}.csv"))


def compact(table: pl.DataFrame, keys: list[str] | None = None) -> pl.DataFrame:
    """Hourly and daily rows of a summary / slice table side by side, rounded, one row per key.

    Columns: hours, RMSE, R², slope, intercept, pairs scored and % of pairs passing the EPA
    targets (hourly), then RMSE and % of pairs passing (daily).
    """
    keys = (keys or []) + ["paires", "voisins"]
    hourly = table.filter(pl.col("résolution") == "hourly").select(
        *keys,
        heures=pl.col("n").cast(pl.Int64),
        rmse_h=pl.col("rmse").round(2),
        r2_h=pl.col("r2").round(2),
        pente_h=pl.col("slope").round(2),
        ordonnee_h=pl.col("intercept").round(2),
        paires_notees=pl.col("pairs").cast(pl.Int64),
        epa_h=pl.col("pct_epa_all").round(1),
    )
    daily = table.filter(pl.col("résolution") == "daily").select(
        *keys, rmse_j=pl.col("rmse").round(2), epa_j=pl.col("pct_epa_all").round(1)
    )
    order = {"saines": 0, "toutes": 1}
    nb = {"toutes": 0, "avec_voisins": 1, "sans_voisins": 2}
    return (
        hourly.join(daily, on=keys, how="left")
        .with_columns(_p=pl.col("paires").replace_strict(order), _v=pl.col("voisins").replace_strict(nb))
        .sort(*keys[:-2], "_p", "_v")
        .drop("_p", "_v")
    )
