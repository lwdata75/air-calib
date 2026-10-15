"""Step 7 studies: does the retained model hold outside the conditions it was tuned in?

``python -m aircalib train --study <name>``, each logged to MLflow:

* ``temporal``: train on one year, test on the next. (a) **same stations**: the
  2025 hours of the pairs that had 2024 hours (calibrate a network one year, use it
  the next); (b) **new stations**: station folds *and* 2024 -> 2025; (b0) control: new
  stations, trained and tested in 2025 (same test rows as b, no time gap), so that
  ``b - b0`` is the cost of the year gap alone. References: the 5-fold predictions of
  the final model restricted to the same 2025 hours;
* ``distance``: station folds over **every** distance band: the model learns on the
  near pairs (<= 1.5 km) of the other folds' stations and predicts all pairs of the
  held-out stations, up to 3 km;
* ``france``: trained on every sane German near pair, tested on France (near and far
  bands) without any re-fitting, physics included (``a, kappa`` of Germany);
* ``breakdowns``: the final model's 5-fold predictions by station type, measurement
  method, season, humidity and sensor age (no training);
* ``conformal``: the 10-90 % quantile interval widened by split conformal calibration,
  fold by fold (no training).

Every run logs the summary (sane / all pairs × all / with / without neighbours) and
``slices_<name>.csv`` tables for its breakdowns. The raw sensor ("before") is logged
on the same rows without its predictions.
"""

from __future__ import annotations

from collections.abc import Callable

import polars as pl

from aircalib.config import Settings
from aircalib.evaluation.conformal import calibrate_by_fold
from aircalib.evaluation.metrics import bin_label
from aircalib.evaluation.scoring import interval_coverage
from aircalib.gold.build import ALL_FOLDS
from aircalib.logging import get_logger
from aircalib.models import data as mdata
from aircalib.models import results, tracking
from aircalib.models.cv import OPTIONAL, CrossValidation, Split
from aircalib.models.ladder import RawSensor
from aircalib.models.studies import TRAIN, Context, retained

log = get_logger(__name__)

FINAL_PARENT, FINAL_RUN = "modele_final", "final_reglages_optuna"
QUANTILE_PARENT, QUANTILE_RUN = "echelle_de_modeles", "5_quantiles"


def slice_expressions(settings: Settings) -> dict[str, pl.Expr]:
    """The breakdowns of step 7, as expressions on the prediction table."""
    g, ev = settings.generalisation, settings.evaluation
    months = {m: name for name, ms in g.seasons.items() for m in ms}
    return {
        "distance": bin_label(pl.col("distance_km"), g.distance_bins_km, " km"),
        "bande": pl.col("distance_band"),
        "type_station": pl.col("station_type"),
        "methode": pl.col("method_class"),
        "saison": pl.col("hour").dt.month().replace_strict(months, return_dtype=pl.String),
        "humidite": bin_label(pl.col("official_humidity"), ev.humidity_bins, " %"),
        "age": bin_label(pl.col("age_years"), g.age_bins_years, " ans"),
    }


def _slices(settings: Settings, *names: str) -> dict[str, pl.Expr]:
    """The named breakdown expressions."""
    every = slice_expressions(settings)
    return {n: every[n] for n in names}


def _logged_predictions(ctx: Context, parent: str, run: str) -> pl.DataFrame:
    """Out-of-fold predictions of a step-6 run, with the breakdown columns of this context's rows."""
    runs = results.children(ctx.settings, parent, "complet")
    if runs.is_empty() or run not in runs["run"].to_list():
        raise ValueError(f"run {parent}/{run} not found: run its step-6 study first")
    run_id = runs.filter(pl.col("run") == run)["run_id"][0]
    oof = pl.read_parquet(results.artifact(ctx.settings, run_id, "oof_predictions.parquet"))
    missing = [c for c in OPTIONAL if c not in oof.columns and c in ctx.data.columns]
    if missing:
        oof = oof.join(ctx.data.select("pair_id", "hour", *missing), on=["pair_id", "hour"], how="left")
    return oof


