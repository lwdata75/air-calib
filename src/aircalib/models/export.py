"""Export the key results as small CSV files in ``reports/results/`` (versioned).

``python -m aircalib export`` reads the MLflow runs of steps 6-7 (and the gold pair
table for the health score) and writes one light CSV per table. Notebook 00 and the
README read these files only: they can be opened without any data or MLflow store.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import polars as pl

from aircalib.config import Settings
from aircalib.evaluation.health import threshold_cv
from aircalib.evaluation.scoring import pair_distribution
from aircalib.logging import get_logger
from aircalib.models import results

log = get_logger(__name__)

SCOPE_FULL, SCOPE_SUB = "complet", "sous-échantillon"


def _label(table: pl.DataFrame, labels: dict[str, str]) -> pl.DataFrame:
    """Constant label columns, placed first."""
    if not labels:
        return table
    return table.with_columns(**{k: pl.lit(v) for k, v in labels.items()}).select(
        *labels, pl.exclude(*labels)
    )


def _summary(settings: Settings, parent: str, run: str, **labels: str) -> pl.DataFrame:
    """A run's summary table (hourly and daily side by side), with label columns."""
    return _label(results.compact(results.run_table(settings, parent, run, "summary")), labels)


def _slices(settings: Settings, parent: str, run: str, name: str, **labels: str) -> pl.DataFrame:
    """A run's table for one breakdown, with label columns."""
    return _label(results.compact(results.run_table(settings, parent, run, f"slices_{name}"), [name]), labels)


def _runs(settings: Settings, parent: str, labels: dict[str, str]) -> pl.DataFrame:
    """Summaries of several child runs of one parent, labelled."""
    return pl.concat([_summary(settings, parent, run, modele=label) for run, label in labels.items()])


def before(settings: Settings) -> pl.DataFrame:
    """The raw sensor ("before"): Germany at every distance, France without any correction."""
    return pl.concat(
        [
            _summary(settings, "test_distance", "capteur_brut", pays="DE", perimetre="0-3 km"),
            _summary(settings, "transfert_france", "capteur_brut", pays="FR", perimetre="0-3 km"),
        ]
    )


def ladder(settings: Settings) -> pl.DataFrame:
    """Summaries of the model ladder and of the retained model (default and Optuna parameters)."""
    labels = {
        "0_capteur_brut": "0. capteur brut",
        "1_lineaire_log": "1. linéaire en log",
        "2_physique": "2. modèle physique",
        "3_lightgbm": "3. LightGBM",
        "4_hybride": "4. hybride",
        "5_quantiles": "5. quantiles",
    }
    ladder_runs = _runs(settings, "echelle_de_modeles", labels)
    final_runs = _runs(
        settings,
        "modele_final",
        {
            "final_reglages_defaut": "retenu, réglages par défaut",
            "final_reglages_optuna": "retenu, réglages Optuna",
        },
    )
    return pl.concat([ladder_runs, final_runs])


def ablation(settings: Settings) -> pl.DataFrame:
    """Hourly RMSE (sane pairs) without each family, and the gain of Optuna's best trial, same subsample."""
    runs = results.children(settings, "ablation", SCOPE_SUB)
    base = runs.filter(pl.col("run") == "toutes_les_familles")["hourly_saines_rmse"][0]
    trials = results.children(settings, "optuna", SCOPE_SUB)
    best = (
        trials.sort("hourly_saines_rmse").head(1).with_columns(run=pl.lit("réglage Optuna (meilleur essai)"))
    )
    table = pl.concat([runs, best], how="diagonal_relaxed").select(
        effet=pl.col("run")
        .str.replace("^sans_", "sans : ")
        .str.replace("toutes_les_familles", "modèle complet"),
        rmse_h=pl.col("hourly_saines_rmse").round(3),
        delta_rmse=(pl.col("hourly_saines_rmse") - base).round(3),
        delta_pct=(100 * (pl.col("hourly_saines_rmse") - base) / base).round(1),
        epa_h=pl.col("hourly_saines_pct_epa_all").round(1),
        epa_j=pl.col("daily_saines_pct_epa_all").round(1),
        rmse_h_avec_voisins=pl.col("hourly_saines_avec_voisins_rmse").round(3),
        rmse_h_sans_voisins=pl.col("hourly_saines_sans_voisins_rmse").round(3),
    )
    return table.sort("delta_rmse", descending=True)


