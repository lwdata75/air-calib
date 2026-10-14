"""Stage ``silver`` (part 1): clean each source and aggregate it to UTC hours.

Nothing is deleted: every reading or hour keeps its row with a ``quality_flag``
(``ok`` or the first rule it failed). The funnel report is computed from these
flags (``aircalib.silver.report``).

Outputs under ``<layers>/silver``:

* ``sensor_community/pm/<sensor_id>.parquet``: every SDS011 reading, its position
  ``segment`` and its ``quality_flag``;
* ``sensor_community/pm_hourly.parquet``: per sensor, segment and hour: mean, median,
  standard deviation and number of valid readings, completeness, ``hour_flag``;
* ``sensor_community/segments.parquet``: position segments of every sensor;
* ``sensor_community/humidity_hourly.parquet`` and ``humidity_status.parquet``: probe
  hours with their reliability, and reading counts per status;
* ``eea/hourly.parquet``: reference hours (PM2.5 and PM10) on UTC with their flag;
* ``weather/hourly.parquet``: official weather hours with their humidity flag.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import polars as pl

from aircalib.config import Settings
from aircalib.ingest.bronze import write_parquet_atomic
from aircalib.ingest.plan import Plan
from aircalib.logging import get_logger
from aircalib.schemas import silver as contracts
from aircalib.transform import humidity as hum
from aircalib.transform import particles as pt
from aircalib.transform import reference as ref
from aircalib.transform import weather as wx
from aircalib.transform.positions import assign_segments, segment_table

log = get_logger(__name__)

PM_COLUMNS = ["sensor_id", "segment", "timestamp", "P1", "P2", pt.FLAG]
SEGMENT_KEYS = ["sensor_id", "segment"]
WORKERS = 4  # sensors processed in parallel (Polars itself is multi-threaded)


def silver_dir(settings: Settings) -> Path:
    path = settings.paths.silver
    path.mkdir(parents=True, exist_ok=True)
    return path


def _bronze_files(settings: Settings, kind: str, sensor_id: int) -> list[Path]:
    return sorted((settings.paths.bronze / "sensor_community" / kind / str(sensor_id)).glob("*.parquet"))


# ------------------------------------------------------------------------ particles
def clean_pm_sensor(settings: Settings, sensor_id: int) -> tuple[pl.DataFrame, pl.DataFrame] | None:
    """Flag one sensor's readings, cut them into segments, write them; return (hourly, segments)."""
    files = _bronze_files(settings, "pm", sensor_id)
    if not files:
        return None
    readings = pl.read_parquet(files)
    flagged = assign_segments(pt.flag_readings(readings, settings.particles))
    dest = settings.paths.silver / "sensor_community" / "pm" / f"{sensor_id}.parquet"
    contracts.PmReadings.validate(flagged.select(PM_COLUMNS))
    write_parquet_atomic(flagged.select(PM_COLUMNS).with_columns(pl.col(pt.FLAG).cast(pl.Categorical)), dest)
    hourly = pt.hourly_particles(flagged, settings.particles, SEGMENT_KEYS).collect()
    return hourly, segment_table(flagged).collect()


def clean_particles(settings: Settings, plan: Plan) -> tuple[pl.DataFrame, pl.DataFrame]:
    ids = plan.pairs["sensor_id"].to_list()
    hourly, segments = [], []
    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        for i, result in enumerate(pool.map(lambda s: clean_pm_sensor(settings, s), ids), 1):
            if result is not None:
                hourly.append(result[0])
                segments.append(result[1])
            if i % 200 == 0:
                log.info("particles: %d/%d sensors", i, len(ids))
    out = silver_dir(settings) / "sensor_community"
    pm_hourly, segment_frame = pl.concat(hourly), pl.concat(segments)
    contracts.PmHourly.validate(pm_hourly)
    write_parquet_atomic(pm_hourly, out / "pm_hourly.parquet")
    write_parquet_atomic(segment_frame, out / "segments.parquet")
    log.info(
        "particles: %d sensors, %d segments, %d sensor-hours (%d ok)",
        segment_frame["sensor_id"].n_unique(),
        segment_frame.height,
        pm_hourly.height,
        pm_hourly.filter(pl.col("hour_flag") == pt.OK).height,
    )
    return pm_hourly, segment_frame


