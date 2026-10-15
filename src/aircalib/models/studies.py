"""Step 6 studies, each logged to MLflow: ``python -m aircalib train --study <name>``.

* ``ladder``: the model ladder on the 5 spatial folds, full volume;
* side studies on the fixed subsample (``models.subsample_pair_share`` of the pairs):
  ``ablation`` (each feature family removed, including the neighbours), ``scale`` (ln vs
  ug/m3 target), ``monotone`` (constraint on the raw PM), ``unverifiable`` (training with
  or without the unverifiable pairs), ``x`` (dead-month threshold 0.1 / 0.3 / 0.5),
  ``optuna`` (hyper-parameters, nested runs), ``recalibration`` (linear recalibration of
  the predictions, learned out of fold, against the EPA slope);
* ``final``: the retained configuration retrained on the full volume;
* ``attribution``: full volume, default parameters: rung 3 then each change of the
  retained configuration alone (ug/m3 target, monotone constraint, no local probe), then
  all three, to attribute the loss on the unverifiable pairs.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from pathlib import Path

import polars as pl

from aircalib.config import Settings
from aircalib.logging import get_logger
from aircalib.models import data as mdata
from aircalib.models import tracking
from aircalib.models.cv import CrossValidation, Split, fold_splits, run_splits
from aircalib.models.ladder import Boosting, LogLinear, Physics, RawSensor, Recalibrated

log = get_logger(__name__)

TRAIN = pl.col("training_zone")
NEIGHBOURS = "voisinage citoyen"


class Context:
    """Data shared by the studies (loaded once)."""

    def __init__(self, settings: Settings, subsample: bool, rows: pl.Expr | None = None) -> None:
        """Load the rows of the study (all of them, or the fixed subsample of pairs) once."""
        self.settings = settings
        self.full = mdata.load(settings, rows)
        self.categories = mdata.categories(self.full)
        self.fits = pl.read_parquet(settings.paths.gold / "physics_fits.parquet")
        self.gold_hash = mdata.file_hash(mdata.gold_path(settings))
        self.sub_pairs = mdata.subsample_pairs(
            self.full, settings.models.subsample_pair_share, settings.models.seed
        )
        self.data = self.full.filter(pl.col("pair_id").is_in(self.sub_pairs)) if subsample else self.full
        self.scope = "sous-échantillon" if subsample else "complet"

    def boosting(self, features: list[str] | None = None, **kwargs) -> Callable[[], Boosting]:
        """A LightGBM factory with the configured parameters, overridable per study."""
        m = self.settings.models
        params = {**m.lightgbm, **kwargs.pop("lgb_params", {})}
        rounds = kwargs.pop("num_boost_round", m.num_boost_round)
        feats = features or mdata.feature_list()
        return lambda: Boosting(feats, self.categories, params, rounds, **kwargs)

    def evaluate(
        self,
        name: str,
        make_model: Callable[[], object],
        *,
        features: list[str] | None = None,
        train_mask: pl.Expr = TRAIN,
        data: pl.DataFrame | None = None,
        splits: list[Split] | None = None,
        nested: bool = False,
        extra: dict | None = None,
        keep_predictions: bool = True,
        slices: dict[str, pl.Expr] | None = None,
    ) -> dict[str, float]:
        """Train and predict (the 5 station folds unless ``splits`` is given), then log the run."""
        start = time.perf_counter()
        splits = splits or fold_splits(self.fits, self.settings, train_mask=train_mask)
        result = run_splits(make_model, self.data if data is None else data, splits, self.settings)
        return self.log(
            name,
            result,
            features=features,
            nested=nested,
            extra=extra,
            keep_predictions=keep_predictions,
            slices=slices,
            start=start,
        )

    def log(
        self,
        name: str,
        result: CrossValidation,
        *,
        features: list[str] | None = None,
        nested: bool = True,
        extra: dict | None = None,
        keep_predictions: bool = True,
        slices: dict[str, pl.Expr] | None = None,
        start: float | None = None,
    ) -> dict[str, float]:
        """Log predictions (computed here or read back from another run) as one MLflow run."""
        start = time.perf_counter() if start is None else start
        with tracking.run(name, nested=nested, tags={"scope": self.scope}):
            flat = tracking.log_cv(
                result,
                self.settings,
                features=features or [],
                gold_hash=self.gold_hash,
                extra_params={"scope": self.scope, **(extra or {})},
                keep_predictions=keep_predictions,
                slices=slices,
            )
        log.info(
            "%s: hourly sane RMSE %.2f, EPA hourly %.1f %%, daily %.1f %% (%.0f s)",
            name,
            flat["hourly_saines_rmse"],
            flat["hourly_saines_pct_epa_all"],
            flat["daily_saines_pct_epa_all"],
            time.perf_counter() - start,
        )
        return flat


# ------------------------------------------------------------------------------- full volume
def ladder(ctx: Context) -> None:
    """The model ladder on the 5 station folds (rungs 0 to 5)."""
    m = ctx.settings.models
    features = mdata.feature_list()
    rungs = [
        ("0_capteur_brut", RawSensor, ["pm25_mean"]),
        ("1_lineaire_log", lambda: LogLinear(list(m.linear_features)), list(m.linear_features)),
        ("2_physique", Physics, ["pm25_mean", "official_humidity"]),
        ("3_lightgbm", ctx.boosting(features), features),
        ("4_hybride", ctx.boosting(features, residual_of="log_physics_pm25", name="4_hybride"), features),
        ("5_quantiles", ctx.boosting(features, quantiles=tuple(m.quantiles), name="5_quantiles"), features),
    ]
    with tracking.run("echelle_de_modeles", tags={"scope": ctx.scope}):
        for name, factory, feats in rungs:
            ctx.evaluate(name, factory, features=feats, nested=True)


# ------------------------------------------------------------------------------- side studies
def ablation(ctx: Context) -> None:
    """Each feature family removed in turn, including the citizen neighbours."""
    from aircalib.schemas.gold import FEATURES  # noqa: PLC0415

    with tracking.run("ablation", tags={"scope": ctx.scope}):
        ctx.evaluate("toutes_les_familles", ctx.boosting(), features=mdata.feature_list(), nested=True)
        for family in FEATURES:
            feats = mdata.feature_list(drop_families=(family,))
            ctx.evaluate(
                f"sans_{family}",
                ctx.boosting(feats),
                features=feats,
                nested=True,
                extra={"dropped_family": family},
                keep_predictions=family == NEIGHBOURS,
            )


def scale(ctx: Context) -> None:
    """The same LightGBM with a ln target (Duan back-transform) and a ug/m3 target."""
    with tracking.run("echelle_log_vs_lineaire", tags={"scope": ctx.scope}):
        ctx.evaluate("cible_log", ctx.boosting(log_target=True), features=mdata.feature_list(), nested=True)
        ctx.evaluate("cible_ugm3", ctx.boosting(log_target=False), features=mdata.feature_list(), nested=True)


def monotone(ctx: Context) -> None:
    """LightGBM with and without the monotone constraint on the raw PM."""
    mono = tuple(ctx.settings.models.monotone_features)
    with tracking.run("contrainte_monotone", tags={"scope": ctx.scope}):
        ctx.evaluate("sans_contrainte", ctx.boosting(), features=mdata.feature_list(), nested=True)
        ctx.evaluate(
            "avec_contrainte",
            ctx.boosting(monotone=mono),
            features=mdata.feature_list(),
            nested=True,
            extra={"monotone_features": ",".join(mono)},
        )


def unverifiable(ctx: Context) -> None:
    """Training on sane pairs only, then on every pair; evaluated on the same test pairs."""
    with_them = pl.col("country") == ctx.settings.study.main_country  # near band already
    with tracking.run("paires_inverifiables", tags={"scope": ctx.scope}):
        ctx.evaluate("entrainement_paires_saines", ctx.boosting(), features=mdata.feature_list(), nested=True)
        ctx.evaluate(
            "entrainement_toutes_paires",
            ctx.boosting(),
            features=mdata.feature_list(),
            nested=True,
            train_mask=with_them,
        )


def x_threshold(ctx: Context) -> None:
    """Local humidity recomputed with X = 0.1, 0.3 and 0.5 (dead-month threshold), model retrained."""
    from aircalib.models.variants import local_humidity_for  # noqa: PLC0415

    with tracking.run("sensibilite_X", tags={"scope": ctx.scope}):
        for x in (0.1, 0.3, 0.5):
            local = local_humidity_for(ctx.settings, x)
            data = (
                ctx.data.drop("local_humidity", "local_temperature", "local_reliable")
                .join(local, on=["pair_id", "hour"], how="left")
                .with_columns(pl.col("local_reliable").fill_null(False))
            )
            ctx.evaluate(
                f"X_{x}",
                ctx.boosting(),
                features=mdata.feature_list(),
                data=data,
                nested=True,
                extra={"dead_month_share": x},
                keep_predictions=False,
            )


def optuna_search(ctx: Context) -> dict:
    """TPE search of LightGBM's hyper-parameters (nested runs); the best ones are written to disk."""
    import optuna  # noqa: PLC0415

    m = ctx.settings.models
    features = mdata.feature_list()

    def objective(trial: optuna.Trial) -> float:
        """One trial: a full cross-validation; returns the hourly RMSE of the sane pairs."""
        params = {}
        for name, r in m.optuna_space.items():
            if r.integer:
                params[name] = trial.suggest_int(name, int(r.low), int(r.high), log=r.log)
            else:
                params[name] = trial.suggest_float(name, r.low, r.high, log=r.log)
        rounds = params.pop("num_boost_round")
        flat = ctx.evaluate(
            f"essai_{trial.number:02d}",
            ctx.boosting(features, lgb_params=params, num_boost_round=rounds),
            features=features,
            nested=True,
            extra={"trial": trial.number},
            keep_predictions=False,
        )
        return flat["hourly_saines_rmse"]

    with tracking.run("optuna", tags={"scope": ctx.scope}):
        study = optuna.create_study(direction="minimize", sampler=optuna.samplers.TPESampler(seed=m.seed))
        study.optimize(objective, n_trials=m.optuna_trials)
        best = {**study.best_params, "best_rmse": study.best_value}
        import mlflow  # noqa: PLC0415

        mlflow.log_params({f"best_{k}": v for k, v in best.items()})
        mlflow.log_dict(best, "best_params.json")
    out = ctx.settings.paths.gold.parent / "models"
    out.mkdir(parents=True, exist_ok=True)
    (out / "optuna_best.json").write_text(json.dumps(best, indent=1), encoding="utf-8")
    return best


