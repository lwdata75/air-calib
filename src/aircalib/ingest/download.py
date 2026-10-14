"""Stage ``download``: fetch every raw file the plan needs, politely and resumably.

Sources, in order:

1. Sensor.Community: one gzip CSV per sensor and day, grouped in sensor-month
   *units* (a unit is recorded in the manifest once all its days are resolved:
   downloaded, or absent = HTTP 404);
2. EEA: E1a PM2.5 and PM10 Parquet files of the paired stations;
3. DWD: hourly temperature/humidity zips of the weather stations nearest to the
   German paired stations;
4. Meteo-France: department files around the French paired stations.

A re-run only fetches what is missing: files already on disk are skipped and
units recorded as converted in the bronze manifest are never re-downloaded,
even though their raw files were deleted.
"""

from __future__ import annotations

import asyncio
import time
from datetime import date, timedelta
from pathlib import Path

import polars as pl

from aircalib.config import Settings
from aircalib.ingest import discovery, dwd, eea, meteo_france
from aircalib.ingest import sensor_community as sc
from aircalib.ingest.http import PoliteClient, polite_client
from aircalib.ingest.manifest import FAILED, OK, Manifest
from aircalib.ingest.plan import Plan, reference_dir
from aircalib.logging import get_logger
from aircalib.transform.pairing import nearest_k

log = get_logger(__name__)


# ----------------------------------------------------------------------- helpers
def write_atomic(dest: Path, content: bytes) -> None:
    """Write through a ``.part`` file so that a crash never leaves a truncated file."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(dest.name + ".part")
    tmp.write_bytes(content)
    tmp.replace(dest)


def sc_manifests(settings: Settings) -> tuple[Manifest, Manifest]:
    key = ("sensor_id", "month")
    raw = Manifest(settings.paths.raw / "sensor_community" / "_manifest.jsonl", key)
    bronze = Manifest(settings.paths.bronze / "sensor_community" / "_manifest.jsonl", key)
    return raw, bronze


def plan_stations(plan: Plan) -> pl.DataFrame:
    """Paired PM2.5 sampling points (one per station) with their position."""
    return plan.pairs.select(
        "sampling_point", "station_code", "country", lat="station_lat", lon="station_lon"
    ).unique("sampling_point")


class _Ticker:
    """Logs progress every ``every_s`` seconds (works without a terminal)."""

    def __init__(self, label: str, total: int, every_s: float) -> None:
        self.label, self.total, self.every_s = label, total, every_s
        self.done = self.files = self.bytes = 0
        self.start = self.last = time.monotonic()

    def update(self, files: int, n_bytes: int) -> None:
        self.done += 1
        self.files += files
        self.bytes += n_bytes
        now = time.monotonic()
        if now - self.last >= self.every_s or self.done == self.total:
            self.last = now
            elapsed = now - self.start
            rate = self.done / elapsed if elapsed else 0.0
            eta_h = (self.total - self.done) / rate / 3600 if rate else float("nan")
            log.info(
                "%s: %d/%d units, %d files, %.1f MB, %.1f units/s, ETA %.1f h",
                self.label,
                self.done,
                self.total,
                self.files,
                self.bytes / 1e6,
                rate,
                eta_h,
            )


# -------------------------------------------------------------- Sensor.Community
async def _download_unit(
    client: PoliteClient, settings: Settings, unit: dict, raw_root: Path, today: date
) -> dict:
    cfg = settings.sensor_community
    folder = sc.raw_unit_dir(raw_root, unit["sensor_id"], unit["month"])
    days = [unit["first_day"] + timedelta(days=i) for i in range(unit["n_days"])]

    async def one(day: date) -> tuple[str, int]:
        url = sc.file_url(
            cfg.archive_url,
            sensor_type=unit["sensor_type"],
            sensor_id=unit["sensor_id"],
            indoor=unit["indoor"],
            day=day,
            today=today,
        )
        dest = folder / url.rsplit("/", 1)[-1]
        if dest.exists():
            return "found", dest.stat().st_size
        blob = await sc.fetch_file(client, url)
        if blob is None:
            return "absent", 0
        write_atomic(dest, blob)
        return "found", len(blob)

    record = {k: unit[k] for k in ("sensor_id", "month", "kind", "sensor_type")}
    started = time.monotonic()
    error: str | None = None
    try:
        # A TaskGroup cancels the other days as soon as one fails for good.
        async with asyncio.TaskGroup() as group:
            tasks = [group.create_task(one(d)) for d in days]
    except* Exception as errors:
        error = repr(errors.exceptions[0])[:500]
    if error is not None:
        return {**record, "status": FAILED, "error": error}
    results = [t.result() for t in tasks]
    return {
        **record,
        "status": OK,
        "days_requested": len(days),
        "days_found": sum(1 for s, _ in results if s == "found"),
        "days_absent": sum(1 for s, _ in results if s == "absent"),
        "bytes": sum(n for _, n in results),
        "seconds": round(time.monotonic() - started, 2),
    }


async def download_sensor_community(settings: Settings, plan: Plan, today: date) -> int:
    """Download every planned sensor-month not yet downloaded nor converted; return #failures."""
    raw_root = settings.paths.raw / "sensor_community"
    raw_manifest, bronze_manifest = sc_manifests(settings)
    finished = raw_manifest.done() | bronze_manifest.done()
    units = [
        u for u in plan.units.iter_rows(named=True) if (str(u["sensor_id"]), str(u["month"])) not in finished
    ]
    log.info(
        "Sensor.Community: %d sensor-months planned, %d already done, %d to fetch",
        plan.units.height,
        plan.units.height - len(units),
        len(units),
    )
    if not units:
        return 0
    ticker = _Ticker("Sensor.Community", len(units), settings.http.progress_log_s)
    queue: asyncio.Queue[dict] = asyncio.Queue()
    for unit in units:
        queue.put_nowait(unit)
    failures = 0

    async with polite_client(settings.http) as client:

        async def worker() -> None:
            nonlocal failures
            while not queue.empty():
                unit = queue.get_nowait()
                record = await _download_unit(client, settings, unit, raw_root, today)
                raw_manifest.append(record)
                if record["status"] == FAILED:
                    failures += 1
                    log.warning("unit %s %s failed: %s", unit["sensor_id"], unit["month"], record["error"])
                ticker.update(record.get("days_found", 0), record.get("bytes", 0))

        await asyncio.gather(*(worker() for _ in range(settings.sensor_community.unit_workers)))
    log.info("Sensor.Community: %d unit(s) failed (re-run to retry)", failures)
    return failures


