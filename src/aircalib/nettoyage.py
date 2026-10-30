"""Les règles de nettoyage, une fonction par règle.

On n'applique que des règles physiques ou de cohérence, jamais de filtre statistique : un pic de
pollution par temps humide est justement ce que le modèle doit apprendre à corriger.
Chaque règle renvoie les lignes gardées ; le script 02 compte ce que chacune retire (l'entonnoir).
"""

from datetime import timedelta

import polars as pl

from aircalib import parametres as P
from aircalib.geo import distance_km

# --- a. Valeurs impossibles du SDS011 -----------------------------------------------------------


def retirer_valeurs_impossibles(mesures: pl.DataFrame) -> dict[str, pl.DataFrame]:
    """Applique les règles physiques dans l'ordre ; renvoie la table restante après chaque règle.

    - vide : PM2.5 ou PM10 absent (ou illisible) ;
    - code d'erreur : une valeur négative (le capteur écrit −1/−1 quand il est en panne) ;
    - plafond : 999,9 en PM2.5 ou 1 999,9 en PM10, la valeur maximale du capteur ;
    - incohérent : PM2.5 > PM10, impossible puisque le PM2.5 fait partie du PM10.
    """
    etapes = {}
    etapes["vide"] = mesures = mesures.drop_nulls(["pm25", "pm10"]).filter(
        ~pl.col("pm25").is_nan(), ~pl.col("pm10").is_nan()
    )
    etapes["code d'erreur (négatif)"] = mesures = mesures.filter(pl.col("pm25") >= 0, pl.col("pm10") >= 0)
    etapes["plafond du capteur"] = mesures = mesures.filter(
        pl.col("pm25") < P.PM25_PLAFOND, pl.col("pm10") < P.PM10_PLAFOND
    )
    etapes["PM2.5 > PM10"] = mesures.filter(pl.col("pm25") <= pl.col("pm10"))
    return etapes


# --- b. Capteur figé ----------------------------------------------------------------------------


def retirer_plateaux_de_panne(mesures: pl.DataFrame) -> pl.DataFrame:
    """Retire les plateaux 0/0 d'au moins une heure : le capteur ne voit plus rien.

    Une petite valeur répétée (0,3 µg/m³) peut être de l'air très propre ; le code 0/0 répété pendant
    une heure, non. On ne retire donc que ce code de panne.
    """
    mesures = mesures.sort("horodatage")
    change = (pl.col("pm25") != pl.col("pm25").shift()) | (pl.col("pm10") != pl.col("pm10").shift())
    mesures = mesures.with_columns(plateau=change.fill_null(True).cum_sum())
    duree = (pl.col("horodatage").max() - pl.col("horodatage").min()).over("plateau")
    en_panne = (pl.col("pm25") == 0) & (pl.col("pm10") == 0) & (duree >= timedelta(hours=P.PLATEAU_MIN_H))
    return mesures.filter(~en_panne).drop("plateau")


# --- c. Moyennes horaires -----------------------------------------------------------------------


def cadence_s(mesures: pl.DataFrame) -> float:
    """Intervalle habituel entre deux mesures du capteur (médiane, en ignorant les coupures > 1 h)."""
    ecarts = mesures["horodatage"].sort().diff().dt.total_seconds().drop_nulls()
    ecarts = ecarts.filter(ecarts < 3600)
    return float(ecarts.median()) if ecarts.len() else float("nan")


def moyennes_horaires(mesures: pl.DataFrame, cadence: float) -> pl.DataFrame:
    """Moyenne horaire (heure UTC, étiquetée par son début), gardée si elle a ≥ 75 % des mesures attendues.

    Les capteurs n'envoient pas tous au même rythme (de 1 à 5 minutes) : on compare le nombre de
    mesures à celui qu'on attend avec la cadence du capteur, plutôt qu'à un nombre fixe.
    """
    attendues = 3600 / cadence
    return (
        mesures.group_by(heure=pl.col("horodatage").dt.truncate("1h"))
        .agg(pm25=pl.col("pm25").mean(), pm10=pl.col("pm10").mean(), n_mesures=pl.len())
        .filter(pl.col("n_mesures") >= P.COMPLETUDE_MIN * attendues)
        .sort("heure")
    )


