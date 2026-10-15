"""Stage ``features``: the gold layer, one row per usable pair-hour with the model's variables.

Built from the silver pair-hours. Dynamic features are computed on every hour where
the sensor is valid (before keeping the usable ones), so that a 24-hour mean also
sees the hours whose reference is missing; they only ever look at the past.

Outputs under ``<layers>/gold``:

* ``pair_hours.parquet``: usable pair-hours (``status = ok``), target, features, ``fold``;
* ``pairs.parquet``: one row per pair (station, band, fold, sane or not, health scores);
* ``physics_fits.parquet``: gain ``a`` and ``kappa`` fitted per fold, on training pairs only.

Training pairs = main country, near band, *sane* pairs (clock not "unverifiable": those
are evaluated apart). Folds group whole stations (``evaluation.folds``).
"""

from __future__ import annotations

import polars as pl

from aircalib.config import Settings
from aircalib.evaluation.folds import station_folds
from aircalib.ingest.bronze import write_parquet_atomic
from aircalib.ingest.plan import NEAR
from aircalib.logging import get_logger
from aircalib.schemas import gold as contracts
from aircalib.transform import clock
from aircalib.transform import features as ft
from aircalib.transform import particles as pt
from aircalib.transform.neighbours import health_scores, neighbour_links, neighbour_median
from aircalib.transform.physics import fit_gain_kappa

log = get_logger(__name__)

ALL_FOLDS = -1  # physics fit on every training pair, used outside the folds (France)


def _dynamic(hours: pl.DataFrame, settings: Settings) -> pl.DataFrame:
    """Past-only features on the pair's series: rolling PM and humidity, missing rates."""
    f = settings.features
    base = hours.sort("pair_id", "hour").with_columns(valid=pl.col("hour_flag") == pt.OK)
    missing = base.select("pair_id", "hour", *ft.missing_rate_past("valid", f.missing_window_hours))
    valid = base.filter("valid").select(
        "pair_id", "hour", pm25=pl.col("pm25_mean"), humidity=pl.col("official_humidity")
    )
    rolling = valid.with_columns(
        *ft.rolling_past("pm25", f.rolling_hours), *ft.rolling_past("humidity", f.rolling_hours)
    ).drop("pm25", "humidity")
    return missing.join(rolling, on=["pair_id", "hour"], how="left")


def _neighbours(
    hours: pl.DataFrame, segments: pl.DataFrame, settings: Settings
) -> tuple[pl.DataFrame, pl.DataFrame]:
    """Neighbour median per pair-hour and health scores per pair.

    The join pair-hour × neighbours is ~10^8 rows: pairs get integer ids and the
    median is computed month by month to bound memory.
    """
    f = settings.features
    ids = segments.select("pair_id").unique().sort("pair_id").with_row_index("pid")
    links = (
        neighbour_links(segments, f.neighbour_radius_km)
        .join(ids, on="pair_id")
        .join(ids.select(neighbour_id="pair_id", nid="pid"), on="neighbour_id")
        .select(pair_id="pid", neighbour_id="nid")
    )
    values = (
        hours.filter(pl.col("hour_flag") == pt.OK)
        .join(ids, on="pair_id")
        .select(
            pair_id="pid",
            hour="hour",
            value=pl.col("pm25_mean").cast(pl.Float32),
            month=pl.col("hour").dt.truncate("1mo"),
        )
    )
    parts = []
    for (_,), month in values.partition_by("month", as_dict=True, include_key=False).items():
        parts.append(neighbour_median(month, links, f.neighbour_min, f.log_floor_ugm3).collect())
    around = pl.concat(parts)
    health = health_scores(
        around.join(values.drop("month"), on=["pair_id", "hour"]), settings.health.min_hours, f.log_floor_ugm3
    )
    names = ids.select("pid", name="pair_id")
    around = around.join(names, left_on="pair_id", right_on="pid").drop("pair_id").rename({"name": "pair_id"})
    health = health.join(names, left_on="pair_id", right_on="pid").drop("pair_id").rename({"name": "pair_id"})
    log.info(
        "neighbours: %d links, %.1f %% of valid sensor hours with >= %d neighbours",
        links.height,
        100 * around["neighbour_median"].is_not_null().mean(),
        f.neighbour_min,
    )
    return around, health


def _folds(gold: pl.DataFrame, settings: Settings) -> pl.DataFrame:
    """Folds over the main country's stations, balanced on their usable hours."""
    station_hours = (
        gold.filter(pl.col("country") == settings.study.main_country).group_by("cv_group").agg(hours=pl.len())
    )
    return station_folds(station_hours, settings.cv.n_folds)


