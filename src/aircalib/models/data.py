"""Model inputs: the gold table as matrices, fold-consistent physics, the fixed subsample.

Rules that keep the evaluation honest:

* **training rows** = training zone (main country, near band, sane pairs) of the
  other folds; **test rows** = the held-out fold's pairs of the main country, near
  band, sane *and* unverifiable (each key figure is reported for both);
* the physical features of a fold's model use the ``a, kappa`` fitted **without**
  that fold, for its training rows as for its test rows (gold stores, per row, the
  fit without the row's own fold: right for a test row, but a training row's value
  would then have seen the test fold);
* the subsample of the side studies is a fixed share of the pairs, drawn with a seed.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import numpy as np
import polars as pl

from aircalib.config import Settings
from aircalib.ingest.plan import NEAR
from aircalib.schemas.gold import CATEGORICAL, FEATURES
from aircalib.transform.features import log_floor
from aircalib.transform.physics import fit_gain_kappa, growth_factor, physical_estimate

TARGET, LOG_TARGET = "reference", "log_reference"


def gold_path(settings: Settings) -> Path:
    """The gold table of usable pair-hours with their features."""
    return settings.paths.gold / "pair_hours.parquet"


def file_hash(path: Path, chunk: int = 1 << 24) -> str:
    """SHA-256 of a file: ties every MLflow run to the exact gold table it used."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while block := handle.read(chunk):
            digest.update(block)
    return digest.hexdigest()


def load(settings: Settings, rows: pl.Expr | None = None) -> pl.DataFrame:
    """Pair-hours with a ``has_neighbours`` flag; by default the scope of step 6 (main country, near band).

    Step 7 widens ``rows`` (far band, France); the training rows stay ``training_zone``.
    """
    if rows is None:
        rows = (pl.col("country") == settings.study.main_country) & (pl.col("distance_band") == NEAR)
    return (
        pl.scan_parquet(gold_path(settings))
        .filter(rows)
        .with_columns(has_neighbours=pl.col("neighbour_median").is_not_null())
        .collect()
    )


def fit_physics(train: pl.DataFrame, settings: Settings) -> tuple[float, float]:
    """``a, kappa`` fitted on the given training rows only (step 7: e.g. 2024 rows alone)."""
    part = train.drop_nulls(["log_pm25", "log_reference", "official_humidity"])
    fit = fit_gain_kappa(
        part["log_pm25"].to_numpy(),
        part["log_reference"].to_numpy(),
        part["official_humidity"].to_numpy(),
        settings.physics,
    )
    return fit.gain, fit.kappa


def feature_list(drop_families: tuple[str, ...] = ()) -> list[str]:
    """Model features, optionally without some families (ablation)."""
    return [c for family, columns in FEATURES.items() if family not in drop_families for c in columns]


def categories(frame: pl.DataFrame) -> dict[str, list[str]]:
    """Fixed category lists (sorted) so that codes mean the same thing in every fold."""
    return {c: sorted(frame[c].drop_nulls().unique().to_list()) for c in CATEGORICAL if c in frame.columns}


def matrix(frame: pl.DataFrame, features: list[str], cats: dict[str, list[str]]) -> np.ndarray:
    """Float32 matrix; categoricals as integer codes, missing values as NaN (LightGBM handles them)."""
    exprs = []
    for column in features:
        if column in cats:
            mapping = {value: float(i) for i, value in enumerate(cats[column])}
            exprs.append(pl.col(column).replace_strict(mapping, default=None, return_dtype=pl.Float32))
        else:
            exprs.append(pl.col(column).cast(pl.Float32))
    return frame.select(exprs).to_numpy()


def with_fold_physics(frame: pl.DataFrame, gain: float, kappa: float, settings: Settings) -> pl.DataFrame:
    """Recompute the physical features of every row with one fold's ``a, kappa``."""
    rh, pm = pl.col("official_humidity"), pl.col("pm25_mean")
    return frame.with_columns(
        kohler_c=growth_factor(rh, kappa, settings.physics).cast(pl.Float32),
        physics_pm25=physical_estimate(pm, rh, gain, kappa, settings.physics).cast(pl.Float32),
    ).with_columns(
        log_physics_pm25=log_floor(pl.col("physics_pm25"), settings.features.log_floor_ugm3).cast(pl.Float32)
    )


def subsample_pairs(frame: pl.DataFrame, share: float, seed: int) -> list[str]:
    """A fixed random share of the pairs (sane and unverifiable alike), the same for every study."""
    pairs = frame.select("pair_id").unique().sort("pair_id")
    return pairs.sample(fraction=share, seed=seed, shuffle=True)["pair_id"].sort().to_list()
