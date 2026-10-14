"""Funnel report (``reports/funnel.md``), computed from the per-row statuses of the silver layer.

Each table follows the rules in their order of application: for every rule, the
rows it removes and the entities (sensors, pairs, stations...) that lose their
last row at that step. Regenerated at every ``pair`` run.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import polars as pl

from aircalib.config import Settings
from aircalib.ingest.report import _table, folder_bytes
from aircalib.silver.rules import HOUR_RULES
from aircalib.transform import humidity as hum
from aircalib.transform import particles as pt
from aircalib.transform import reference as ref
from aircalib.transform import weather as wx
from aircalib.transform.funnel import OK, funnel

# Why each rule exists, in French for the report and the notebooks (one map per table:
# the same status name can mean different things in two sources).
COMMON = {"start": "point de départ", "usable": "**utilisable**"}
PARTICLE_REASONS = {
    pt.PM_MISSING: "PM10 ou PM2.5 vide / NaN",
    pt.DUPLICATE: "doublon exact (même horodatage, mêmes valeurs) : la 1re copie est gardée",
    pt.DUPLICATE_CONFLICT: "même horodatage, valeurs différentes : impossible de choisir",
    pt.PM_NEGATIVE: "valeur négative (code d'erreur −1 du firmware)",
    pt.PM_SATURATED: "saturation du capteur (PM2.5 = 999,9 avec PM10 = 1 999,9)",
    pt.PM_ABOVE_RANGE: "au-delà de la plage du SDS011 (999,9 µg/m³)",
    pt.PM25_ABOVE_PM10: "PM2.5 > PM10 : incohérent (PM2.5 fait partie de PM10)",
    pt.PM_STUCK: "valeurs identiques pendant ≥ 1 h (`particles.stuck_min_hours`) : capteur figé",
}
PROBE_REASONS = {
    hum.MISSING: "humidité vide / NaN",
    hum.OUT_OF_RANGE: "humidité < 0 ou > 100 %",
    hum.STUCK_LOW: "humidité figée basse (< 5 %)",
    hum.SATURATED: "humidité saturée (≥ 99,9 %)",
    hum.STUCK: "humidité identique pendant ≥ 3 h (`humidity.stuck_min_hours`)",
    "dead_month": "mois-sonde « mort » (> 30 % d'heures en défaut, `humidity.dead_month_share`)",
    "faulty_hour": "heure en défaut (majorité des mesures non utilisables) dans un mois sain",
}
REFERENCE_REASONS = {
    ref.INVALID: "drapeau de validité refusé ou sentinelle −999",
    ref.NOT_VERIFIED: "heure non vérifiée par l'agence",
    ref.NEGATIVE: "valeur < −5 µg/m³ (`eea.negative_floor_ugm3`) : au-delà du bruit autour de 0",
}
WEATHER_REASONS = {
    wx.MISSING: "humidité officielle absente (dont −999 DWD)",
    wx.DOUBTFUL: "code qualité Météo-France « douteux » (2)",
    wx.OUT_OF_RANGE: "humidité officielle hors 0–100 %",
}
HOUR_REASONS = {
    pt.NO_VALID_READING: "aucune mesure valide dans l'heure (raison dominante dans `main_flag`)",
    pt.INCOMPLETE_HOUR: "complétude < 75 % de la cadence propre du capteur",
    "no_reference_station": "segment de position sans station utilisable à ≤ 3 km",
    "reference_not_downloaded": "station de référence non téléchargée",
    "reference_missing": "pas de valeur EEA PM2.5 à cette heure",
    "reference_invalid": "valeur EEA PM2.5 invalide (drapeau ou −999)",
    "reference_not_verified": "valeur EEA PM2.5 non vérifiée",
    "reference_negative_beyond_uncertainty": "valeur EEA PM2.5 < −5 µg/m³",
    "clock_mismatch_station": "station exclue : son décalage d'horloge (toutes paires) ≠ 0",
    "clock_mismatch": "paire exclue : son meilleur décalage d'horloge ≠ 0",
    "no_official_humidity": "aucune humidité officielle (ni station principale, ni secours)",
}


def _explain(table: pl.DataFrame, reasons: dict[str, str]) -> pl.DataFrame:
    mapping = {**COMMON, **reasons}
    return table.with_columns(raison=pl.col("step").replace_strict(mapping, default=pl.col("step"))).select(
        "step", "raison", pl.exclude("step", "raison")
    )


def particle_funnel(settings: Settings) -> pl.DataFrame:
    readings = pl.scan_parquet(settings.paths.silver / "sensor_community" / "pm" / "*.parquet").with_columns(
        pl.col(pt.FLAG).cast(pl.String)
    )
    return funnel(
        readings, pt.FLAG, list(pt.RULES), {"capteurs": "sensor_id", "segments": ["sensor_id", "segment"]}
    )


def humidity_funnels(settings: Settings) -> tuple[pl.DataFrame, pl.DataFrame]:
    sc = settings.paths.silver / "sensor_community"
    counts = pl.read_parquet(sc / "humidity_status.parquet").with_columns(
        status=pl.when(pl.col("status") == hum.USABLE).then(pl.lit(OK)).otherwise(pl.col("status"))
    )
    rules = [hum.MISSING, hum.OUT_OF_RANGE, hum.STUCK_LOW, hum.SATURATED, hum.STUCK]
    readings = funnel(counts, "status", rules, {"sondes": "sensor_id"}, weight="n")
    hourly = pl.read_parquet(sc / "humidity_hourly.parquet").with_columns(
        status=pl.when(pl.col("month_status") == hum.DEAD)
        .then(pl.lit("dead_month"))
        .when(pl.col("faulty") | pl.col("humidity").is_null())
        .then(pl.lit("faulty_hour"))
        .otherwise(pl.lit(OK))
    )
    hours = funnel(hourly, "status", ["dead_month", "faulty_hour"], {"sondes": "sensor_id"})
    return readings, hours


def reference_funnel(settings: Settings) -> pl.DataFrame:
    eea = pl.read_parquet(settings.paths.silver / "eea" / "hourly.parquet")
    rules = [r for r in ref.RULES if r != ref.OUTSIDE_PERIOD]
    tables = [
        funnel(
            eea.filter(pl.col("pollutant") == p), "quality_flag", rules, {"points": "sampling_point"}
        ).with_columns(polluant=pl.lit(p))
        for p in ("pm25", "pm10")
    ]
    return pl.concat(tables)


def weather_funnel(settings: Settings) -> pl.DataFrame:
    weather = pl.read_parquet(settings.paths.silver / "weather" / "hourly.parquet")
    return funnel(weather, "humidity_flag", list(wx.RULES), {"stations": ["source", "station_id"]})


def pair_funnel(hours: pl.DataFrame) -> pl.DataFrame:
    return funnel(
        hours,
        "status",
        HOUR_RULES,
        {"capteurs": "sensor_id", "paires": "pair_id", "stations": "station_code"},
    )


def usable_by(hours: pl.DataFrame, by: list[str]) -> pl.DataFrame:
    usable = hours.filter(pl.col("status") == OK)
    return (
        usable.group_by(by)
        .agg(
            heures_paires=pl.len(),
            paires=pl.col("pair_id").n_unique(),
            capteurs=pl.col("sensor_id").n_unique(),
            stations=pl.col("station_code").n_unique(),
            pct_humidité_locale_fiable=(100 * pl.col("local_reliable").mean()).round(1),
            pct_secours_météo=(100 * pl.col("weather_fallback").mean()).round(2),
        )
        .sort(by)
    )


def build(settings: Settings) -> tuple[str, dict[str, pl.DataFrame]]:
    pairs_dir = settings.paths.silver / "pairs"
    hours = pl.read_parquet(pairs_dir / "pair_hours.parquet")
    clock_check = pl.read_parquet(pairs_dir / "clock_check.parquet")
    clock_station = pl.read_parquet(pairs_dir / "clock_check_station.parquet")
    tables = {
        "particles": _explain(particle_funnel(settings), PARTICLE_REASONS),
        "pairs": _explain(pair_funnel(hours), HOUR_REASONS),
        "reference": _explain(reference_funnel(settings), REFERENCE_REASONS),
        "weather": _explain(weather_funnel(settings), WEATHER_REASONS),
    }
    humidity_readings, humidity_hours = humidity_funnels(settings)
    tables["humidity_readings"] = _explain(humidity_readings, PROBE_REASONS)
    tables["humidity_hours"] = _explain(humidity_hours, PROBE_REASONS)
    tables["usable_country_band"] = usable_by(hours, ["country", "distance_band"])
    tables["usable_station_type"] = usable_by(hours, ["country", "station_type"])
    tables["clock"] = (
        clock_check.group_by("clock_flag", "best_lag_h")
        .agg(
            paires=pl.len(), heures=pl.col("n_hours").sum(), r_lag0_médian=pl.col("r_lag0").median().round(3)
        )
        .sort("clock_flag", "best_lag_h")
    )
    tables["clock_station_summary"] = (
        clock_station.group_by("clock_flag")
        .agg(stations=pl.len(), heures=pl.col("n_hours").sum())
        .sort("clock_flag")
    )
    method = (
        pl.read_parquet(settings.paths.meta / "eea_stations_pm25.parquet")
        .select("station_code", "measurement_method")
        .unique("station_code")
    )
    tables["clock_station"] = (
        clock_station.filter(pl.col("clock_flag") == "clock_mismatch")
        .join(method, on="station_code", how="left")
        .sort("station_code")
    )
    stamp = datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC")
    mode = "échantillon (`--sample`)" if settings.paths.sample else "complet"
    disk = pl.DataFrame(
        {
            "couche": ["bronze", "silver"],
            "go": [folder_bytes(settings.paths.bronze) / 1e9, folder_bytes(settings.paths.silver) / 1e9],
        }
    )
    text = (
        f"# Rapport d'entonnoir — mode {mode}\n\n"
        f"_Généré automatiquement le {stamp} à partir des statuts de la couche silver "
        "(aucune ligne supprimée : chaque mesure ou heure garde sa raison)._\n\n"
        "## 1. Mesures SDS011 (une ligne = une mesure brute)\n\n"
        + _table(tables["particles"])
        + "\n## 2. Paires × heures (une ligne = une heure d'un segment de capteur)\n\n"
        + _table(tables["pairs"])
        + "\n### Heures-paires utilisables par pays et bande de distance\n\n"
        + _table(tables["usable_country_band"])
        + "\n### Heures-paires utilisables par pays et type de station\n\n"
        + _table(tables["usable_station_type"])
        + "\n### Contrôle d'horloge, par paire (variations horaires, verdict selon `clock.*`)\n\n"
        + _table(tables["clock"])
        + "\n### Contrôle d'horloge, par station (paires regroupées)\n\n"
        + _table(tables["clock_station_summary"])
        + "\nStations en décalage (toutes leurs paires sont exclues) :\n\n"
        + _table(tables["clock_station"])
        + "\n## 3. Référence EEA (une ligne = une heure d'un point de mesure)\n\n"
        + _table(tables["reference"])
        + "\n## 4. Humidité officielle (une ligne = une heure d'une station météo)\n\n"
        + _table(tables["weather"])
        + "\n## 5. Humidité des sondes (feature secondaire)\n\n### Mesures\n\n"
        + _table(tables["humidity_readings"])
        + "\n### Heures-sondes\n\n"
        + _table(tables["humidity_hours"])
        + "\n## Espace disque\n\n"
        + _table(disk)
    )
    return text, tables


def write(settings: Settings) -> Path:
    text, _ = build(settings)
    name = "funnel_sample.md" if settings.paths.sample else "funnel.md"
    dest = settings.paths.reports_dir / name
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(text, encoding="utf-8")
    return dest