# --- d et e. Référence EEA, avec la correction d'horloge ----------------------------------------


def nettoyer_reference(eea: pl.DataFrame) -> pl.DataFrame:
    """Heures valides et vérifiées de la station, avec l'heure remise en UTC.

    L'EEA étiquette l'heure 13 h–14 h UTC « Start = 14:00 » : on retire une heure (notebook 01).
    Les valeurs manquantes sont codées −999 : ce sont des manquants, pas des concentrations.
    """
    return eea.filter(
        pl.col("validite").is_in(P.EEA_VALIDITES),
        pl.col("verification") == 1,
        pl.col("valeur") != P.EEA_MANQUANT,
    ).select(
        "point",
        heure=pl.col("debut") - pl.duration(hours=P.EEA_DECALAGE_H),
        reference=pl.col("valeur"),
    )


def correlations_decalees(paire: pl.DataFrame, decalages: range = range(-3, 4)) -> pl.DataFrame:
    """Corrélation entre les variations horaires du capteur et celles de la station décalée de k heures.

    `paire` contient les colonnes heure, pm25 (capteur) et reference. On travaille sur les variations
    d'une heure à l'autre : les niveaux de pollution changent trop lentement pour révéler un décalage.
    """
    paire = paire.sort("heure")
    capteur = paire.select("heure", dc=pl.col("pm25").diff()).filter(
        pl.col("heure").diff() == timedelta(hours=1)
    )
    lignes = []
    for k in decalages:
        station = paire.select(
            heure=pl.col("heure") - pl.duration(hours=k), ds=pl.col("reference").diff()
        ).filter(pl.col("heure").diff() == timedelta(hours=1))
        commun = capteur.join(station, on="heure").drop_nulls()
        lignes.append(
            {
                "decalage_h": k,
                "correlation": commun.select(pl.corr("dc", "ds")).item(),
                "heures": commun.height,
            }
        )
    return pl.DataFrame(lignes)


# --- f. Capteurs déplacés ------------------------------------------------------------------------


def a_demenage(mesures: pl.DataFrame, lat: float, lon: float) -> bool:
    """Vrai si le capteur a été à plus de 1 km de sa position actuelle pendant la période.

    Règle volontairement simple : plutôt que de découper un capteur déménagé en morceaux, on l'écarte.
    """
    positions = mesures.select("lat", "lon").drop_nulls().unique()
    if positions.is_empty():
        return False
    distances = positions.select(d=distance_km(pl.col("lat"), pl.col("lon"), pl.lit(lat), pl.lit(lon)))
    return bool(distances["d"].max() > P.DEPLACEMENT_MAX_KM)


# --- g. Paires défectueuses ---------------------------------------------------------------------


def controler_paire(heures: pl.DataFrame) -> dict:
    """Corrélation des variations horaires capteur / station, à décalage nul (horloge déjà corrigée).

    Un capteur en bon état monte et descend avec sa station. S'il ne la suit pas (corrélation < 0,1)
    ou si on a trop peu d'heures pour le vérifier, la paire est jugée défectueuse : on ne l'utilise pas
    pour apprendre, mais on la garde pour évaluer.
    """
    resultat = correlations_decalees(heures, range(0, 1)).row(0, named=True)
    saine = (
        resultat["heures"] >= P.HEURES_MIN_CONTROLE and (resultat["correlation"] or 0) >= P.CORRELATION_MIN
    )
    return {
        "correlation_variations": resultat["correlation"],
        "heures_controle": resultat["heures"],
        "paire_saine": saine,
    }
