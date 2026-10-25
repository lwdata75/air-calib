"""Les modèles, la validation croisée par station et le journal des essais.

Chaque modèle est une fonction `entrainer(apprentissage) -> predire`, où `predire(table)` renvoie un
tableau numpy de prédictions en µg/m³. La validation croisée apprend sur 4 plis de stations et prédit le
5ᵉ, cinq fois : chaque prédiction est faite par un modèle qui n'a jamais vu la station.
"""

import json
import os
import sys
import time
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

import numpy as np
import polars as pl
from sklearn.linear_model import LinearRegression

from aircalib import parametres as P
from aircalib.metriques import resume

Predire = Callable[[pl.DataFrame], np.ndarray]


def dossier_openmp() -> str:
    """Le dossier de la copie d'OpenMP livrée avec scikit-learn."""
    import sklearn

    return str(Path(sklearn.__file__).parent / ".dylibs")


def importer_lightgbm():
    """Importe LightGBM, qui a besoin de la bibliothèque OpenMP (absente de macOS sans Homebrew).

    Dans un script, si OpenMP manque, on relance le script en lui indiquant la copie de scikit-learn.
    Dans un notebook, ce n'est pas possible : il faut `brew install libomp`, ou lancer `make notebooks`.
    """
    try:
        import lightgbm
    except OSError as erreur:
        relance_possible = (
            sys.platform == "darwin" and sys.argv[0].endswith(".py") and "AIRCALIB_OPENMP" not in os.environ
        )
        if relance_possible:
            env = dict(os.environ, DYLD_FALLBACK_LIBRARY_PATH=dossier_openmp(), AIRCALIB_OPENMP="1")
            os.execve(sys.executable, [sys.executable, *sys.argv], env)
        raise RuntimeError(
            "LightGBM ne trouve pas OpenMP : installez-le (brew install libomp) ou lancez make notebooks."
        ) from erreur
    return lightgbm


def matrice(table: pl.DataFrame, variables: list[str]) -> np.ndarray:
    """Les variables en tableau numpy (les manquants restent NaN, LightGBM sait les gérer)."""
    return table.select(pl.col(variables).cast(pl.Float64)).to_numpy()


# --- Les modèles --------------------------------------------------------------------------------


def capteur_brut(apprentissage: pl.DataFrame) -> Predire:
    """Aucune correction : la prédiction est la mesure du capteur."""
    return lambda table: table["pm25"].to_numpy()


def regression_lineaire(
    apprentissage: pl.DataFrame, variables: tuple = ("pm25", "humidite", "temperature")
) -> Predire:
    """La calibration classique : référence ≈ a + b·PM brut + c·humidité + d·température."""
    variables = list(variables)
    modele = LinearRegression().fit(matrice(apprentissage, variables), apprentissage["reference"].to_numpy())
    return lambda table: modele.predict(matrice(table, variables))


def lightgbm(
    variables: list[str], reglages: dict | None = None, arbres: int = 100
) -> Callable[[pl.DataFrame], Predire]:
    """LightGBM sur les variables choisies ; sans `reglages`, les valeurs par défaut de la bibliothèque."""
    lgb = importer_lightgbm()
    parametres = {"objective": "regression", "verbose": -1, "seed": P.GRAINE, **(reglages or {})}

    def entrainer(apprentissage: pl.DataFrame) -> Predire:
        donnees = lgb.Dataset(matrice(apprentissage, variables), apprentissage["reference"].to_numpy())
        booster = lgb.train(parametres, donnees, num_boost_round=arbres)
        entrainer.importance = dict(zip(variables, booster.feature_importance("gain"), strict=True))
        return lambda table: booster.predict(matrice(table, variables))

    return entrainer


# --- Validation croisée par station ---------------------------------------------------------------


def validation_croisee(
    table: pl.DataFrame,
    entrainer: Callable[[pl.DataFrame], Predire],
    apprendre_sur: pl.Expr | None = None,
    tester_sur: pl.Expr | None = None,
) -> pl.DataFrame:
    """Prédictions « hors pli » : chaque station est prédite par le modèle appris sur les 4 autres plis.

    On n'apprend que sur les paires saines ; on prédit toutes les paires du pli (saines et défectueuses).
    `apprendre_sur` et `tester_sur` restreignent en plus les heures (par exemple : apprendre sur 2024,
    tester sur 2025, notebook 06).
    """
    apprendre_sur = pl.lit(True) if apprendre_sur is None else apprendre_sur
    tester_sur = pl.lit(True) if tester_sur is None else tester_sur
    morceaux, importances = [], []
    for pli in range(P.N_PLIS):
        apprentissage = table.filter((pl.col("pli") != pli) & pl.col("paire_saine") & apprendre_sur)
        test = table.filter((pl.col("pli") == pli) & tester_sur)
        if apprentissage.is_empty() or test.is_empty():
            continue
        predire = entrainer(apprentissage)
        morceaux.append(test.select(COLONNES_GARDEES).with_columns(prediction=pl.Series(predire(test))))
        if hasattr(entrainer, "importance"):
            importances.append(entrainer.importance)
    predictions = pl.concat(morceaux)
    if importances:  # gain moyen sur les plis, en part du total
        moyenne = {v: float(np.mean([imp[v] for imp in importances])) for v in importances[0]}
        total = sum(moyenne.values()) or 1.0
        validation_croisee.importance = {
            v: g / total for v, g in sorted(moyenne.items(), key=lambda x: -x[1])
        }
    return predictions


