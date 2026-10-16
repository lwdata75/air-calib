"""Tests des règles de nettoyage, sur de petits exemples écrits à la main."""

from datetime import datetime, timedelta

import numpy as np
import polars as pl

from aircalib import nettoyage as N

T0 = datetime(2025, 3, 1)


def mesures(valeurs: list[tuple[float, float]], pas_s: int = 150) -> pl.DataFrame:
    """Une suite de mesures (pm25, pm10) espacées de `pas_s` secondes, au même endroit."""
    return pl.DataFrame(
        {
            "horodatage": [T0 + timedelta(seconds=i * pas_s) for i in range(len(valeurs))],
            "pm25": pl.Series([v[0] for v in valeurs], dtype=pl.Float64),
            "pm10": pl.Series([v[1] for v in valeurs], dtype=pl.Float64),
            "lat": 52.0,
            "lon": 13.0,
        }
    )


def test_horloge_eea_une_heure_en_moins_et_manquants_retires():
    eea = pl.DataFrame(
        {
            "point": "P",
            "debut": [
                datetime(2025, 3, 1, 14),
                datetime(2025, 3, 1, 15),
                datetime(2025, 3, 1, 16),
                datetime(2025, 3, 1, 17),
            ],
            "valeur": [8.0, -999.0, 9.0, 10.0],
            "validite": [1, -1, 1, 1],
            "verification": [1, 1, 3, 1],
        }
    )
    propre = N.nettoyer_reference(eea)
    # 14:00 « EEA » correspond à l'heure 13:00–14:00 UTC ; −999 et l'heure non vérifiée disparaissent.
    assert propre["heure"].to_list() == [datetime(2025, 3, 1, 13), datetime(2025, 3, 1, 16)]
    assert propre["reference"].to_list() == [8.0, 10.0]


def test_codes_erreur_plafond_et_incoherences_retires_mais_pas_les_pics():
    table = mesures([(5, 8), (-1, -1), (999.9, 1999.9), (12, 10), (None, 4), (850, 900)])
    etapes = N.retirer_valeurs_impossibles(table)
    reste = list(etapes.values())[-1]
    assert reste["pm25"].to_list() == [5, 850]  # un pic de 850 µg/m³ est plausible : on le garde


def test_seul_le_plateau_0_0_d_une_heure_est_retire():
    panne = [(0, 0)] * 30  # 30 mesures × 150 s ≈ 1 h 12 min
    court = [(0, 0)] * 10  # 25 min seulement
    air_propre = [(0.3, 0.5)] * 40  # 0,3 µg/m³ répété 1 h 40 : de l'air très propre, pas une panne
    table = mesures(panne + [(5, 7)] + court + [(6, 8)] + air_propre)
    reste = N.retirer_plateaux_de_panne(table)
    assert reste.height == 1 + 10 + 1 + 40


def test_completude_selon_la_cadence_du_capteur():
    rapide = mesures([(5, 7)] * 18, pas_s=150)  # 18 mesures sur 24 attendues : 75 %, gardée
    trop_peu = mesures([(5, 7)] * 17, pas_s=150).with_columns(pl.col("horodatage") + timedelta(hours=1))
    heures = N.moyennes_horaires(pl.concat([rapide, trop_peu]), cadence=150)
    assert heures["n_mesures"].to_list() == [18]
    lent = mesures([(5, 7)] * 12, pas_s=300)  # un capteur lent : 12 mesures sur 12 attendues
    assert N.moyennes_horaires(lent, cadence=N.cadence_s(lent)).height == 1


def test_capteur_demenage_au_dela_de_1_km():
    table = mesures([(5, 7)] * 4)
    assert not N.a_demenage(table, 52.003, 13.0)  # environ 330 m
    assert N.a_demenage(table, 52.02, 13.0)  # environ 2,2 km


def serie(reference: np.ndarray, capteur: np.ndarray) -> pl.DataFrame:
    heures = [T0 + timedelta(hours=i) for i in range(len(reference))]
    return pl.DataFrame({"heure": heures, "reference": reference, "pm25": capteur})


def test_le_decalage_d_horloge_est_retrouve():
    rng = np.random.default_rng(0)
    station = rng.lognormal(2, 0.5, 800)
    capteur = 0.5 * np.roll(station, -1) + rng.normal(0, 0.2, 800)  # le capteur « voit » l'heure suivante
    correlations = N.correlations_decalees(serie(station, capteur))
    assert correlations.sort("correlation", descending=True)["decalage_h"][0] == 1


def test_paire_saine_ou_defectueuse():
    rng = np.random.default_rng(1)
    station = rng.lognormal(2, 0.5, 800)
    assert N.controler_paire(serie(station, 0.5 * station + rng.normal(0, 0.3, 800)))["paire_saine"]
    assert not N.controler_paire(serie(station, rng.lognormal(2, 0.5, 800)))["paire_saine"]  # ne suit pas
    assert not N.controler_paire(serie(station[:100], station[:100]))["paire_saine"]  # trop court pour juger
