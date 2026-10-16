"""Tests des variables : pas de futur, des plis par station, un point de rosée juste."""

from datetime import datetime, timedelta

import polars as pl
import pytest

from aircalib import variables as V


def test_les_moyennes_glissantes_ne_regardent_pas_le_futur():
    heures = [datetime(2025, 1, 1) + timedelta(hours=i) for i in range(48)]
    table = pl.DataFrame({"capteur": 1, "heure": heures, "pm25": [float(i % 7) for i in range(48)]})
    coupure = heures[30]
    modifiee = table.with_columns(
        pl.when(pl.col("heure") > coupure).then(1e6).otherwise(pl.col("pm25")).alias("pm25")
    )
    avant = V.moyennes_passees(table).filter(pl.col("heure") <= coupure)
    apres = V.moyennes_passees(modifiee).filter(pl.col("heure") <= coupure)
    assert avant.equals(apres)  # changer le futur ne change rien jusqu'à la coupure


def test_une_station_est_toujours_dans_un_seul_pli():
    table = pl.DataFrame({"station": [f"S{i % 12}" for i in range(240)], "capteur": list(range(240))})
    plis = V.plis_par_station(table)
    assert plis["pli"].n_unique() == 5
    assert (plis.group_by("station").agg(pl.col("pli").n_unique())["pli"] == 1).all()


def test_point_de_rosee():
    table = pl.DataFrame({"t": [20.0, 20.0], "rh": [100.0, 50.0]})
    rosee = table.select(V.point_de_rosee(pl.col("t"), pl.col("rh")).alias("rosee"))["rosee"]
    assert rosee[0] == pytest.approx(20.0, abs=1e-6)  # air saturé : le point de rosée est la température
    assert rosee[1] == pytest.approx(9.3, abs=0.1)