def _physics(gold: pl.DataFrame, settings: Settings) -> tuple[pl.DataFrame, pl.DataFrame]:
    """Out-of-fold physical estimate: each row uses ``a, kappa`` fitted without its station's fold."""
    cfg = settings.physics
    train = gold.filter("training_zone").drop_nulls(["log_pm25", "log_reference", "official_humidity"])
    fits = []
    for fold in [*range(settings.cv.n_folds), ALL_FOLDS]:
        part = train if fold == ALL_FOLDS else train.filter(pl.col("fold") != fold)
        fit = fit_gain_kappa(
            part["log_pm25"].to_numpy(),
            part["log_reference"].to_numpy(),
            part["official_humidity"].to_numpy(),
            cfg,
        )
        fits.append({"fold": fold, **fit.__dict__})
    table = pl.DataFrame(fits).with_columns(pl.col("fold").cast(pl.Int32))
    rows = gold.with_columns(physics_fold=pl.col("fold").fill_null(ALL_FOLDS)).join(
        table.select(physics_fold="fold", gain="gain", kappa="kappa"), on="physics_fold"
    )
    # kappa differs per fold: compute C row by row from the joined parameters.
    capped = pl.min_horizontal("official_humidity", pl.lit(cfg.rh_cap_pct)) / 100
    c = 1 + (pl.col("kappa") / cfg.density_ratio) / (1 / capped - 1)
    out = rows.with_columns(
        kohler_c=pl.when(pl.col("official_humidity") > 0).then(c),
    ).with_columns(physics_pm25=pl.col("gain") * pl.col("pm25_mean") / pl.col("kohler_c"))
    out = out.with_columns(
        log_physics_pm25=ft.log_floor(pl.col("physics_pm25"), settings.features.log_floor_ugm3)
    ).drop("gain", "kappa", "physics_fold")
    return out, table


def build(settings: Settings) -> dict[str, pl.DataFrame]:
    """Build and write the gold tables: usable pair-hours, features, folds, out-of-fold physics."""
    silver = settings.paths.silver
    f = settings.features
    hours = pl.read_parquet(silver / "pairs" / "pair_hours.parquet").with_columns(
        pl.col("status").cast(pl.String)
    )
    segments = pl.read_parquet(silver / "pairs" / "segment_pairs.parquet")
    registrations = pl.read_parquet(settings.paths.bronze / "reference" / "sensor_registrations.parquet")

    dynamic = _dynamic(hours, settings)
    around, health = _neighbours(hours, segments, settings)
    floor = f.log_floor_ugm3
    gold = (
        hours.filter(pl.col("status") == pt.OK)
        .join(dynamic, on=["pair_id", "hour"], how="left")
        .join(around, on=["pair_id", "hour"], how="left")
        .join(registrations.select("sensor_id", "registered_by"), on="sensor_id", how="left")
        .with_columns(
            *ft.time_features(pl.col("hour"), f.local_timezone),
            reference=pl.col("ref_pm25"),
            reference_pm10=pl.col("ref_pm10"),
            log_reference=ft.log_floor(pl.col("ref_pm25"), floor),
            log_pm25=ft.log_floor(pl.col("pm25_mean"), floor),
            pm25_pm10_ratio=pl.when(pl.col("pm10_mean") > 0).then(pl.col("pm25_mean") / pl.col("pm10_mean")),
            dew_point=ft.dew_point(pl.col("official_temperature"), pl.col("official_humidity"), f),
            age_years=(pl.col("hour").dt.date() - pl.col("registered_by")).dt.total_days().clip(lower_bound=0)
            / 365.25,
            # Sane = clock verified (or explained by the instrument); a pair never checked is not sane.
            sane_pair=pl.col("clock_flag").is_in([clock.OK, clock.INSTRUMENT_LAG]).fill_null(False),
        )
        .with_columns(
            training_zone=(pl.col("country") == settings.study.main_country)
            & (pl.col("distance_band") == NEAR)
            & pl.col("sane_pair"),
            dew_point_depression=pl.col("official_temperature") - pl.col("dew_point"),
        )
    )
    folds = _folds(gold, settings)
    gold = gold.join(folds, on="cv_group", how="left")
    gold, fits = _physics(gold, settings)
    gold = gold.select(contracts.COLUMNS).sort("pair_id", "hour")
    gold = gold.with_columns(pl.col(pl.Float64).cast(pl.Float32))  # model inputs: 7 digits are plenty
    contracts.GoldPairHours.validate(gold)

    pairs = (
        gold.group_by("pair_id")
        .agg(
            pl.col(
                "sensor_id",
                "cv_group",
                "fold",
                "country",
                "distance_band",
                "station_type",
                "method_class",
                "clock_flag",
                "sane_pair",
            ).first(),
            distance_km=pl.col("distance_km").first(),
            usable_hours=pl.len(),
            training_hours=pl.col("training_zone").sum(),
        )
        .join(health, on="pair_id", how="left")
        .sort("pair_id")
    )
    out = settings.paths.gold
    write_parquet_atomic(gold, out / "pair_hours.parquet")
    write_parquet_atomic(pairs, out / "pairs.parquet")
    write_parquet_atomic(fits, out / "physics_fits.parquet")
    all_fit = fits.filter(pl.col("fold") == ALL_FOLDS).row(0, named=True)
    log.info(
        "gold: %d pair-hours (%d training), %d pairs; physics on all training pairs: a = %.3f, kappa = %.4f",
        gold.height,
        gold["training_zone"].sum(),
        pairs.height,
        all_fit["gain"],
        all_fit["kappa"],
    )
    return {"hours": gold, "pairs": pairs, "fits": fits}
