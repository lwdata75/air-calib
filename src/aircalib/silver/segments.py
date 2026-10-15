"""Position segments and their stations (silver).

A sensor that moved is several *segments* (``transform.positions``); each segment is
paired on its own with the nearest usable station within ``pairing.radius_download_km``
(same rule as step 1): a moved sensor can change station or lose it. A pair is
``(sensor, segment) → station`` (``pair_id``); ``cv_group`` (the station code) is the group
of the station-wise cross-validation. The station's measurement method (TEOM, beta,
optical...) is attached as a feature and a breakdown of the results.

Segments are built *before* the particle readings are flagged, because the stuck-value
rule looks at the paired station.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

import polars as pl

from aircalib.config import Settings
from aircalib.ingest.bronze import write_parquet_atomic
from aircalib.ingest.plan import FAR, NEAR, Plan
from aircalib.logging import get_logger
from aircalib.silver.rules import NO_STATION, NOT_DOWNLOADED
from aircalib.transform import particles as pt
from aircalib.transform.pairing import nearest_usable_station
from aircalib.transform.positions import POSITION, assign_segments, segment_table

log = get_logger(__name__)

KEYS = ["sensor_id", "segment"]
WORKERS = 4


def pair_id(frame: pl.DataFrame) -> pl.DataFrame:
    """Pair identifier ``<sensor>-<segment>``."""
    return frame.with_columns(pair_id=pl.format("{}-{}", "sensor_id", "segment"))


def sorted_readings(frame: pl.DataFrame) -> pl.DataFrame:
    """Readings in the canonical order used everywhere (stable: file order breaks timestamp ties)."""
    return frame.sort(*pt.KEYS, maintain_order=True)


def _sensor_segments(settings: Settings, sensor_id: int) -> pl.DataFrame | None:
    """Position segments of one sensor, from its bronze readings."""
    files = sorted((settings.paths.bronze / "sensor_community" / "pm" / str(sensor_id)).glob("*.parquet"))
    if not files:
        return None
    positions = pl.read_parquet(files, columns=["sensor_id", "timestamp", *POSITION])
    return segment_table(assign_segments(sorted_readings(positions))).collect()


def method_classes(settings: Settings, stations: pl.DataFrame) -> pl.DataFrame:
    """Measurement method of every station: raw EEA fields and the instrument family."""
    cfg = settings.eea
    return (
        stations.select("sampling_point", "measurement_method", "measurement_equipment")
        .unique("sampling_point")
        .with_columns(
            method_class=pl.col("measurement_method").replace_strict(
                cfg.method_classes, default=cfg.method_unknown, return_dtype=pl.String
            )
        )
    )


def segment_pairs(settings: Settings, plan: Plan, segments: pl.DataFrame, eea: pl.DataFrame) -> pl.DataFrame:
    """One row per segment with its station (or the reason it has none), distance band and method."""
    meta = settings.paths.meta
    stations = pl.read_parquet(meta / "eea_stations_pm25.parquet")
    availability = pl.read_parquet(meta / "eea_pm25_monthly_valid_hours.parquet")
    points = pair_id(segments).with_columns(
        months=pl.date_ranges(
            pl.col("start").dt.date().dt.truncate("1mo"), pl.col("end").dt.date().dt.truncate("1mo"), "1mo"
        )
    )
    nearest = nearest_usable_station(
        points.select("pair_id", "lat", "lon", "months"),
        stations,
        availability,
        settings.pairing.radius_download_km,
        point_id="pair_id",
    ).select(
        "pair_id",
        "sampling_point",
        "station_code",
        "country",
        "station_type",
        "station_area",
        "distance_km",
        "n_stations_within",
    )
    downloaded = eea.filter(pl.col("pollutant") == "pm25")["sampling_point"].unique().implode()
    sensor_country = plan.pairs.select("sensor_id", sensor_country="country")
    out = (
        points.drop("months")
        .join(nearest, on="pair_id", how="left")
        .join(method_classes(settings, stations), on="sampling_point", how="left")
        .join(sensor_country, on="sensor_id", how="left")
        .with_columns(
            country=pl.coalesce("country", "sensor_country"),
            pair_status=pl.when(pl.col("sampling_point").is_null())
            .then(pl.lit(NO_STATION))
            .when(~pl.col("sampling_point").is_in(downloaded))
            .then(pl.lit(NOT_DOWNLOADED))
            .otherwise(pl.lit(pt.OK)),
            distance_band=pl.when(pl.col("distance_km") <= settings.pairing.radius_train_km)
            .then(pl.lit(NEAR))
            .when(pl.col("distance_km").is_not_null())
            .then(pl.lit(FAR)),
            cv_group=pl.col("station_code"),
        )
        .drop("sensor_country")
        .sort(KEYS)
    )
    planned = plan.pairs.select("sensor_id", planned_point="sampling_point")
    changed = out.join(planned, on="sensor_id").filter(
        pl.col("sampling_point").is_not_null() & (pl.col("sampling_point") != pl.col("planned_point"))
    )
    log.info(
        "segments: %d (%d sensors moved), %d without station, %d paired with another station than planned",
        out.height,
        out.filter(pl.col("segment") > 1)["sensor_id"].n_unique(),
        out.filter(pl.col("pair_status") != pt.OK).height,
        changed.height,
    )
    return out


def build(settings: Settings, plan: Plan, eea: pl.DataFrame) -> pl.DataFrame:
    """Segments of every planned sensor and their stations; written to silver."""
    ids = plan.pairs["sensor_id"].to_list()
    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        tables = [t for t in pool.map(lambda s: _sensor_segments(settings, s), ids) if t is not None]
    segments = pl.concat(tables)
    pairs = segment_pairs(settings, plan, segments, eea)
    silver = settings.paths.silver
    write_parquet_atomic(segments, silver / "sensor_community" / "segments.parquet")
    write_parquet_atomic(pairs, silver / "pairs" / "segment_pairs.parquet")
    return pairs
