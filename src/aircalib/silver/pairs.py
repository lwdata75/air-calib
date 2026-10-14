"""Stage ``pair`` (silver, part 2): pair every position segment with a station and join the sources.

1. **Segments → stations.** A sensor that moved is several *segments*; each segment is
   paired on its own with the nearest usable station within ``pairing.radius_download_km``
   (same rule as step 1). A moved sensor can change station or lose it. A pair is
   ``(sensor, segment) → station``; ``cv_group`` (the station code) is the group of the
   station-wise cross-validation: a station is never in training and test at once.
2. **Official weather.** Among the two official stations kept for the EEA station, the
   one nearest to the *sensor* is the main one (tie within ``weather.tie_km`` broken by
   humidity completeness); an hour missing there falls back to the other station.
3. **Pair-hours.** Every sensor-hour gets the reference PM2.5 and PM10, the official
   humidity and temperature, the local probe humidity when it is reliable, and a
   ``status``: ``ok`` or the first rule it fails (``HOUR_RULES`` order = funnel order).
4. **Clock check** on every pair and every station (``aircalib.transform.clock``): a pair
   whose best lag is significantly not 0, or whose station's is, is excluded.
"""

from __future__ import annotations

import polars as pl

from aircalib.config import Settings
from aircalib.ingest.bronze import write_parquet_atomic
from aircalib.ingest.plan import FAR, NEAR, Plan, reference_dir
from aircalib.logging import get_logger
from aircalib.schemas import silver as contracts
from aircalib.silver.rules import (
    NO_OFFICIAL_HUMIDITY,
    NO_STATION,
    NOT_DOWNLOADED,
    REFERENCE_MISSING,
    STATION_MISMATCH,
)
from aircalib.silver.sources import silver_dir
from aircalib.transform import clock
from aircalib.transform import particles as pt
from aircalib.transform import reference as ref
from aircalib.transform import weather as wx
from aircalib.transform.pairing import nearest_usable_station, weather_links

log = get_logger(__name__)

KEYS = ["sensor_id", "segment"]


def pair_id(frame: pl.DataFrame) -> pl.DataFrame:
    return frame.with_columns(pair_id=pl.format("{}-{}", "sensor_id", "segment"))


# ---------------------------------------------------------------- segments → stations
def segment_pairs(settings: Settings, plan: Plan, segments: pl.DataFrame, eea: pl.DataFrame) -> pl.DataFrame:
    """One row per segment with its station (or the reason it has none) and its distance band."""
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


# ---------------------------------------------------------------------- weather choice
def weather_choice(settings: Settings, pairs: pl.DataFrame, weather: pl.DataFrame) -> pl.DataFrame:
    """Official stations of each pair ranked by ``choice`` (1 = main, 2 = hourly fallback)."""
    ref_dir = reference_dir(settings)
    tables = [
        pl.read_parquet(path)
        .select("sampling_point", "station_id", "rank", "lat", "lon", "distance_km")
        .with_columns(source=pl.lit(source))
        for source, path in (
            (wx.DWD, ref_dir / "weather_stations_dwd.parquet"),
            (wx.METEO_FRANCE, ref_dir / "weather_stations_meteo_france.parquet"),
        )
        if path.exists()
    ]
    paired = pairs.filter(pl.col("pair_status") == pt.OK).select(
        "pair_id", "sampling_point", sensor_lat="lat", sensor_lon="lon"
    )
    links = weather_links(paired, pl.concat(tables), pair_id="pair_id")
    completeness = (
        weather.filter(pl.col("humidity_flag") == wx.OK)
        .group_by("source", weather_station_id="station_id")
        .agg(humidity_hours=pl.len())
    )
    chosen = wx.choose_stations(
        links.select("pair_id", "source", "weather_station_id", "sensor_weather_km", "weather_rank"),
        completeness,
        settings.weather.tie_km,
    )
    switched = chosen.filter((pl.col("choice") == 1) & (pl.col("weather_rank") != 1)).height
    log.info(
        "weather: %d pairs; main station is not the EEA station's nearest for %d", paired.height, switched
    )
    return chosen


