"""Les variables du modèle : jamais la référence, jamais le futur."""

import numpy as np
import polars as pl
from sklearn.model_selection import GroupKFold

from aircalib import parametres as P
from aircalib.geo import distance_km

# 14 variables (17 colonnes : l'heure, le mois et le jour sont codés sur deux colonnes), par famille.
# Le dictionnaire sert aussi pour l'ablation. Rien sur la station (type, distance) : là où l'on veut
# corriger un capteur, il n'y a pas de station (notebook 03).
FAMILLES = {
    "capteur": ["pm25", "pm10", "rapport_pm25_pm10", "n_mesures"],
    "meteo": ["humidite", "temperature", "point_de_rosee"],
    "temps": ["heure_sin", "heure_cos", "mois_sin", "mois_cos", "jour_sin", "jour_cos"],
    "passe": ["pm25_moy_3h", "pm25_moy_24h"],
    "voisins": ["mediane_voisins", "ecart_voisins"],
}
VARIABLES = [v for liste in FAMILLES.values() for v in liste]


def point_de_rosee(temperature: pl.Expr, humidite: pl.Expr) -> pl.Expr:
    """Température à laquelle l'air serait saturé (formule de Magnus, en °C)."""
    a, b = 17.62, 243.12
    gamma = (humidite / 100).log() + a * temperature / (b + temperature)
    return b * gamma / (a - gamma)


def variables_du_temps(heure_utc: pl.Expr) -> list[pl.Expr]:
    """Heure, mois et jour de la semaine en heure locale, codés en sinus et cosinus.

    Le codage circulaire place 23 h juste à côté de 0 h, et décembre à côté de janvier.
    """
    locale = heure_utc.dt.replace_time_zone("UTC").dt.convert_time_zone(P.FUSEAU_LOCAL)
    cycles = {
        "heure": (locale.dt.hour(), 24),
        "mois": (locale.dt.month() - 1, 12),
        "jour": (locale.dt.weekday() - 1, 7),
    }
    expressions = []
    for nom, (valeur, periode) in cycles.items():
        angle = 2 * np.pi * valeur / periode
        expressions += [angle.sin().alias(f"{nom}_sin"), angle.cos().alias(f"{nom}_cos")]
    return expressions


def moyennes_passees(table: pl.DataFrame) -> pl.DataFrame:
    """Moyenne du PM2.5 brut sur les 3 et 24 dernières heures du même capteur (heure courante comprise).

    La fenêtre (h − w, h] ne contient que le présent et le passé : aucune heure future ne peut s'y glisser.
    """
    table = table.sort("capteur", "heure")
    return table.with_columns(
        [
            pl.col("pm25")
            .rolling_mean_by("heure", window_size=f"{w}h")
            .over("capteur")
            .alias(f"pm25_moy_{w}h")
            for w in P.FENETRES_H
        ]
    )


def mediane_des_voisins(table: pl.DataFrame, mesures: pl.DataFrame, positions: pl.DataFrame) -> pl.DataFrame:
    """Médiane du PM2.5 des autres capteurs à 3 km ou moins, à la même heure (s'ils sont au moins 3).

    `mesures` donne le PM2.5 horaire de tous les capteurs disponibles (capteur, heure, pm25) et `positions`
    leur position. Les voisins mesurent le même air, avec le même type de capteur : c'est une information
    que la station ne donne pas. On ne regarde jamais la référence.
    """
    apparies = table.select("capteur").unique().join(positions, on="capteur")
    liens = (
        apparies.join(positions.select(voisin="capteur", lat_v="lat", lon_v="lon"), how="cross")
        .filter(pl.col("capteur") != pl.col("voisin"))
        .filter(
            distance_km(pl.col("lat"), pl.col("lon"), pl.col("lat_v"), pl.col("lon_v")) <= P.VOISINS_RAYON_KM
        )
        .select("capteur", "voisin")
    )
    mediane = (
        liens.join(mesures.select(voisin="capteur", heure="heure", pm25_voisin="pm25"), on="voisin")
        .group_by("capteur", "heure")
        .agg(mediane_voisins=pl.col("pm25_voisin").median(), n_voisins=pl.len())
        .filter(pl.col("n_voisins") >= P.VOISINS_MIN)
    )
    return table.join(mediane.drop("n_voisins"), on=["capteur", "heure"], how="left").with_columns(
        ecart_voisins=pl.col("pm25").clip(lower_bound=1).log()
        - pl.col("mediane_voisins").clip(lower_bound=1).log()
    )


def plis_par_station(table: pl.DataFrame) -> pl.DataFrame:
    """Répartit les stations en 5 plis : une station et tous ses capteurs sont toujours dans le même pli.

    Sans ça, le modèle pourrait apprendre sur une station et être testé sur la même : il la « reconnaîtrait »
    et paraîtrait meilleur qu'il ne l'est (une fuite d'information).
    """
    stations = table.group_by("station").agg(heures=pl.len()).sort("station")
    plis = np.empty(stations.height, dtype=int)
    for pli, (_, test) in enumerate(
        GroupKFold(n_splits=P.N_PLIS).split(stations, groups=stations["station"])
    ):
        plis[test] = pli
    return table.join(stations.select("station").with_columns(pli=pl.Series(plis)), on="station")


def construire(heures: pl.DataFrame, mesures: pl.DataFrame, positions: pl.DataFrame) -> pl.DataFrame:
    """Toutes les variables, la cible (`reference`) et le pli de chaque heure-paire."""
    table = heures.with_columns(
        variables_du_temps(pl.col("heure")),
        rapport_pm25_pm10=pl.col("pm25") / pl.col("pm10").clip(lower_bound=0.1),
        point_de_rosee=point_de_rosee(pl.col("temperature"), pl.col("humidite").clip(lower_bound=1)),
    )
    table = moyennes_passees(table)
    table = mediane_des_voisins(table, mesures, positions)
    return plis_par_station(table).with_columns(a_des_voisins=pl.col("mediane_voisins").is_not_null())