# --------------------------------------------------------------------------- EEA
async def eea_sampling_points(settings: Settings, plan: Plan) -> pl.DataFrame:
    """PM2.5 points of the plan + every PM10 point of the same stations."""
    pm25 = await discovery.eea_stations(settings, "pm25")
    pm10 = await discovery.eea_stations(settings, "pm10")
    paired = plan_stations(plan)
    cols = ["sampling_point", "station_code", "country", "url"]
    return pl.concat(
        [
            pm25.join(paired.select("sampling_point"), on="sampling_point", how="semi")
            .select(cols)
            .with_columns(pollutant=pl.lit("pm25")),
            pm10.join(paired.select("station_code"), on="station_code", how="semi")
            .select(cols)
            .with_columns(pollutant=pl.lit("pm10")),
        ]
    ).sort("pollutant", "sampling_point")


async def download_eea(settings: Settings, plan: Plan) -> int:
    points = await eea_sampling_points(settings, plan)
    points.write_parquet(reference_dir(settings) / "eea_sampling_points.parquet")
    manifest = Manifest(settings.paths.raw / "eea" / "_manifest.jsonl", ("pollutant", "sampling_point"))
    converted = Manifest(
        settings.paths.bronze / "eea" / "_manifest.jsonl", ("pollutant", "sampling_point")
    ).done()
    failures = 0
    async with polite_client(settings.http) as client:

        async def one(rec: dict) -> None:
            nonlocal failures
            dest = eea.raw_path(settings.paths.raw, rec["pollutant"], rec["country"], rec["sampling_point"])
            key = {"pollutant": rec["pollutant"], "sampling_point": rec["sampling_point"]}
            if manifest.key_of(key) in converted:  # already in bronze: the raw file is not needed again
                return
            try:
                await eea.download_parquet(client, rec["url"], dest)
                manifest.append({**key, "status": OK, "bytes": dest.stat().st_size})
            except Exception as exc:
                failures += 1
                manifest.append({**key, "status": FAILED, "error": repr(exc)[:500]})

        await asyncio.gather(*(one(r) for r in points.iter_rows(named=True)))
    log.info(
        "EEA: %d PM2.5 + %d PM10 sampling points, %d failure(s)",
        points.filter(pl.col("pollutant") == "pm25").height,
        points.filter(pl.col("pollutant") == "pm10").height,
        failures,
    )
    return failures