# --------------------------------------------------------------------------- joins
def _reference(eea: pl.DataFrame) -> tuple[pl.DataFrame, pl.DataFrame]:
    """PM2.5 by sampling point; PM10 by station (its point with the most valid hours)."""
    pm25 = eea.filter(pl.col("pollutant") == "pm25").select(
        "sampling_point", "hour", ref_pm25="value", ref_pm25_flag="quality_flag"
    )
    pm10 = eea.filter(pl.col("pollutant") == "pm10")
    return pm25, pm10


def _pm10_by_station(settings: Settings, pm10: pl.DataFrame) -> pl.DataFrame:
    points = pl.read_parquet(reference_dir(settings) / "eea_sampling_points.parquet").filter(
        pl.col("pollutant") == "pm10"
    )
    best = (
        pm10.filter(pl.col("quality_flag") == ref.OK)
        .group_by("sampling_point")
        .agg(n=pl.len())
        .join(points.select("sampling_point", "station_code"), on="sampling_point")
        .sort("n", "sampling_point", descending=[True, False])
        .unique("station_code", keep="first")
    )
    return pm10.join(best.select("sampling_point", "station_code"), on="sampling_point").select(
        "station_code", "hour", ref_pm10="value", ref_pm10_flag="quality_flag"
    )


def _official(chosen: pl.DataFrame, weather: pl.DataFrame) -> pl.DataFrame:
    """Per pair and hour: official humidity/temperature from the main station, else the fallback."""
    hours = weather.select(
        "source",
        weather_station_id="station_id",
        hour="hour",
        rh="humidity",
        t="temperature",
        flag="humidity_flag",
    )
    per_choice = []
    for choice in (1, 2):
        stations = chosen.filter(pl.col("choice") == choice).select(
            "pair_id", "source", "weather_station_id", "sensor_weather_km"
        )
        per_choice.append(
            stations.join(hours, on=["source", "weather_station_id"]).select(
                "pair_id",
                "hour",
                *[
                    pl.col(c).alias(f"{c}_{choice}")
                    for c in ("source", "weather_station_id", "sensor_weather_km", "rh", "t", "flag")
                ],
            )
        )
    both = per_choice[0].join(per_choice[1], on=["pair_id", "hour"], how="full", coalesce=True)
    main_ok = (pl.col("flag_1") == wx.OK).fill_null(False)
    fallback_ok = (pl.col("flag_2") == wx.OK).fill_null(False)

    def pick(column: str) -> pl.Expr:
        return pl.when(main_ok).then(pl.col(f"{column}_1")).when(fallback_ok).then(pl.col(f"{column}_2"))

    return both.select(
        "pair_id",
        "hour",
        official_humidity=pick("rh"),
        official_temperature=pick("t"),
        weather_source=pick("source"),
        weather_station_id=pick("weather_station_id"),
        sensor_weather_km=pick("sensor_weather_km"),
        weather_fallback=~main_ok & fallback_ok,
    )


def _local(settings: Settings, probes: pl.DataFrame) -> pl.DataFrame:
    """Per site and hour, the reliable probe value (by ``humidity.probe_preference``)."""
    preference = {name: i for i, name in enumerate(settings.humidity.probe_preference)}
    return (
        probes.filter(pl.col("reliable"))
        .with_columns(_pref=pl.col("sensor_type").replace_strict(preference, default=len(preference)))
        .sort("location", "hour", "_pref", "sensor_id")
        .unique(["location", "hour"], keep="first")
        .select(
            "location",
            "hour",
            local_humidity="humidity",
            local_temperature="temperature",
            local_probe_id="sensor_id",
            local_probe_type="sensor_type",
        )
    )


def _status() -> pl.Expr:
    reference_flag = pl.col("ref_pm25_flag")
    return (
        pl.when(pl.col("hour_flag") != pt.OK)
        .then(pl.col("hour_flag"))
        .when(pl.col("pair_status") != pt.OK)
        .then(pl.col("pair_status"))
        .when(reference_flag.is_null())
        .then(pl.lit(REFERENCE_MISSING))
        .when(reference_flag != ref.OK)
        .then(pl.lit("reference_") + reference_flag)
        .when(pl.col("station_clock_flag") == clock.MISMATCH)
        .then(pl.lit(STATION_MISMATCH))
        .when(pl.col("clock_flag") == clock.MISMATCH)
        .then(pl.lit(clock.MISMATCH))
        .when(pl.col("official_humidity").is_null())
        .then(pl.lit(NO_OFFICIAL_HUMIDITY))
        .otherwise(pl.lit(pt.OK))
    )