# ------------------------------------------------------------------------------- time
def temporal(ctx: Context) -> None:
    """Train on one year, test on the next: same stations (a), new stations (b), and the 2025 control (b0)."""
    g, settings = ctx.settings.generalisation, ctx.settings
    year = pl.col("hour").dt.year()
    first, second = year == g.train_year, year == g.test_year
    make, features = retained(ctx)
    seen = ctx.data.filter(first)["pair_id"].unique().to_list()
    same_pairs = second & pl.col("pair_id").is_in(seen)
    slices = _slices(settings, "age", "type_station", "saison")

    def fold_splits_between(train_year: pl.Expr, test_year: pl.Expr) -> list[Split]:
        """Station folds: train on ``train_year``, test on ``test_year``, physics refitted per split."""
        splits = []
        for fold in range(settings.cv.n_folds):
            train = TRAIN & train_year & (pl.col("fold") != fold)
            gain, kappa = mdata.fit_physics(ctx.data.filter(train), settings)
            splits.append(Split(train, test_year & (pl.col("fold") == fold), gain, kappa))
        return splits

    train_a = TRAIN & first
    gain_a, kappa_a = mdata.fit_physics(ctx.data.filter(train_a), settings)
    final = _logged_predictions(ctx, FINAL_PARENT, FINAL_RUN)
    with tracking.run("test_temporel", tags={"scope": ctx.scope}):
        ctx.log(
            "reference_5_plis_2025", CrossValidation(final.filter(second)), features=features, slices=slices
        )
        ctx.log(
            "reference_5_plis_2025_memes_paires",
            CrossValidation(final.filter(same_pairs)),
            features=features,
            slices=slices,
        )
        ctx.evaluate(
            "a_memes_stations_2024_vers_2025",
            make,
            features=features,
            splits=[Split(train_a, same_pairs, gain_a, kappa_a)],
            nested=True,
            extra={"gain": gain_a, "kappa": kappa_a},
            slices=slices,
        )
        ctx.evaluate(
            "b_nouvelles_stations_2024_vers_2025",
            make,
            features=features,
            splits=fold_splits_between(first, second),
            nested=True,
            slices=slices,
        )
        ctx.evaluate(
            "b0_nouvelles_stations_2025_vers_2025",
            make,
            features=features,
            splits=fold_splits_between(second, second),
            nested=True,
            slices=slices,
        )


# ------------------------------------------------------------------------------- distance
def distance(ctx: Context) -> None:
    """Station folds over every distance band, up to 3 km (raw sensor logged on the same rows)."""
    make, features = retained(ctx)
    slices = _slices(ctx.settings, "distance", "bande", "type_station")
    with tracking.run("test_distance", tags={"scope": ctx.scope}):
        ctx.evaluate("modele_retenu", make, features=features, nested=True, slices=slices)
        ctx.evaluate(
            "capteur_brut",
            RawSensor,
            features=["pm25_mean"],
            nested=True,
            slices=slices,
            keep_predictions=False,
        )


# ------------------------------------------------------------------------------- France
def france(ctx: Context) -> None:
    """Train on every sane German near pair and predict France as is (raw sensor on the same rows)."""
    settings = ctx.settings
    fit = ctx.fits.filter(pl.col("fold") == ALL_FOLDS).row(0, named=True)
    split = [Split(TRAIN, pl.col("country") == settings.study.transfer_country, fit["gain"], fit["kappa"])]
    make, features = retained(ctx)
    slices = _slices(settings, "bande", "distance", "type_station", "methode", "saison")
    with tracking.run("transfert_france", tags={"scope": ctx.scope}):
        ctx.evaluate("modele_retenu", make, features=features, splits=split, nested=True, slices=slices)
        ctx.evaluate(
            "capteur_brut",
            RawSensor,
            features=["pm25_mean"],
            splits=split,
            nested=True,
            slices=slices,
            keep_predictions=False,
        )


