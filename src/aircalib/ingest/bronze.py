"""Stage ``bronze``: convert raw files to typed Parquet (zstd), content unchanged.

For every source, a converted unit is validated against its Pandera contract
(``aircalib.schemas.bronze``), written atomically, recorded in the bronze
manifest with audit counters, and only *then* its raw files are deleted
(unless ``keep_raw``). A crash at any point therefore loses nothing.
"""

from __future__ import annotations

import shutil
import zlib
from pathlib import Path

import polars as pl

from aircalib.config import Settings
from aircalib.ingest import dwd, eea, meteo_france
from aircalib.ingest import sensor_community as sc
from aircalib.ingest.download import plan_stations, sc_manifests
from aircalib.ingest.manifest import FAILED, OK, Manifest
from aircalib.ingest.plan import Plan, reference_dir
from aircalib.logging import get_logger
from aircalib.schemas import bronze as contracts
from aircalib.transform.pairing import nearest_k, pairs_within, weather_links

log = get_logger(__name__)

COMPRESSION = "zstd"


def write_parquet_atomic(frame: pl.DataFrame, dest: Path) -> int:
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(dest.name + ".part")
    frame.write_parquet(tmp, compression=COMPRESSION)
    tmp.replace(dest)
    return dest.stat().st_size


# -------------------------------------------------------------- Sensor.Community
def read_unit(folder: Path) -> tuple[pl.DataFrame, int]:
    """Concatenate the raw day files of a unit (all text); count unreadable files."""
    frames, corrupt = [], 0
    for path in sorted(folder.glob("*.csv*")):
        try:
            frame = sc.read_day_csv(path.read_bytes(), gzipped=path.suffix == ".gz")
        except (zlib.error, EOFError, pl.exceptions.ComputeError, pl.exceptions.NoDataError):
            corrupt += 1
            continue
        if frame.height:
            frames.append(frame)
    raw = pl.concat(frames, how="diagonal_relaxed") if frames else pl.DataFrame()
    return raw, corrupt


def convert_unit(settings: Settings, unit: dict) -> dict:
    """Raw sensor-month → bronze Parquet; returns the manifest record."""
    cfg = settings.sensor_community
    raw_root = settings.paths.raw / "sensor_community"
    folder = sc.raw_unit_dir(raw_root, unit["sensor_id"], unit["month"])
    record = {k: unit[k] for k in ("sensor_id", "month", "kind", "sensor_type")}
    raw, corrupt = read_unit(folder) if folder.exists() else (pl.DataFrame(), 0)
    raw_bytes = sum(p.stat().st_size for p in folder.glob("*")) if folder.exists() else 0
    record |= {"raw_files": len(list(folder.glob("*.csv*"))) if folder.exists() else 0}
    record |= {"corrupt_files": corrupt, "raw_bytes": raw_bytes}
    if raw.is_empty():
        return {**record, "status": OK, "rows": 0, "bronze_bytes": 0}

    is_pm = unit["kind"] == "pm"
    columns = cfg.pm_columns if is_pm else cfg.humidity_columns
    typed, stats = sc.bronze_frame(raw, columns, cfg.timestamp_format)
    contract = contracts.SensorCommunityPm if is_pm else contracts.SensorCommunityHumidity
    contract.validate(typed)
    stats["foreign_sensor_rows"] = typed.filter(pl.col("sensor_id") != unit["sensor_id"]).height
    dest = sc.bronze_unit_path(
        settings.paths.bronze / "sensor_community", unit["kind"], unit["sensor_id"], unit["month"]
    )
    return {**record, **stats, "status": OK, "bronze_bytes": write_parquet_atomic(typed, dest)}


