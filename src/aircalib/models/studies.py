"""Step 6 studies, each logged to MLflow: ``python -m aircalib train --study <name>``.

* ``ladder``: the model ladder on the 5 spatial folds, full volume;
* side studies on the fixed subsample (``models.subsample_pair_share`` of the pairs):
  ``ablation`` (each feature family removed, including the neighbours), ``scale`` (ln vs
  ug/m3 target), ``monotone`` (constraint on the raw PM), ``unverifiable`` (training with
  or without the unverifiable pairs), ``x`` (dead-month threshold 0.1 / 0.3 / 0.5),
  ``optuna`` (hyper-parameters, nested runs), ``recalibration`` (linear recalibration of
  the predictions, learned out of fold, against the EPA slope);
* ``final``: the retained configuration retrained on the full volume.
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
from aircalib.models.cv import cross_validate
from aircalib.models.ladder import Boosting, LogLinear, Physics, RawSensor, Recalibrated

log = get_logger(__name__)

TRAIN = pl.col("training_zone")
NEIGHBOURS = "voisinage citoyen"


class Context:
    """Data shared by the studies (loaded once)."""

    def __init__(self, settings: Settings, subsample: bool) -> None:
        self.settings = settings
        self.full = mdata.load(settings)
        self.categories = mdata.categories(self.full)
        self.fits = pl.read_parquet(settings.paths.gold / "physics_fits.parquet")
        self.gold_hash = mdata.file_hash(mdata.gold_path(settings))
        self.sub_pairs = mdata.subsample_pairs(
            self.full, settings.models.subsample_pair_share, settings.models.seed
        )
        self.data = self.full.filter(pl.col("pair_id").is_in(self.sub_pairs)) if subsample else self.full
        self.scope = "sous-échantillon" if subsample else "complet"

    def boosting(self, features: list[str] | None = None, **kwargs) -> Callable[[], Boosting]:
        m = self.settings.models
        params = {**m.lightgbm, **kwargs.pop("lgb_params", {})}
        rounds = kwargs.pop("num_boost_round", m.num_boost_round)
        feats = features or mdata.feature_list()
        return lambda: Boosting(feats, self.categories, params, rounds, **kwargs)

    def evaluate(
        self,
        name: str,
        make_model,
        *,
        features: list[str] | None = None,
        train_mask=TRAIN,
        data: pl.DataFrame | None = None,
        nested: bool = False,
        extra: dict | None = None,
        keep_predictions: bool = True,
    ) -> dict[str, float]:
        start = time.perf_counter()
        result = cross_validate(
            make_model, self.data if data is None else data, self.fits, self.settings, train_mask=train_mask
        )
        with tracking.run(name, nested=nested, tags={"scope": self.scope}):
            flat = tracking.log_cv(
                result,
                self.settings,
                features=features or [],
                gold_hash=self.gold_hash,
                extra_params={"scope": self.scope, **(extra or {})},
                keep_predictions=keep_predictions,
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
    with tracking.run("echelle_log_vs_lineaire", tags={"scope": ctx.scope}):
        ctx.evaluate("cible_log", ctx.boosting(log_target=True), features=mdata.feature_list(), nested=True)
        ctx.evaluate("cible_ugm3", ctx.boosting(log_target=False), features=mdata.feature_list(), nested=True)


def monotone(ctx: Context) -> None:
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
    import optuna  # noqa: PLC0415

    m = ctx.settings.models
    features = mdata.feature_list()

    def objective(trial: optuna.Trial) -> float:
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
    base = ctx.boosting()
    with tracking.run("recalibration", tags={"scope": ctx.scope}):
        ctx.evaluate("sans_recalibration", base, features=mdata.feature_list(), nested=True)
        ctx.evaluate(
            "recalibration_hors_pli", lambda: Recalibrated(base), features=mdata.feature_list(), nested=True
        )


def final(ctx: Context) -> None:
    """The retained configuration (``models.final``), full volume, with default and with Optuna's parameters.

    Both variants share everything but the hyper-parameters: the difference measures what tuning brings.
    """
    cfg = ctx.settings.models.final
    tuned = json.loads(best_params_path(ctx.settings).read_text(encoding="utf-8"))
    tuned_rounds = tuned.pop("num_boost_round")
    for key in ("best_rmse", "trial"):
        tuned.pop(key, None)
    features = mdata.feature_list(drop_families=tuple(cfg.drop_families))
    common = {
        "log_target": cfg.log_target,
        "residual_of": cfg.residual_of or None,
        "monotone": tuple(ctx.settings.models.monotone_features) if cfg.monotone else (),
    }
    variants = [("final_reglages_defaut", {}, ctx.settings.models.num_boost_round)]
    if cfg.use_optuna:
        variants.append(("final_reglages_optuna", tuned, tuned_rounds))
    with tracking.run("modele_final", tags={"scope": ctx.scope}):
        for name, params, rounds in variants:
            model = ctx.boosting(features, lgb_params=params, num_boost_round=rounds, name=name, **common)
            factory = (lambda m=model: Recalibrated(m)) if cfg.recalibrate else model
            ctx.evaluate(
                name,
                factory,
                features=features,
                nested=True,
                extra={"final_" + k: str(v) for k, v in cfg.model_dump().items()},
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
}
FULL_VOLUME = {"ladder", "final", "rescore"}


def best_params_path(settings: Settings) -> Path:
    return settings.paths.gold.parent / "models" / "optuna_best.json"


def run(settings: Settings, study: str) -> None:
    tracking.setup(settings)
    ctx = Context(settings, subsample=study not in FULL_VOLUME)
    log.info(
        "study %s on %s: %d hours, %d pairs",
        study,
        ctx.scope,
        ctx.data.height,
        ctx.data["pair_id"].n_unique(),
    )
    STUDIES[study](ctx)
