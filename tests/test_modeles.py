"""Test du modèle combiné : il choisit le bon modèle selon la présence de voisins."""

from datetime import datetime

import polars as pl

from aircalib.modeles import combiner


def test_combiner_prend_le_modele_sans_voisins_quand_il_n_y_en_a_pas():
    heures = [datetime(2025, 1, 1, h) for h in range(3)]
    base = pl.DataFrame({"capteur": [1, 1, 1], "heure": heures, "a_des_voisins": [True, False, True]})
    avec = base.with_columns(prediction=pl.lit(10.0))
    sans = base.with_columns(prediction=pl.lit(20.0))
    assert combiner(avec, sans)["prediction"].to_list() == [10.0, 20.0, 10.0]
