"""Test des cibles EPA sur un cas où l'on connaît la réponse."""

from datetime import datetime, timedelta

import numpy as np
import polars as pl
import pytest

from aircalib.metriques import resume


def test_pourcentage_de_paires_epa_calcule_a_la_main():
    rng = np.random.default_rng(0)
    heures = [datetime(2025, 1, 1) + timedelta(hours=i) for i in range(24 * 40)]  # 960 heures, 40 jours
    reference = rng.lognormal(2, 0.5, len(heures))
    paires = {
        1: reference,  # parfaite : passe tout
        2: reference + rng.normal(0, 1, len(heures)),  # un peu de bruit : passe encore
        3: 0.5 * reference,  # pente de 0,5 : échoue (cible 0,65–1,35)
        4: reference + 20,  # biais de 20 µg/m³ : échoue (ordonnée hors de ±5)
    }
    table = pl.concat(
        [
            pl.DataFrame({"capteur": c, "heure": heures, "reference": reference, "prediction": pred})
            for c, pred in paires.items()
        ]
    )
    chiffres = resume(table)
    assert chiffres["paires_notees"] == 4
    assert chiffres["pct_epa_horaire"] == pytest.approx(50.0)  # 2 paires sur 4
    assert chiffres["pct_epa_journalier"] == pytest.approx(50.0)
    assert chiffres["r2_median_par_paire"] > 0.9  # la corrélation reste parfaite pour les paires 3 et 4