def recalibration(ctx: Context) -> None:
    """Plain LightGBM against the out-of-fold linear recalibration of its predictions."""
    base = ctx.boosting()
    with tracking.run("recalibration", tags={"scope": ctx.scope}):
        ctx.evaluate("sans_recalibration", base, features=mdata.feature_list(), nested=True)
        ctx.evaluate(
            "recalibration_hors_pli", lambda: Recalibrated(base), features=mdata.feature_list(), nested=True
        )


def retained(ctx: Context, *, tuned: bool = True, name: str = "modele_retenu") -> tuple[Callable, list[str]]:
    """The retained model (``models.final``): factory and features; ``tuned`` uses Optuna's parameters."""
    cfg = ctx.settings.models.final
    features = mdata.feature_list(drop_families=tuple(cfg.drop_families))
    common = {
        "log_target": cfg.log_target,
        "residual_of": cfg.residual_of or None,
        "monotone": tuple(ctx.settings.models.monotone_features) if cfg.monotone else (),
    }
    params, rounds = {}, ctx.settings.models.num_boost_round
    if tuned and cfg.use_optuna:
        params = json.loads(best_params_path(ctx.settings).read_text(encoding="utf-8"))
        rounds = params.pop("num_boost_round")
        for key in ("best_rmse", "trial"):
            params.pop(key, None)
    model = ctx.boosting(features, lgb_params=params, num_boost_round=rounds, name=name, **common)
    factory = (lambda m=model: Recalibrated(m)) if cfg.recalibrate else model
    return factory, features


