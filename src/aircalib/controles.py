"""Contrôles de cohérence des tables, avec des messages clairs quand quelque chose ne va pas."""

import polars as pl

from aircalib import parametres as P


def verifier_heures_paires(table: pl.DataFrame) -> None:
    """Vérifie la table des heures-paires après nettoyage (s'arrête au premier problème)."""
    attendues = {
        "capteur",
        "heure",
        "pm25",
        "pm10",
        "n_mesures",
        "reference",
        "humidite",
        "temperature",
        "station",
    }
    manquantes = attendues - set(table.columns)
    assert not manquantes, f"colonnes manquantes : {sorted(manquantes)}"
    assert table.select(pl.struct("capteur", "heure").is_unique().all()).item(), (
        "une heure apparaît deux fois pour un capteur"
    )
    assert table["reference"].null_count() == 0, "des heures n'ont pas de valeur de référence"
    assert (table["pm25"] >= 0).all(), "un PM2.5 négatif a passé le nettoyage"
    assert (table["pm25"] <= table["pm10"]).all(), "un PM2.5 dépasse le PM10"
    assert table["humidite"].is_between(0, 100).all(), "une humidité sort de [0, 100] %"
    assert table["heure"].dt.date().is_between(P.DEBUT, P.FIN).all(), "une heure sort de la période"


def verifier_variables(table: pl.DataFrame, variables: list[str]) -> None:
    """Vérifie la table des variables : toutes présentes, pas de doublon, un seul pli par station."""
    manquantes = set(variables) - set(table.columns)
    assert not manquantes, f"variables manquantes : {sorted(manquantes)}"
    assert table.select(pl.struct("capteur", "heure").is_unique().all()).item(), "doublon capteur × heure"
    plis_par_station = table.group_by("station").agg(pl.col("pli").n_unique())["pli"]
    assert (plis_par_station == 1).all(), "une station est répartie sur plusieurs plis (fuite d'information)"
