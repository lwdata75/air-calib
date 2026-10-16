"""Étape 3 : construire les variables du modèle et les plis par station.

    python scripts/03_construire_variables.py [--echantillon]

Entrée : data/propre/heures_paires.parquet. Sortie : data/variables/table.parquet (une ligne par
heure-paire : les 15 variables, la cible `reference`, le pli de la station, `paire_saine`).
"""

import argparse
import time

import polars as pl

from aircalib import parametres as P
from aircalib.controles import verifier_variables
from aircalib.variables import VARIABLES, construire


def principal(echantillon: bool) -> None:
    depart = time.time()
    racine = P.dossier_donnees(echantillon)
    heures = pl.read_parquet(racine / "propre" / "heures_paires.parquet")
    paires = pl.read_parquet(racine / "propre" / "paires.parquet")
    table = construire(heures, paires)
    verifier_variables(table, VARIABLES)
    (racine / "variables").mkdir(parents=True, exist_ok=True)
    table.write_parquet(racine / "variables" / "table.parquet")
    resume = table.group_by("pli").agg(stations=pl.col("station").n_unique(), heures=pl.len()).sort("pli")
    print(resume)
    part_voisins = table["a_des_voisins"].mean()
    print(f"{table.height:,} heures-paires, {len(VARIABLES)} colonnes de variables.")
    print(f"Médiane des voisins disponible {part_voisins:.0%} du temps.")
    print(f"Terminé en {time.time() - depart:.0f} s.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--echantillon", action="store_true")
    principal(parser.parse_args().echantillon)