def final(ctx: Context) -> None:
    """The retained configuration (``models.final``), full volume, with default and with Optuna's parameters.

    Both variants share everything but the hyper-parameters: the difference measures what tuning brings.
    """
    cfg = ctx.settings.models.final
    variants = [("final_reglages_defaut", False)]
    if cfg.use_optuna:
        variants.append(("final_reglages_optuna", True))
    with tracking.run("modele_final", tags={"scope": ctx.scope}):
        for name, tuned in variants:
            factory, features = retained(ctx, tuned=tuned, name=name)
            ctx.evaluate(
                name,
                factory,
                features=features,
                nested=True,
                extra={"final_" + k: str(v) for k, v in cfg.model_dump().items()},
            )


def attribution(ctx: Context) -> None:
    """Rung 3 plus each change of ``models.final`` alone, then together (default parameters, full volume)."""
    cfg = ctx.settings.models.final
    mono = tuple(ctx.settings.models.monotone_features)
    full = mdata.feature_list()
    reduced = mdata.feature_list(drop_families=tuple(cfg.drop_families))
    variants = [
        ("echelon_3", full, {}),
        ("cible_ugm3", full, {"log_target": False}),
        ("contrainte_monotone", full, {"monotone": mono}),
        ("sans_sonde_locale", reduced, {}),
        ("trois_changements", reduced, {"log_target": False, "monotone": mono}),
    ]
    with tracking.run("attribution_recul", tags={"scope": ctx.scope}):
        for name, features, kwargs in variants:
            ctx.evaluate(
                name, ctx.boosting(features, **kwargs), features=features, nested=True, keep_predictions=False
            )


