"""MLflow tracking (local, ``sqlite:///mlflow.db``): one run per model or study variant.

Each run records the parameters, the feature list, the SHA-256 of the gold table,
the metrics per fold and pooled (hourly and daily, ug/m3, sane pairs and all
pairs), and as artifacts the out-of-fold predictions (Parquet), the feature
importances and the figures.
"""

from __future__ import annotations

import json
import math
import os
import tempfile
from contextlib import contextmanager
from pathlib import Path

import polars as pl

from aircalib.config import Settings

os.environ.setdefault("MLFLOW_DISABLE_AGENT_HINT", "1")
import mlflow

from aircalib.evaluation.scoring import interval_coverage, score
from aircalib.models.cv import CrossValidation


def setup(settings: Settings) -> None:
    os.environ.setdefault("MLFLOW_DISABLE_AGENT_HINT", "1")
    root = settings.paths.data_dir.parent
    mlflow.set_tracking_uri(settings.models.tracking_uri.replace("sqlite:///", f"sqlite:///{root}/"))
    mlflow.set_experiment(settings.models.experiment)


@contextmanager
def run(name: str, *, nested: bool = False, tags: dict | None = None):
    with mlflow.start_run(run_name=name, nested=nested, tags=tags or {}) as active:
        yield active


def log_cv(
    result: CrossValidation,
    settings: Settings,
    *,
    features: list[str],
    gold_hash: str,
    extra_params: dict | None = None,
    keep_predictions: bool = True,
) -> dict[str, float]:
    """Log one cross-validation in the active run; returns the flat metrics."""
    flat, tables = score(result.oof, settings.evaluation)
    if "q10" in result.oof.columns and "q90" in result.oof.columns:
        for subset, mask in (("saines", pl.col("sane_pair")), ("toutes", pl.lit(True))):
            cov = interval_coverage(result.oof.filter(mask), "q10", "q90")
            flat.update({f"interval_{subset}_{k}": v for k, v in cov.items()})
    flat["seconds_fit_total"] = float(sum(result.seconds_fit))
    flat["seconds_predict_total"] = float(sum(result.seconds_predict))
    mlflow.log_params(
        {
            **{k: str(v)[:500] for k, v in result.model_params.items()},
            **(extra_params or {}),
            "gold_sha256": gold_hash,
            "n_features": len(features),
            "n_test_hours": result.oof.height,
        }
    )
    mlflow.log_metrics({k: float(v) for k, v in flat.items() if v is not None and math.isfinite(v)})
    with tempfile.TemporaryDirectory() as tmp:
        folder = Path(tmp)
        (folder / "features.json").write_text(json.dumps(features, indent=1), encoding="utf-8")
        for name, table in tables.items():
            table.write_csv(folder / f"{name}.csv")
        if result.importance is not None:
            result.importance.write_csv(folder / "importance.csv")
        if keep_predictions:
            result.oof.write_parquet(folder / "oof_predictions.parquet", compression="zstd")
        mlflow.log_artifacts(str(folder))
    return flat


def log_figure(fig, name: str) -> None:
    mlflow.log_figure(fig, name)


def rescore(settings: Settings, run_ids: list[str]) -> int:
    """Recompute the metrics of finished runs from their stored out-of-fold predictions (no retraining).

    Used when the scoring gains a breakdown (e.g. hours with / without neighbours):
    the new metrics are added to the same runs. Returns the number of runs rescored.
    """
    done = 0
    for run_id in run_ids:
        try:
            path = mlflow.artifacts.download_artifacts(run_id=run_id, artifact_path="oof_predictions.parquet")
        except Exception:  # runs logged without predictions are skipped
            continue
        oof = pl.read_parquet(path)
        flat, tables = score(oof, settings.evaluation)
        if "q10" in oof.columns and "q90" in oof.columns:
            for subset, mask in (("saines", pl.col("sane_pair")), ("toutes", pl.lit(True))):
                cov = interval_coverage(oof.filter(mask), "q10", "q90")
                flat.update({f"interval_{subset}_{k}": v for k, v in cov.items()})
        with mlflow.start_run(run_id=run_id):
            mlflow.log_metrics({k: float(v) for k, v in flat.items() if v is not None and math.isfinite(v)})
            with tempfile.TemporaryDirectory() as tmp:
                for name, table in tables.items():
                    table.write_csv(Path(tmp) / f"{name}.csv")
                mlflow.log_artifacts(tmp)
        done += 1
    return done