# ------------------------------------------------------------------------------- breakdowns, interval
def breakdowns(ctx: Context) -> None:
    """The final model's 5-fold predictions by station type, method, season, humidity and sensor age."""
    final = _logged_predictions(ctx, FINAL_PARENT, FINAL_RUN)
    slices = _slices(ctx.settings, "type_station", "methode", "saison", "humidite", "age")
    with tracking.run("decoupages", tags={"scope": ctx.scope}):
        ctx.log("modele_retenu_5_plis", CrossValidation(final), slices=slices, keep_predictions=False)


def conformal(ctx: Context) -> None:
    """Coverage of the 10-90 % interval before and after split conformal calibration, fold by fold."""
    import mlflow  # noqa: PLC0415

    m = ctx.settings.models
    low, high = min(m.quantiles), max(m.quantiles)
    lower, upper, nominal = f"q{round(100 * low)}", f"q{round(100 * high)}", high - low
    oof = _logged_predictions(ctx, QUANTILE_PARENT, QUANTILE_RUN)
    variants = {"global": []}
    if ctx.settings.generalisation.conformal_by_neighbours:
        variants["par_voisinage"] = ["has_neighbours"]
    with tracking.run("intervalle_conforme", tags={"scope": ctx.scope}):
        for name, groups in variants.items():
            calibrated, widening = calibrate_by_fold(
                oof,
                lower,
                upper,
                nominal,
                ctx.settings.features.log_floor_ugm3,
                calibration_rows=pl.col("sane_pair"),
                groups=groups,
            )
            with tracking.run(name, nested=True, tags={"scope": ctx.scope}):
                metrics = {"nominal_coverage": nominal}
                for subset, mask in (("saines", pl.col("sane_pair")), ("toutes", pl.lit(True))):
                    for nb, nb_mask in (
                        ("", pl.lit(True)),
                        ("avec_voisins_", pl.col("has_neighbours")),
                        ("sans_voisins_", ~pl.col("has_neighbours")),
                    ):
                        part = calibrated.filter(mask & nb_mask)
                        for label, lo, hi in (("avant", lower, upper), ("apres", "lower_conf", "upper_conf")):
                            cov = interval_coverage(part, lo, hi)
                            metrics.update({f"{label}_{subset}_{nb}{k}": v for k, v in cov.items()})
                per_fold = (
                    calibrated.filter(pl.col("sane_pair"))
                    .group_by("fold")
                    .agg(
                        avant=(
                            (pl.col("reference") >= pl.col(lower)) & (pl.col("reference") <= pl.col(upper))
                        ).mean(),
                        apres=(
                            (pl.col("reference") >= pl.col("lower_conf"))
                            & (pl.col("reference") <= pl.col("upper_conf"))
                        ).mean(),
                    )
                    .sort("fold")
                )
                for rec in per_fold.iter_rows(named=True):
                    metrics[f"fold{rec['fold']}_saines_avant_coverage"] = rec["avant"]
                    metrics[f"fold{rec['fold']}_saines_apres_coverage"] = rec["apres"]
                mlflow.log_params(
                    {"groups": ",".join(groups) or "aucun", "calibration": "paires saines, autres plis"}
                )
                mlflow.log_metrics({k: float(v) for k, v in metrics.items()})
                mlflow.log_table(widening.to_pandas(), "widening.json")
                mlflow.log_table(per_fold.to_pandas(), "coverage_per_fold.json")
            log.info(
                "%s: coverage (sane) %.3f -> %.3f",
                name,
                metrics["avant_saines_coverage"],
                metrics["apres_saines_coverage"],
            )


STUDIES: dict[str, Callable[[Context], object]] = {
    "temporal": temporal,
    "distance": distance,
    "france": france,
    "breakdowns": breakdowns,
    "conformal": conformal,
}
# Rows each study needs (default: main country, near band).
ROWS: dict[str, Callable[[Settings], pl.Expr]] = {
    "distance": lambda s: pl.col("country") == s.study.main_country,
    "france": lambda s: pl.col("training_zone") | (pl.col("country") == s.study.transfer_country),
}