def combiner(avec_voisins: pl.DataFrame, sans_voisins: pl.DataFrame) -> pl.DataFrame:
    """Le modèle retenu : LightGBM complet quand le capteur a des voisins, sinon LightGBM sans voisins.

    Le notebook 03 a montré que, pour les heures sans voisins, le modèle appris sans ces variables fait
    mieux. On sait à l'avance si une heure a des voisins : le choix n'utilise jamais la référence.
    """
    autre = sans_voisins.select("capteur", "heure", prediction_sans="prediction")
    return (
        avec_voisins.join(autre, on=["capteur", "heure"])
        .with_columns(
            prediction=pl.when(pl.col("a_des_voisins"))
            .then(pl.col("prediction"))
            .otherwise(pl.col("prediction_sans"))
        )
        .drop("prediction_sans")
    )


COLONNES_GARDEES = [
    "capteur",
    "heure",
    "station",
    "pli",
    "reference",
    "paire_saine",
    "a_des_voisins",
    "distance_km",
    "type_station",
    "pm25",
]


def echantillon_de_stations(table: pl.DataFrame, part: float = P.PART_ECHANTILLON_STATIONS) -> pl.DataFrame:
    """Un échantillon de stations (toujours le même, graine fixe), avec toutes leurs heures.

    On tire des stations entières, pas des heures : sinon une même station serait des deux côtés.
    """
    stations = table.select("station").unique().sort("station").sample(fraction=part, seed=P.GRAINE)
    return table.filter(pl.col("station").is_in(stations["station"].implode()))


# --- Journal des essais ---------------------------------------------------------------------------


def noter_essai(
    nom: str,
    perimetre: str,
    variables: list[str],
    reglages: dict,
    predictions: pl.DataFrame,
    duree_s: float,
    journal: Path = P.RAPPORTS / "runs.csv",
) -> dict:
    """Ajoute une ligne au journal des essais : quand, quel modèle, avec quels réglages, quels résultats.

    Les chiffres sont calculés sur les paires saines (celles qu'on sait juger) ; on note aussi le RMSE
    des heures sans voisins, notre point faible connu.
    """
    saines = predictions.filter(pl.col("paire_saine"))
    chiffres = resume(saines)
    sans_voisins = resume(saines.filter(~pl.col("a_des_voisins")))
    ligne = {
        "date": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "perimetre": perimetre,
        "modele": nom,
        "variables": ",".join(variables),
        "reglages": json.dumps(reglages, ensure_ascii=False),
        "heures_test": chiffres["heures"],
        "rmse": round(chiffres["rmse"], 3),
        "mae": round(chiffres["mae"], 3),
        "r2_median_par_paire": round(chiffres["r2_median_par_paire"], 3),
        "pct_epa_horaire": round(chiffres["pct_epa_horaire"], 1),
        "pct_epa_journalier": round(chiffres["pct_epa_journalier"], 1),
        "rmse_sans_voisins": round(sans_voisins["rmse"], 3) if sans_voisins["heures"] else None,
        "duree_s": round(duree_s, 1),
    }
    nouvelle = pl.DataFrame([ligne])
    if journal.exists():
        nouvelle = pl.concat(
            [pl.read_csv(journal, infer_schema_length=None), nouvelle], how="diagonal_relaxed"
        )
    nouvelle.write_csv(journal)
    return ligne


def evaluer(
    nom: str,
    perimetre: str,
    table: pl.DataFrame,
    entrainer: Callable[[pl.DataFrame], Predire],
    variables: list[str],
    reglages: dict,
    journal: Path = P.RAPPORTS / "runs.csv",
) -> pl.DataFrame:
    """Validation croisée + note dans le journal ; renvoie les prédictions hors pli."""
    depart = time.time()
    predictions = validation_croisee(table, entrainer)
    noter_essai(nom, perimetre, variables, reglages, predictions, time.time() - depart, journal)
    return predictions