# --------------------------------------------------------------------------- DWD
async def select_dwd_stations(settings: Settings, client: PoliteClient, plan: Plan) -> pl.DataFrame:
    """The ``n_nearest`` DWD stations of each German paired station, with their zip file."""
    cfg = settings.dwd
    listing = await dwd.fetch_text(client, cfg.base_url + "/")
    stations_text = await dwd.fetch_text(
        client, f"{cfg.base_url}/{cfg.station_list}", cfg.station_list_encoding
    )
    files = dwd.files_covering(dwd.parse_file_listing(listing), settings.period.start, settings.period.end)
    stations = dwd.parse_station_list(stations_text).join(
        files.select("station_id").unique(), on="station_id", how="semi"
    )
    targets = plan_stations(plan).filter(pl.col("country") == "DE")
    nearest = nearest_k(
        targets,
        stations,
        point_id="sampling_point",
        candidate_id="station_id",
        k=settings.weather.n_nearest,
        max_km=settings.weather.max_distance_km,
    )
    selected = nearest.join(stations, on="station_id").join(
        files.select("station_id", "filename", file_begin="begin", file_end="end"), on="station_id"
    )
    selected.write_parquet(reference_dir(settings) / "weather_stations_dwd.parquet")
    return selected


async def download_dwd(settings: Settings, plan: Plan) -> int:
    if plan_stations(plan).filter(pl.col("country") == "DE").is_empty():
        return 0
    manifest = Manifest(settings.paths.raw / "dwd" / "_manifest.jsonl", ("filename",))
    bronze_done = Manifest(settings.paths.bronze / "dwd" / "_manifest.jsonl", ("filename",)).done()
    failures = 0
    async with polite_client(settings.http) as client:
        selected = await select_dwd_stations(settings, client, plan)
        files = selected.select("filename").unique()

        async def one(filename: str) -> None:
            nonlocal failures
            dest = dwd.raw_path(settings.paths.raw, filename)
            if dest.exists() or (filename,) in bronze_done:
                return
            try:
                response = await client.get(f"{settings.dwd.base_url}/{filename}")
                response.raise_for_status()
                write_atomic(dest, response.content)
                manifest.append({"filename": filename, "status": OK, "bytes": len(response.content)})
            except Exception as exc:
                failures += 1
                manifest.append({"filename": filename, "status": FAILED, "error": repr(exc)[:500]})

        await asyncio.gather(*(one(f) for f in files["filename"]))
    log.info(
        "DWD: %d weather stations for %d German stations, %d failure(s)",
        files.height,
        selected["sampling_point"].n_unique(),
        failures,
    )
    return failures


# ------------------------------------------------------------------ Meteo-France
async def download_meteo_france(settings: Settings, plan: Plan) -> int:
    targets = plan_stations(plan).filter(pl.col("country") == "FR")
    if targets.is_empty():
        return 0
    cfg = settings.meteo_france
    manifest = Manifest(settings.paths.raw / "meteo_france" / "_manifest.jsonl", ("filename",))
    bronze_done = Manifest(settings.paths.bronze / "meteo_france" / "_manifest.jsonl", ("filename",)).done()
    failures = 0
    async with polite_client(settings.http) as client:
        resources = await meteo_france.resources(client, cfg)
        communes = await meteo_france.communes(client, cfg)
        departments = meteo_france.departments_near(targets, communes, settings.weather.max_distance_km)
        files = meteo_france.files_for(resources, departments, settings.period.start, settings.period.end)
        files.write_parquet(reference_dir(settings) / "meteo_france_files.parquet")

        async def one(url: str) -> None:
            nonlocal failures
            dest = meteo_france.raw_path(settings.paths.raw, url)
            if dest.exists() or (dest.name,) in bronze_done:
                return
            try:
                response = await client.get(url)
                response.raise_for_status()
                write_atomic(dest, response.content)
                manifest.append({"filename": dest.name, "status": OK, "bytes": len(response.content)})
            except Exception as exc:
                failures += 1
                manifest.append({"filename": dest.name, "status": FAILED, "error": repr(exc)[:500]})

        await asyncio.gather(*(one(u) for u in files["url"]))
    log.info(
        "Meteo-France: %d department(s) %s, %d file(s), %.0f MB announced, %d failure(s)",
        len(departments),
        departments,
        files.height,
        (files["bytes"].sum() or 0) / 1e6,
        failures,
    )
    return failures


# ------------------------------------------------------------- sensor registration
async def registrations(settings: Settings, plan: Plan, today: date) -> pl.DataFrame:
    """Registration window of every planned sensor (used to study ageing probes)."""
    ids = pl.concat([plan.pairs.select("sensor_id"), plan.humidity.select("sensor_id")])
    table = await discovery.sensor_registrations(settings, ids, today)
    table.write_parquet(reference_dir(settings) / "sensor_registrations.parquet")
    return table


# ---------------------------------------------------------------------- the stage
async def run(settings: Settings, plan: Plan, today: date) -> dict[str, int]:
    """Download every source; return the number of failures per source."""
    failures = {
        "eea": await download_eea(settings, plan),
        "dwd": await download_dwd(settings, plan),
        "meteo_france": await download_meteo_france(settings, plan),
        "sensor_community": await download_sensor_community(settings, plan, today),
    }
    await registrations(settings, plan, today)
    return failures