def attribution(settings: Settings) -> pl.DataFrame:
    """Rung 3, each change of the retained model alone, then all three (full volume)."""
    labels = {
        "echelon_3": "échelon 3 (log, sans contrainte, avec sonde)",
        "cible_ugm3": "+ cible en µg/m³",
        "contrainte_monotone": "+ contrainte monotone",
        "sans_sonde_locale": "− sonde locale",
        "trois_changements": "les trois changements",
    }
    return _runs(settings, "attribution_recul", labels)


TEMPORAL = {
    "reference_5_plis_2025": "référence, 5 plis, heures 2025",
    "b0_nouvelles_stations_2025_vers_2025": "b0 témoin : nouvelles stations, 2025 → 2025",
    "b_nouvelles_stations_2024_vers_2025": "b : nouvelles stations, 2024 → 2025",
    "reference_5_plis_2025_memes_paires": "référence, mêmes paires, heures 2025",
    "a_memes_stations_2024_vers_2025": "a : mêmes stations, 2024 → 2025",
}


def temporal(settings: Settings) -> pl.DataFrame:
    """Summaries of the time test (scenarios a, b, b0 and references)."""
    return _runs(settings, "test_temporel", TEMPORAL)


def temporal_age(settings: Settings) -> pl.DataFrame:
    """The time test by sensor age."""
    return pl.concat(
        [_slices(settings, "test_temporel", run, "age", modele=label) for run, label in TEMPORAL.items()]
    )


def distance(settings: Settings) -> pl.DataFrame:
    """Raw and corrected sensor by distance band (stations held out)."""
    return pl.concat(
        [
            _slices(settings, "test_distance", "capteur_brut", "distance", modele="brut"),
            _slices(settings, "test_distance", "modele_retenu", "distance", modele="corrigé"),
        ]
    )


def france(settings: Settings) -> pl.DataFrame:
    """Raw and corrected sensor in France, overall and by band."""
    rows = []
    for run, label in (("capteur_brut", "brut"), ("modele_retenu", "corrigé")):
        rows.append(_summary(settings, "transfert_france", run, modele=label, bande="toutes"))
        rows.append(
            _slices(settings, "transfert_france", run, "bande", modele=label).select(
                "modele", "bande", pl.exclude("modele", "bande")
            )
        )
    return pl.concat(rows, how="diagonal_relaxed")


def breakdowns(settings: Settings) -> dict[str, pl.DataFrame]:
    """The retained model's breakdowns, and France by station type."""
    out = {}
    for name in ("type_station", "methode", "saison", "humidite", "age"):
        out[f"decoupage_{name}"] = _slices(settings, "decoupages", "modele_retenu_5_plis", name)
    out["france_type_station"] = pl.concat(
        [
            _slices(settings, "transfert_france", "capteur_brut", "type_station", modele="brut"),
            _slices(settings, "transfert_france", "modele_retenu", "type_station", modele="corrigé"),
        ]
    )
    return out


def conformal(settings: Settings) -> pl.DataFrame:
    """Coverage and width of the 10-90 % interval before and after conformal calibration."""
    rows = []
    for rec in results.children(settings, "intervalle_conforme", SCOPE_FULL).iter_rows(named=True):
        for subset in ("saines", "toutes"):
            for nb in ("", "avec_voisins_", "sans_voisins_"):
                rows.append(
                    {
                        "calibration": rec["run"],
                        "paires": subset,
                        "voisins": nb.rstrip("_") or "toutes",
                        "couverture_avant": round(rec[f"avant_{subset}_{nb}coverage"], 3),
                        "couverture_apres": round(rec[f"apres_{subset}_{nb}coverage"], 3),
                        "largeur_mediane_avant": round(rec[f"avant_{subset}_{nb}width_median"], 2),
                        "largeur_mediane_apres": round(rec[f"apres_{subset}_{nb}width_median"], 2),
                    }
                )
    return pl.DataFrame(rows)


