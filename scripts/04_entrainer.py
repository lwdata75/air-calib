"""Étape 4 : entraîner les modèles retenus sur toutes les stations (5 plis par station).

    python scripts/04_entrainer.py [--echantillon]

Le notebook 03 a comparé les modèles sur 20 % des stations et choisi les réglages ; ce script refait les
mêmes calculs sur toutes les stations, pour obtenir les chiffres définitifs. Pour chaque modèle :
  - les prédictions hors pli sont sauvegardées dans data/predictions/<modele>.parquet ;
  - une ligne est ajoutée au journal reports/runs.csv.
Le modèle retenu (5_lightgbm_combine) combine les modèles 2 et 4 selon que le capteur a des voisins ou non.
Le tableau de résultats (reports/resultats.csv) et l'importance des variables (reports/importance.csv)
alimentent le notebook 04 et le README.
"""

import argparse
import time

import polars as pl

from aircalib import modeles as M
from aircalib import parametres as P
from aircalib.metriques import resume
from aircalib.variables import FAMILLES, VARIABLES

# Le notebook 03 a comparé, sur 20 % des stations, les réglages par défaut de LightGBM à deux réglages
# manuels : aucun n'a fait clairement mieux (quelques paires d'écart sur 60, et un RMSE un peu moins bon).
# On garde donc les réglages par défaut, les plus simples. Le réglage 1 est refait ici sur toutes les
# stations, pour vérifier ce choix.
REGLAGE_1 = {"learning_rate": 0.05}
ARBRES_REGLAGE_1 = 500
SANS_VOISINS = [v for v in VARIABLES if v not in FAMILLES["voisins"]]


def les_modeles() -> dict:
    """Nom → (fonction d'entraînement, variables, réglages), dans l'ordre de l'échelle."""
    return {
        "0_capteur_brut": (M.capteur_brut, ["pm25"], {}),
        "1_regression_lineaire": (M.regression_lineaire, ["pm25", "humidite", "temperature"], {}),
        "2_lightgbm": (M.lightgbm(VARIABLES), VARIABLES, {"arbres": 100}),
        "3_lightgbm_reglage_1": (
            M.lightgbm(VARIABLES, REGLAGE_1, ARBRES_REGLAGE_1),
            VARIABLES,
            REGLAGE_1 | {"arbres": ARBRES_REGLAGE_1},
        ),
        "4_lightgbm_sans_voisins": (M.lightgbm(SANS_VOISINS), SANS_VOISINS, {"arbres": 100}),
    }


def tableau_de_resultats(predictions: dict[str, pl.DataFrame]) -> pl.DataFrame:
    """Les chiffres de chaque modèle : toutes heures, avec / sans voisins, par bande de distance."""
    bandes = P.BANDES_DISTANCE_KM
    lignes = []
    for nom, pred in predictions.items():
        pred = pred.with_columns(
            bande=pl.when(pl.col("distance_km") < bandes[0])
            .then(pl.lit("0-0,5 km"))
            .when(pl.col("distance_km") < bandes[1])
            .then(pl.lit("0,5-1 km"))
            .otherwise(pl.lit("1-1,5 km"))
        )
        tranches = {
            ("toutes", "toutes heures"): pl.lit(True),
            ("saines", "toutes heures"): pl.col("paire_saine"),
            ("saines", "avec voisins"): pl.col("paire_saine") & pl.col("a_des_voisins"),
            ("saines", "sans voisins"): pl.col("paire_saine") & ~pl.col("a_des_voisins"),
            **{
                ("saines", f"bande {b}"): pl.col("paire_saine") & (pl.col("bande") == b)
                for b in ["0-0,5 km", "0,5-1 km", "1-1,5 km"]
            },
        }
        for (paires, heures), filtre in tranches.items():
            chiffres = resume(pred.filter(filtre))
            lignes.append({"modele": nom, "paires": paires, "tranche": heures, **chiffres})
    return pl.DataFrame(lignes).with_columns(pl.col(pl.Float64).round(3))


def principal(echantillon: bool) -> None:
    racine = P.dossier_donnees(echantillon)
    table = pl.read_parquet(racine / "variables" / "table.parquet")
    dossier = racine / "predictions"
    dossier.mkdir(parents=True, exist_ok=True)
    perimetre = "échantillon de test" if echantillon else "toutes les stations"
    journal = racine / "runs.csv" if echantillon else P.RAPPORTS / "runs.csv"
    predictions = {}
    for nom, (entrainer, variables, reglages) in les_modeles().items():
        depart = time.time()
        pred = M.evaluer(nom, perimetre, table, entrainer, variables, reglages, journal)
        pred.write_parquet(dossier / f"{nom}.parquet")
        predictions[nom] = pred
        if nom == "2_lightgbm":
            importance = pl.DataFrame(
                list(M.validation_croisee.importance.items()),
                schema=["variable", "part_du_gain"],
                orient="row",
            )
            if not echantillon:
                importance.write_csv(P.RAPPORTS / "importance.csv")
        print(f"{nom} : {time.time() - depart:.0f} s", flush=True)
    nom = "5_lightgbm_combine"
    pred = M.combiner(predictions["2_lightgbm"], predictions["4_lightgbm_sans_voisins"])
    M.noter_essai(nom, perimetre, VARIABLES, {"sans voisins": "4_lightgbm_sans_voisins"}, pred, 0.0, journal)
    pred.write_parquet(dossier / f"{nom}.parquet")
    predictions[nom] = pred
    resultats = tableau_de_resultats(predictions)
    if not echantillon:
        resultats.write_csv(P.RAPPORTS / "resultats.csv")
    print(
        resultats.filter(pl.col("tranche") == "toutes heures").select(
            "modele", "paires", "rmse", "r2_median_par_paire", "pct_epa_horaire", "pct_epa_journalier"
        )
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--echantillon", action="store_true")
    principal(parser.parse_args().echantillon)