def rescore(ctx: Context) -> None:
    """Rescore the ladder runs from their stored predictions (new breakdowns, no retraining)."""
    from aircalib.models.results import children  # noqa: PLC0415

    runs = children(ctx.settings, "echelle_de_modeles", ctx.scope)
    log.info("rescored %d ladder runs", tracking.rescore(ctx.settings, runs["run_id"].to_list()))


STUDIES: dict[str, Callable[[Context], object]] = {
    "ladder": ladder,
    "ablation": ablation,
    "scale": scale,
    "monotone": monotone,
    "unverifiable": unverifiable,
    "x": x_threshold,
    "optuna": optuna_search,
    "recalibration": recalibration,
    "final": final,
    "rescore": rescore,
    "attribution": attribution,
}
FULL_VOLUME = {"ladder", "final", "rescore", "attribution"}


def best_params_path(settings: Settings) -> Path:
    """Where Optuna's best parameters are stored."""
    return settings.paths.gold.parent / "models" / "optuna_best.json"


def run(settings: Settings, study: str) -> None:
    """Run one study of steps 6-7 by name (``python -m aircalib train --study <name>``)."""
    from aircalib.models import generalisation  # noqa: PLC0415 - step 7 builds on this module

    studies = {**STUDIES, **generalisation.STUDIES}
    if study not in studies:
        raise ValueError(f"unknown study {study!r}; choose among {', '.join(studies)}")
    tracking.setup(settings)
    rows = generalisation.ROWS[study](settings) if study in generalisation.ROWS else None
    ctx = Context(settings, subsample=study not in FULL_VOLUME | set(generalisation.STUDIES), rows=rows)
    log.info(
        "study %s on %s: %d hours, %d pairs",
        study,
        ctx.scope,
        ctx.data.height,
        ctx.data["pair_id"].n_unique(),
    )
    studies[study](ctx)