def health(settings: Settings) -> pl.DataFrame:
    """Health-score threshold chosen on 4 folds, measured on the 5th; then applied to France."""
    pairs = pl.read_parquet(settings.paths.gold / "pairs.parquet")
    per_fold, transfer = threshold_cv(
        pairs, np.round(np.linspace(0.0, 0.5, 51), 3), settings.study.main_country
    )
    table = per_fold.with_columns(
        pays=pl.lit(settings.study.main_country), pli=pl.col("fold").cast(pl.String)
    )
    france_row = pl.DataFrame(
        [{**transfer, "pays": settings.study.transfer_country, "pli": "seuil choisi sur DE"}]
    )
    return pl.concat([table.drop("fold"), france_row], how="diagonal_relaxed").select(
        "pays", "pli", pl.exclude("pays", "pli")
    )


def _predictions(settings: Settings, parent: str, run: str) -> pl.DataFrame:
    """Out-of-fold predictions stored by a full-volume run."""
    runs = results.children(settings, parent, SCOPE_FULL)
    run_id = runs.filter(pl.col("run") == run)["run_id"][0]
    return pl.read_parquet(results.artifact(settings, run_id, "oof_predictions.parquet"))


def per_pair(settings: Settings) -> pl.DataFrame:
    """Per-pair hourly R², slope and RMSE (quartiles over pairs): raw and corrected, Germany and France."""
    cfg = settings.evaluation
    corrected = {
        "DE ≤ 1,5 km (5 plis)": _predictions(settings, "modele_final", "final_reglages_optuna"),
        "FR (transfert)": _predictions(settings, "transfert_france", "modele_retenu"),
    }
    raw_sensor = pl.scan_parquet(settings.paths.gold / "pair_hours.parquet").select(
        "pair_id", "hour", "pm25_mean"
    )
    rows = []
    for zone, oof in corrected.items():
        raw = oof.drop("prediction").join(
            raw_sensor.collect().rename({"pm25_mean": "prediction"}), on=["pair_id", "hour"], how="left"
        )
        for subset, mask in (("saines", pl.col("sane_pair")), ("toutes", pl.lit(True))):
            for model, frame in (("brut", raw), ("corrigé", oof)):
                rows.append(
                    {
                        "zone": zone,
                        "paires": subset,
                        "modele": model,
                        **pair_distribution(frame.filter(mask), cfg),
                    }
                )
    return pl.DataFrame(rows)


def volumes(settings: Settings) -> tuple[pl.DataFrame, pl.DataFrame]:
    """Disk size of each data layer and number of rows of the main tables (real volumes for the README)."""
    data = settings.paths.data_dir
    rows = []
    for layer in ("bronze", "silver", "gold"):
        files = list((data / layer).rglob("*.parquet"))
        rows.append(
            {
                "couche": layer,
                "fichiers": len(files),
                "taille_go": round(sum(f.stat().st_size for f in files) / 1e9, 2),
            }
        )
    table = pl.DataFrame(rows)
    gold = pl.scan_parquet(settings.paths.gold / "pair_hours.parquet")
    counts = gold.group_by("country", "distance_band").agg(
        heures_paires=pl.len(), paires=pl.col("pair_id").n_unique(), stations=pl.col("cv_group").n_unique()
    )
    return table, counts.sort("country", "distance_band").collect()


def export(settings: Settings, out: Path | None = None) -> list[Path]:
    """Write every result table to ``reports/results/``; returns the files written."""
    out = out or settings.paths.reports_dir / "results"
    out.mkdir(parents=True, exist_ok=True)
    tables: dict[str, pl.DataFrame] = {
        "avant": before(settings),
        "echelle_de_modeles": ladder(settings),
        "ablation": ablation(settings),
        "attribution": attribution(settings),
        "temporel": temporal(settings),
        "temporel_age": temporal_age(settings),
        "distance": distance(settings),
        "france": france(settings),
        "intervalle_conforme": conformal(settings),
        "sante": health(settings),
        "r2_par_paire": per_pair(settings),
        **breakdowns(settings),
    }
    layers, gold_counts = volumes(settings)
    tables["volumes_couches"] = layers
    tables["volumes_gold"] = gold_counts
    written = []
    for name, table in tables.items():
        path = out / f"{name}.csv"
        table.write_csv(path, float_precision=3)
        written.append(path)
    log.info("exported %d result tables to %s", len(written), out)
    return written