def build(settings: Settings, plan: Plan) -> dict[str, pl.DataFrame]:
    silver = silver_dir(settings)
    sc = silver / "sensor_community"
    pm_hourly = pl.read_parquet(sc / "pm_hourly.parquet")
    segments = pl.read_parquet(sc / "segments.parquet")
    probes = pl.read_parquet(sc / "humidity_hourly.parquet")
    eea = pl.read_parquet(silver / "eea" / "hourly.parquet")
    weather = pl.read_parquet(silver / "weather" / "hourly.parquet")

    pairs = segment_pairs(settings, plan, segments, eea)
    chosen = weather_choice(settings, pairs, weather)
    pm25, pm10 = _reference(eea)
    pair_cols = [
        "pair_id",
        "location",
        "sampling_point",
        "station_code",
        "cv_group",
        "country",
        "station_type",
        "station_area",
        "distance_km",
        "distance_band",
        "pair_status",
    ]
    hours = (
        pm_hourly.join(pairs.select(*KEYS, *pair_cols), on=KEYS, how="left")
        .join(pm25, on=["sampling_point", "hour"], how="left")
        .join(_pm10_by_station(settings, pm10), on=["station_code", "hour"], how="left")
        .join(_official(chosen, weather), on=["pair_id", "hour"], how="left")
        .join(_local(settings, probes), on=["location", "hour"], how="left")
        .with_columns(local_reliable=pl.col("local_humidity").is_not_null())
    )

    # Clock check on the hours where both sides are valid.
    checkable = hours.filter(
        (pl.col("hour_flag") == pt.OK)
        & (pl.col("pair_status") == pt.OK)
        & (pl.col("ref_pm25_flag") == ref.OK)
    ).select("pair_id", "station_code", "hour", sensor="pm25_mean", reference="ref_pm25")
    changes = clock.hourly_changes(checkable)
    cfg = settings.clock
    correlations = clock.lag_correlations(changes, cfg.max_lag_h)
    clock_flags = clock.clock_status(correlations, cfg.min_hours, cfg.min_r, cfg.min_z)
    station_correlations = clock.lag_correlations(changes, cfg.max_lag_h, group="station_code")
    station_flags = clock.clock_status(
        station_correlations, cfg.min_hours, cfg.min_r, cfg.min_z, group="station_code"
    )
    hours = (
        hours.join(clock_flags.select("pair_id", "clock_flag"), on="pair_id", how="left")
        .join(
            station_flags.select("station_code", station_clock_flag="clock_flag"),
            on="station_code",
            how="left",
        )
        .with_columns(status=_status())
        .sort(*KEYS, "hour")
    )

    out = silver / "pairs"
    write_parquet_atomic(pairs, out / "segment_pairs.parquet")
    write_parquet_atomic(chosen, out / "weather_choice.parquet")
    write_parquet_atomic(correlations, out / "clock_correlations.parquet")
    write_parquet_atomic(clock_flags, out / "clock_check.parquet")
    write_parquet_atomic(station_correlations, out / "clock_station_correlations.parquet")
    write_parquet_atomic(station_flags, out / "clock_check_station.parquet")
    contracts.PairHours.validate(hours)
    write_parquet_atomic(hours, out / "pair_hours.parquet")
    log.info(
        "pair-hours: %d, usable %d; clock per pair: %s; per station: %s",
        hours.height,
        hours.filter(pl.col("status") == pt.OK).height,
        dict(clock_flags["clock_flag"].value_counts().iter_rows()),
        dict(station_flags["clock_flag"].value_counts().iter_rows()),
    )
    return {
        "pairs": pairs,
        "weather": chosen,
        "clock": clock_flags,
        "clock_station": station_flags,
        "hours": hours,
    }