def bronze_sensor_community(settings: Settings, plan: Plan) -> int:
    raw_manifest, bronze_manifest = sc_manifests(settings)
    downloaded = raw_manifest.frame()
    if downloaded.is_empty():
        log.warning("Sensor.Community: nothing downloaded yet")
        return 0
    ready = raw_manifest.done() - bronze_manifest.done()
    units = [u for u in plan.units.iter_rows(named=True) if (str(u["sensor_id"]), str(u["month"])) in ready]
    log.info("Sensor.Community: %d sensor-month(s) to convert", len(units))
    failures = 0
    for i, unit in enumerate(units, 1):
        folder = sc.raw_unit_dir(settings.paths.raw / "sensor_community", unit["sensor_id"], unit["month"])
        try:
            record = convert_unit(settings, unit)
        except Exception as exc:
            failures += 1
            record = {k: unit[k] for k in ("sensor_id", "month", "kind", "sensor_type")}
            record |= {"status": FAILED, "error": repr(exc)[:500]}
            log.warning("conversion failed for %s %s: %s", unit["sensor_id"], unit["month"], exc)
        bronze_manifest.append(record)
        if record["status"] == OK and not settings.sensor_community.keep_raw and folder.exists():
            shutil.rmtree(folder)
        if i % 2000 == 0:
            log.info("Sensor.Community: %d/%d converted", i, len(units))
    return failures


# --------------------------------------------------------------------------- EEA
def bronze_eea(settings: Settings) -> int:
    points = pl.read_parquet(reference_dir(settings) / "eea_sampling_points.parquet")
    manifest = Manifest(settings.paths.bronze / "eea" / "_manifest.jsonl", ("pollutant", "sampling_point"))
    done, failures = manifest.done(), 0
    cfg, period = settings.eea, settings.period
    for rec in points.iter_rows(named=True):
        key = {"pollutant": rec["pollutant"], "sampling_point": rec["sampling_point"]}
        if manifest.key_of(key) in done:
            continue
        src = eea.raw_path(settings.paths.raw, rec["pollutant"], rec["country"], rec["sampling_point"])
        try:
            frame = eea.bronze_frame(
                eea.scan_observations(src), period.start, period.end, cfg.bronze_margin_days
            ).collect()
            contracts.Eea.validate(frame)
            dest = eea.bronze_path(
                settings.paths.bronze, rec["pollutant"], rec["country"], rec["sampling_point"]
            )
            size = write_parquet_atomic(frame, dest)
            manifest.append({**key, "status": OK, "rows": frame.height, "bronze_bytes": size})
            if not cfg.keep_raw:
                src.unlink()
        except Exception as exc:
            failures += 1
            manifest.append({**key, "status": FAILED, "error": repr(exc)[:500]})
    if not cfg.keep_raw and failures == 0:
        purge_raw_eea(settings)
    return failures


def purge_raw_eea(settings: Settings) -> int:
    """Delete the raw EEA files once bronze holds every planned point.

    Bronze keeps the study period of the planned points; discovery's own downloads
    (stations near any sensor) were only needed for the monthly availability, cached
    in ``data/meta``. Returns the number of bytes freed.
    """
    freed = 0
    for path in (settings.paths.raw / "eea").rglob("*.parquet"):
        freed += path.stat().st_size
        path.unlink()
    if freed:
        log.info("EEA: %.0f MB of raw files deleted (bronze holds them)", freed / 1e6)
    return freed


# --------------------------------------------------------------------------- DWD
def bronze_dwd(settings: Settings) -> int:
    ref = reference_dir(settings) / "weather_stations_dwd.parquet"
    if not ref.exists():
        return 0
    files = pl.read_parquet(ref).select("station_id", "filename").unique()
    manifest = Manifest(settings.paths.bronze / "dwd" / "_manifest.jsonl", ("filename",))
    done, failures = manifest.done(), 0
    for rec in files.iter_rows(named=True):
        if (rec["filename"],) in done:
            continue
        src = dwd.raw_path(settings.paths.raw, rec["filename"])
        try:
            frame = dwd.bronze_frame(
                dwd.read_product(src.read_bytes()), settings.period.start, settings.period.end
            )
            contracts.Dwd.validate(frame)
            size = write_parquet_atomic(frame, dwd.bronze_path(settings.paths.bronze, rec["station_id"]))
            manifest.append(
                {"filename": rec["filename"], "status": OK, "rows": frame.height, "bronze_bytes": size}
            )
            if not settings.dwd.keep_raw:
                src.unlink()
        except Exception as exc:
            failures += 1
            manifest.append({"filename": rec["filename"], "status": FAILED, "error": repr(exc)[:500]})
    return failures