# ------------------------------------------------------------------------- humidity
def clean_probe(settings: Settings, sensor_id: int) -> tuple[pl.DataFrame, pl.DataFrame] | None:
    """Hourly humidity of one probe per site (``location``) and its reading counts per status."""
    files = _bronze_files(settings, "humidity", sensor_id)
    if not files:
        return None
    readings = hum.with_status(pl.scan_parquet(files), settings.humidity).collect()
    by = ("sensor_id", "sensor_type", "location")
    hourly = hum.hourly_humidity(readings, settings.humidity, by=by).collect()
    counts = readings.group_by("sensor_id", "sensor_type", "status").agg(n=pl.len())
    return hourly, counts


def clean_humidity(settings: Settings, plan: Plan) -> pl.DataFrame:
    """Probe hours with ``reliable`` = usable value, healthy month (not "dead"), not a faulty hour."""
    ids = plan.humidity["sensor_id"].to_list()
    hourly, counts = [], []
    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        for result in pool.map(lambda s: clean_probe(settings, s), ids):
            if result is not None:
                hourly.append(result[0])
                counts.append(result[1])
    probe_hourly = pl.concat(hourly).with_columns(pl.col("sensor_type").str.to_lowercase())
    monthly = hum.classify_months(hum.monthly_quality(probe_hourly), settings.humidity.dead_month_share)
    month_status = monthly.select("sensor_id", "month", month_status="status").collect()
    out = (
        probe_hourly.with_columns(month=pl.col("hour").dt.truncate("1mo").dt.date())
        .join(month_status, on=["sensor_id", "month"], how="left")
        .with_columns(
            reliable=(pl.col("month_status") == hum.HEALTHY)
            & ~pl.col("faulty")
            & pl.col("humidity").is_not_null()
        )
        .drop("month")
        .sort("sensor_id", "hour")
    )
    dest = silver_dir(settings) / "sensor_community"
    contracts.HumidityHourly.validate(out)
    write_parquet_atomic(out, dest / "humidity_hourly.parquet")
    write_parquet_atomic(
        pl.concat(counts).with_columns(pl.col("sensor_type").str.to_lowercase()),
        dest / "humidity_status.parquet",
    )
    log.info(
        "humidity: %d probes, %d probe-hours, %d reliable (%.1f %%)",
        out["sensor_id"].n_unique(),
        out.height,
        out["reliable"].sum(),
        100 * out["reliable"].mean(),
    )
    return out


# ------------------------------------------------------------------- reference, weather
def clean_reference(settings: Settings) -> pl.DataFrame:
    files = sorted((settings.paths.bronze / "eea").glob("*/*/*.parquet"))
    out = (
        ref.flag_reference(pl.scan_parquet(files), settings.eea, settings.period.start, settings.period.end)
        .filter(pl.col("quality_flag") != ref.OUTSIDE_PERIOD)  # the bronze margin, not a quality loss
        .collect()
        .sort("sampling_point", "hour")
    )
    contracts.EeaHourly.validate(out)
    write_parquet_atomic(out, silver_dir(settings) / "eea" / "hourly.parquet")
    log.info(
        "EEA: %d hours, %.1f %% ok (PM2.5 %d points, PM10 %d points)",
        out.height,
        100 * (out["quality_flag"] == ref.OK).mean(),
        out.filter(pl.col("pollutant") == "pm25")["sampling_point"].n_unique(),
        out.filter(pl.col("pollutant") == "pm10")["sampling_point"].n_unique(),
    )
    return out


def clean_weather(settings: Settings) -> pl.DataFrame:
    max_pct = settings.humidity.max_pct
    frames = []
    dwd_files = sorted((settings.paths.bronze / "dwd").glob("*.parquet"))
    mf_files = sorted((settings.paths.bronze / "meteo_france").glob("*.parquet"))
    if dwd_files:
        frames.append(wx.official_dwd(pl.scan_parquet(dwd_files), settings.dwd.missing_sentinel, max_pct))
    if mf_files:
        frames.append(wx.official_meteo_france(pl.scan_parquet(mf_files), max_pct))
    start, end = settings.period.start, settings.period.end
    out = (
        pl.concat(frames)
        .filter(pl.col("hour").dt.date().is_between(start, end))
        .collect()
        .sort("source", "station_id", "hour")
    )
    contracts.WeatherHourly.validate(out)
    write_parquet_atomic(out, silver_dir(settings) / "weather" / "hourly.parquet")
    log.info(
        "official weather: %d stations, %d hours, humidity ok %.1f %%",
        out.select("source", "station_id").n_unique(),
        out.height,
        100 * (out["humidity_flag"] == wx.OK).mean(),
    )
    return out


def run(settings: Settings, plan: Plan) -> None:
    clean_reference(settings)
    clean_weather(settings)
    clean_humidity(settings, plan)
    clean_particles(settings, plan)