# ------------------------------------------------------------------ Meteo-France
def bronze_meteo_france(settings: Settings, plan: Plan) -> int:
    """Department files → the ``n_nearest`` humidity stations of each French paired station.

    Station choice needs the data itself (many stations have no humidity
    sensor), so every department file is read first, then the selection is made.
    """
    ref = reference_dir(settings) / "meteo_france_files.parquet"
    manifest = Manifest(settings.paths.bronze / "meteo_france" / "_manifest.jsonl", ("filename",))
    if not ref.exists():
        return 0
    files = pl.read_parquet(ref)
    names = [meteo_france.raw_path(settings.paths.raw, u).name for u in files["url"]]
    if all((n,) in manifest.done() for n in names):
        return 0
    cfg, period = settings.meteo_france, settings.period
    targets = plan_stations(plan).filter(pl.col("country") == "FR")
    frames = []
    for url in files["url"]:
        src = meteo_france.raw_path(settings.paths.raw, url)
        if not src.exists():
            log.warning("Meteo-France: %s missing, run download first", src.name)
            return 1
        frame = meteo_france.bronze_frame(meteo_france.read_raw(src, cfg.columns), period.start, period.end)
        # Keep only stations within reach of a target: departments are large.
        reachable = pairs_within(
            targets.select("lat", "lon"),
            frame.select("station_id", "lat", "lon").unique("station_id"),
            settings.weather.max_distance_km,
        ).select("station_id")
        frames.append(frame.join(reachable.collect().unique(), on="station_id", how="semi"))
    data = pl.concat(frames)
    stations = meteo_france.station_table(data).filter(pl.col("humidity_hours") > 0)
    selected = nearest_k(
        targets,
        stations,
        point_id="sampling_point",
        candidate_id="station_id",
        k=settings.weather.n_nearest,
        max_km=settings.weather.max_distance_km,
    ).join(stations, on="station_id")
    selected.write_parquet(reference_dir(settings) / "weather_stations_meteo_france.parquet")
    for station_id in selected["station_id"].unique().sort():
        frame = data.filter(pl.col("station_id") == station_id)
        contracts.MeteoFrance.validate(frame)
        write_parquet_atomic(frame, meteo_france.bronze_path(settings.paths.bronze, station_id))
    for name in names:
        manifest.append({"filename": name, "status": OK})
        if not cfg.keep_raw:
            (settings.paths.raw / "meteo_france" / name).unlink(missing_ok=True)
    log.info(
        "Meteo-France: %d stations with humidity read, %d kept for %d French stations",
        stations.height,
        selected["station_id"].n_unique(),
        selected["sampling_point"].n_unique(),
    )
    return 0


# --------------------------------------------------------- pair ↔ weather station
def pair_weather(settings: Settings, plan: Plan) -> pl.DataFrame:
    """Weather stations of every pair with the sensor → weather-station distance (a feature)."""
    ref = reference_dir(settings)
    frames = [
        pl.read_parquet(path)
        .select("sampling_point", "station_id", "rank", "lat", "lon", "distance_km")
        .with_columns(source=pl.lit(source))
        for source, path in (
            ("dwd", ref / "weather_stations_dwd.parquet"),
            ("meteo_france", ref / "weather_stations_meteo_france.parquet"),
        )
        if path.exists()
    ]
    if not frames:
        return pl.DataFrame()
    links = weather_links(plan.pairs, pl.concat(frames))
    links.write_parquet(ref / "pair_weather.parquet")
    missing = plan.pairs.join(links, on="sensor_id", how="anti").height
    log.info(
        "weather links: %d pairs, median sensor → weather station %.1f km, %d pair(s) without station",
        links["sensor_id"].n_unique(),
        links.filter(pl.col("weather_rank") == 1)["sensor_weather_km"].median() or float("nan"),
        missing,
    )
    return links


# ---------------------------------------------------------------------- the stage
def run(settings: Settings, plan: Plan) -> dict[str, int]:
    """Convert every source; return the number of failures per source."""
    failures = {
        "eea": bronze_eea(settings),
        "dwd": bronze_dwd(settings),
        "meteo_france": bronze_meteo_france(settings, plan),
        "sensor_community": bronze_sensor_community(settings, plan),
    }
    pair_weather(settings, plan)
    return failures
