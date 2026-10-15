"""Step 1: which citizen sensors were active near a reference station, and when?

The live API only shows sensors active *today*. To find sensors active over
the study period we:

1. list one archive day per month (the ``sample_day_of_month``) → which sensor
   ids were active that month (24 listings instead of 731);
2. read the first row of each candidate's most recent file with an HTTP Range
   request → its site id (``location``) and coordinates;
3. skip sensors that the live snapshot places outside the study countries,
   which divides the number of archive requests by ~2.

Every network result is cached under ``data/meta`` so a re-run is instant.
"""

from __future__ import annotations

import asyncio
import io
import json
from datetime import UTC, date, datetime
from pathlib import Path

import polars as pl
from rich.progress import Progress

from aircalib.config import Settings
from aircalib.ingest import eea
from aircalib.ingest import sensor_community as sc
from aircalib.ingest.http import PoliteClient, polite_client
from aircalib.logging import console, get_logger
from aircalib.transform.hourly import hourly_mean, lagged_correlation
from aircalib.transform.pairing import nearest_usable_station, pairs_within

log = get_logger(__name__)


# --------------------------------------------------------------------------- paths
def _meta(settings: Settings) -> Path:
    """The ``data/meta`` folder (created if needed)."""
    path = settings.paths.meta
    path.mkdir(parents=True, exist_ok=True)
    return path


def eea_raw_path(settings: Settings, pollutant: str, country: str, sampling_point: str) -> Path:
    """Where the raw EEA file of a sampling point is stored."""
    return eea.raw_path(settings.paths.raw, pollutant, country, sampling_point)


# ----------------------------------------------------------------- period sampling
def sample_days(start: date, end: date, day_of_month: int) -> list[date]:
    """One date per calendar month of ``[start, end]`` on ``day_of_month``."""
    days, year, month = [], start.year, start.month
    while (year, month) <= (end.year, end.month):
        day = date(year, month, day_of_month)
        if start <= day <= end:
            days.append(day)
        year, month = (year + 1, 1) if month == 12 else (year, month + 1)
    return days


# ---------------------------------------------------------------- archive listings
async def _listing(
    client: PoliteClient, settings: Settings, day: date, today: date, cache: Path
) -> pl.DataFrame:
    """File list of one archive day, read from the cache when present."""
    path = cache / f"{day.isoformat()}.parquet"
    if path.exists():
        return pl.read_parquet(path)
    url = sc.day_url(settings.sensor_community.archive_url, day, today)
    frame = sc.listing_frame(await sc.fetch_listing(client, url))
    frame.write_parquet(path)
    return frame


async def collect_listings(settings: Settings, today: date, days: list[date] | None = None) -> pl.DataFrame:
    """Archive file listings of the sampled days (one row per sensor-day file).

    A listing is a ~5 MB page that the server renders slowly; with many in
    flight the server drops connections, so they get their own lower limit.
    Failed days are reported and simply retried by the next run (cache).
    """
    cache = _meta(settings) / "sc_listings"
    cache.mkdir(exist_ok=True)
    if days is None:
        days = sample_days(settings.period.start, settings.period.end, settings.discovery.sample_day_of_month)
    gate = asyncio.Semaphore(settings.discovery.listing_concurrency)
    async with polite_client(settings.http) as client:
        with Progress(console=console, transient=True, disable=not console.is_terminal) as progress:
            task = progress.add_task("archive listings", total=len(days))

            async def one(day: date) -> pl.DataFrame:
                """List one archive day (bounded concurrency, cached)."""
                async with gate:
                    frame = await _listing(client, settings, day, today, cache)
                progress.advance(task)
                return frame

            results = await asyncio.gather(*(one(d) for d in days), return_exceptions=True)
    failed = [d for d, r in zip(days, results, strict=True) if isinstance(r, BaseException)]
    if failed:
        log.warning("listing failed for %d day(s), re-run to retry: %s", len(failed), failed)
    listings = pl.concat([r for r in results if isinstance(r, pl.DataFrame)])
    log.info(
        "listed %d/%d sampled days: %d sensor-day files", len(days) - len(failed), len(days), listings.height
    )
    return listings


# ------------------------------------------------------------- sensor registration
def history_days(first_year: int, months: list[int], before: date, day_of_month: int) -> list[date]:
    """Listing days used to date sensor registrations, strictly before ``before``."""
    return [
        d
        for year in range(first_year, before.year + 1)
        for month in months
        if (d := date(year, month, day_of_month)) < before
    ]


def registration_curve(listings: pl.DataFrame) -> pl.DataFrame:
    """Highest sensor id seen up to each listing day (ids are allocated in sequence)."""
    return (
        listings.group_by("date")
        .agg(max_sensor_id=pl.col("sensor_id").max())
        .sort("date")
        .with_columns(pl.col("max_sensor_id").cum_max())
    )


def registration_dates(sensor_ids: pl.DataFrame, curve: pl.DataFrame) -> pl.DataFrame:
    """First listing day whose highest id reaches the sensor id: the sensor existed by then.

    The true registration lies between the previous listing day and this one
    (``registered_after``, ``registered_by``); null bounds mean "outside the curve".
    """
    by = (
        curve.with_columns(registered_after=pl.col("date").shift(1))
        .rename({"date": "registered_by"})
        .sort("max_sensor_id")
    )
    return (
        sensor_ids.select("sensor_id")
        .unique()
        .sort("sensor_id")
        .join_asof(by, left_on="sensor_id", right_on="max_sensor_id", strategy="forward")
        .select("sensor_id", "registered_after", "registered_by")
    )


async def sensor_registrations(settings: Settings, sensor_ids: pl.DataFrame, today: date) -> pl.DataFrame:
    """Registration window of each sensor, from archive listings since the project's start."""
    d = settings.discovery
    days = history_days(d.history_start_year, d.history_months, settings.period.start, d.sample_day_of_month)
    days += sample_days(settings.period.start, settings.period.end, d.sample_day_of_month)
    listings = await collect_listings(settings, today, days)
    curve = registration_curve(listings)
    curve.write_parquet(_meta(settings) / "sc_registration_curve.parquet")
    return registration_dates(sensor_ids, curve)


# -------------------------------------------------------------------- live snapshot
def parse_live_snapshot(records: list[dict]) -> pl.DataFrame:
    """Flatten the live API JSON into one row per sensor."""
    rows = [
        {
            "sensor_id": r["sensor"]["id"],
            "sensor_type": r["sensor"]["sensor_type"]["name"].lower(),
            "location_id": r["location"]["id"],
            "lat": float(r["location"]["latitude"]),
            "lon": float(r["location"]["longitude"]),
            "country": r["location"]["country"],
            "indoor": bool(r["location"]["indoor"]),
            "exact_location": bool(r["location"]["exact_location"]),
            "timestamp": r["timestamp"],
        }
        for r in records
        if r["location"]["latitude"] and r["location"]["longitude"]
    ]
    return (
        pl.DataFrame(rows)
        .with_columns(pl.col("timestamp").str.to_datetime("%Y-%m-%d %H:%M:%S"))
        .unique("sensor_id", keep="first")
    )


async def live_snapshot(settings: Settings, today: date) -> pl.DataFrame:
    """Sensors that emitted in the last 5 minutes, with the UTC time we asked.

    ``retrieved_at_utc`` lets us check the clock of the ``timestamp`` field.
    The first snapshot taken is reused by later runs: it decides which sensors
    are skipped, so refreshing it would silently change the inventory.
    """
    cached = sorted(_meta(settings).glob("sc_live_snapshot_*.parquet"))
    if cached:
        return pl.read_parquet(cached[0])
    path = _meta(settings) / f"sc_live_snapshot_{today.isoformat()}.parquet"
    async with polite_client(settings.http) as client:
        response = await client.get(settings.sensor_community.live_snapshot_url)
        response.raise_for_status()
    retrieved = datetime.now(UTC).replace(tzinfo=None)
    frame = parse_live_snapshot(json.loads(response.content)).with_columns(
        retrieved_at_utc=pl.lit(retrieved, dtype=pl.Datetime("us"))
    )
    frame.write_parquet(path)
    log.info("live snapshot: %d sensors", frame.height)
    return frame


# ------------------------------------------------------------------- positions
def head_candidates(
    listings: pl.DataFrame, live: pl.DataFrame, types: list[str], countries: list[str]
) -> pl.DataFrame:
    """Most recent archive file of every sensor worth locating.

    A sensor is skipped only when the live snapshot positively places it
    outside ``countries`` (sensors that stopped emitting are always kept).
    """
    elsewhere = live.filter(~pl.col("country").is_in(countries)).select("sensor_id")
    return (
        listings.filter(pl.col("sensor_type").is_in(types))
        .sort("date")
        .group_by("sensor_id")
        .agg(pl.all().last())
        .join(elsewhere, on="sensor_id", how="anti")
        .sort("sensor_id")
    )


async def resolve_positions(settings: Settings, candidates: pl.DataFrame, today: date) -> pl.DataFrame:
    """Site id and coordinates of each candidate, read from its archive file head."""
    path = _meta(settings) / "sc_positions.parquet"
    known = pl.read_parquet(path) if path.exists() else None
    todo = candidates if known is None else candidates.join(known, on=["sensor_id", "date"], how="anti")
    log.info("positions: %d cached, %d to fetch", 0 if known is None else known.height, todo.height)

    cfg = settings.sensor_community
    rows: list[dict] = []
    if todo.height:
        async with polite_client(settings.http) as client:
            with Progress(console=console, transient=True, disable=not console.is_terminal) as progress:
                task = progress.add_task("file heads", total=todo.height)

                async def one(rec: dict) -> None:
                    """Read the first bytes of one sensor file to get its site and coordinates."""
                    url = sc.day_url(cfg.archive_url, rec["date"], today) + rec["filename"]
                    try:
                        head = await sc.fetch_head(client, url, cfg.head_bytes)
                    except Exception as exc:
                        log.warning("head failed for %s: %s", rec["filename"], exc)
                        head = None
                    rows.append({"sensor_id": rec["sensor_id"], "date": rec["date"], "head": head})
                    progress.advance(task)

                await asyncio.gather(*(one(rec) for rec in todo.iter_rows(named=True)))

    fetched = pl.DataFrame(
        [
            {
                "sensor_id": r["sensor_id"],
                "date": r["date"],
                "location_id": (r["head"] or {}).get("location"),
                "lat": (r["head"] or {}).get("lat"),
                "lon": (r["head"] or {}).get("lon"),
            }
            for r in rows
        ],
        schema={
            "sensor_id": pl.Int64,
            "date": pl.Date,
            "location_id": pl.String,
            "lat": pl.String,
            "lon": pl.String,
        },
    ).with_columns(
        location_id=pl.col("location_id").cast(pl.Int64, strict=False),
        lat=pl.col("lat").cast(pl.Float64, strict=False),
        lon=pl.col("lon").cast(pl.Float64, strict=False),
    )
    positions = fetched if known is None else pl.concat([known, fetched])
    positions.write_parquet(path)
    return positions


# --------------------------------------------------------------------- inventory
def sensor_inventory(listings: pl.DataFrame, positions: pl.DataFrame, settings: Settings) -> pl.DataFrame:
    """One row per located sensor: kind, activity months and position."""
    cfg = settings.sensor_community
    kind = (
        pl.when(pl.col("sensor_type").is_in(cfg.pm_types))
        .then(pl.lit("pm"))
        .when(pl.col("sensor_type").is_in(cfg.humidity_types))
        .then(pl.lit("humidity"))
    )
    activity = (
        listings.filter(pl.col("sensor_type").is_in(cfg.pm_types + cfg.humidity_types))
        .group_by("sensor_id")
        .agg(
            sensor_type=pl.col("sensor_type").first(),
            indoor=pl.col("indoor").any(),
            months=pl.col("date").dt.truncate("1mo").unique().sort(),
        )
        .with_columns(kind=kind, n_months=pl.col("months").list.len())
    )
    return activity.join(
        positions.select("sensor_id", "location_id", "lat", "lon").drop_nulls(),
        on="sensor_id",
        how="inner",
    )


# ------------------------------------------------------------------- EEA stations
_POLLUTANT_NAMES = {"pm25": "PM2.5", "pm10": "PM10"}


async def eea_stations(settings: Settings, pollutant: str = "pm25") -> pl.DataFrame:
    """Hourly E1a sampling points of the study countries, with their metadata."""
    meta_path = _meta(settings) / "eea_metadata.parquet"
    code = settings.eea.pollutants[pollutant]
    async with polite_client(settings.http) as client:
        if not meta_path.exists():
            eea.parse_metadata(await eea.fetch_metadata_csv(client, settings.eea)).write_parquet(meta_path)
        url_frames = []
        for country in settings.discovery.countries:
            urls = await eea.parquet_urls(client, settings.eea, country, code)
            url_frames.append(
                pl.DataFrame({"url": urls}).with_columns(
                    sampling_point=pl.col("url").str.split("/").list.last().str.strip_suffix(".parquet"),
                    country=pl.lit(country),
                )
            )
    urls = pl.concat(url_frames)
    points = eea.sampling_points(
        pl.read_parquet(meta_path),
        _POLLUTANT_NAMES[pollutant],
        settings.eea.measurement_types,
        settings.eea.timezones,
    )
    stations = points.join(urls.drop("country"), on="sampling_point", how="inner")
    log.info(
        "EEA %s: %d hourly E1a files, %d automatic and in the clock scope",
        pollutant,
        urls.height,
        stations.height,
    )
    stations.write_parquet(_meta(settings) / f"eea_stations_{pollutant}.parquet")
    return stations


async def download_station_files(
    settings: Settings, stations: pl.DataFrame, pollutant: str = "pm25"
) -> pl.DataFrame:
    """Download the E1a Parquet of the given sampling points into ``raw`` (as-is)."""
    async with polite_client(settings.http) as client:
        with Progress(console=console, transient=True, disable=not console.is_terminal) as progress:
            task = progress.add_task(f"EEA {pollutant} files", total=stations.height)

            async def one(rec: dict) -> dict:
                """Download one EEA file and summarise its valid hours per month."""
                dest = eea_raw_path(settings, pollutant, rec["country"], rec["sampling_point"])
                await eea.download_parquet(client, rec["url"], dest)
                progress.advance(task)
                return {
                    "sampling_point": rec["sampling_point"],
                    "path": str(dest),
                    "bytes": dest.stat().st_size,
                }

            done = await asyncio.gather(*(one(r) for r in stations.iter_rows(named=True)))
    return pl.DataFrame(done)


def station_availability(settings: Settings, files: pl.DataFrame) -> pl.DataFrame:
    """Monthly valid+verified hours per sampling point over the study period."""
    lf = eea.scan_observations([Path(p) for p in files["path"]])
    return eea.monthly_valid_hours(lf, settings.eea, settings.period.start, settings.period.end).collect()


# ------------------------------------------------------------------ feasibility
def pair_table(
    inventory: pl.DataFrame, stations: pl.DataFrame, availability: pl.DataFrame, max_km: float
) -> pl.DataFrame:
    """One row per outdoor PM sensor: its nearest usable station within ``max_km``.

    Rule (1 sensor = 1 station): among the stations closer than ``max_km`` that
    have verified hours while the sensor is active, keep the nearest one
    (ties broken by sampling point id). ``n_stations_within`` keeps track of how
    many usable stations were in reach, and ``distance_km`` is kept to study the
    error as a function of distance.

    ``overlap_hours`` estimates the hours where both sources are usable: for each
    month where the sensor appears in the sampled listing, the station's valid
    verified hours that month.
    """
    pm = inventory.filter(pl.col("kind") == "pm", ~pl.col("indoor"))
    humidity = inventory.filter(pl.col("kind") == "humidity", ~pl.col("indoor")).select(
        "location_id", hum_months=pl.col("months")
    )
    nearest = nearest_usable_station(
        pm.select("sensor_id", "sensor_type", "location_id", "months", "lat", "lon"),
        stations,
        availability,
        max_km,
        point_id="sensor_id",
    )
    with_humidity = (
        nearest.select("sensor_id", "location_id", "months")
        .join(humidity, on="location_id", how="inner")
        .filter(pl.col("months").list.set_intersection("hum_months").list.len() > 0)
        .select("sensor_id")
        .unique()
        .with_columns(has_humidity=pl.lit(True))
    )
    return (
        nearest.join(with_humidity, on="sensor_id", how="left")
        .with_columns(pl.col("has_humidity").fill_null(False))
        .drop("months")
        .sort("sensor_id")
    )


def feasibility_summary(pairs: pl.DataFrame, radii_km: list[float]) -> pl.DataFrame:
    """Pair counts per country and radius (``pairs`` = one nearest station per sensor).

    Because each sensor keeps a single station, ``n_pairs`` equals ``n_sensors``;
    several sensors can share the same station, hence ``n_stations`` is smaller.
    """
    rows = []
    for radius in radii_km:
        within = pairs.filter(pl.col("distance_km") <= radius)
        rows.append(
            within.group_by("country")
            .agg(
                n_pairs=pl.len(),
                n_sensors=pl.col("sensor_id").n_unique(),
                n_stations=pl.col("station_code").n_unique(),
                median_overlap_h=pl.col("overlap_hours").median(),
                total_overlap_h=pl.col("overlap_hours").sum(),
                humidity_share=pl.col("has_humidity").mean(),
                traffic_share=(pl.col("station_type") == "traffic").mean(),
            )
            .with_columns(radius_km=pl.lit(radius))
        )
    return (
        pl.concat(rows)
        .select(
            "country",
            "radius_km",
            "n_pairs",
            "n_sensors",
            "n_stations",
            "median_overlap_h",
            "total_overlap_h",
            "humidity_share",
            "traffic_share",
        )
        .sort("country", "radius_km")
    )


# ------------------------------------------------------------- timezone check
async def sensor_days(
    settings: Settings, sensor: dict, start: date, n_days: int, today: date
) -> pl.DataFrame:
    """Download and parse ``n_days`` archive files of one sensor (kept in memory only)."""
    files = await _download_days(settings, sensor, start, n_days, today)
    frames = [sc.read_day_csv(blob, gzipped=url.endswith(".gz")) for url, blob in files]
    frames = [f for f in frames if f.height]
    return pl.concat(frames, how="diagonal_relaxed") if frames else pl.DataFrame()


async def _download_days(
    settings: Settings, sensor: dict, start: date, n_days: int, today: date
) -> list[tuple[str, bytes]]:
    """Download ``n_days`` daily files of one sensor (for the size probe)."""
    cfg = settings.sensor_community
    days = [date.fromordinal(start.toordinal() + i) for i in range(n_days)]
    urls = [
        sc.file_url(
            cfg.archive_url,
            sensor_type=sensor["sensor_type"],
            sensor_id=sensor["sensor_id"],
            indoor=sensor.get("indoor", False),
            day=d,
            today=today,
        )
        for d in days
    ]
    async with polite_client(settings.http) as client:
        blobs = await asyncio.gather(*(sc.fetch_file(client, u) for u in urls))
    return [(u, b) for u, b in zip(urls, blobs, strict=True) if b is not None]


async def size_probe(
    settings: Settings, sensors: pl.DataFrame, start: date, n_days: int, today: date
) -> pl.DataFrame:
    """Measured size of real sensor-day files, one row per sensor.

    Compares the gzip CSV with Parquet zstd written one file per day, and with
    the same days stored together in one Parquet file (bronze partitions group
    many days, which amortises the per-file Parquet overhead).
    """
    rows = []
    for sensor in sensors.iter_rows(named=True):
        files = await _download_days(settings, sensor, start, n_days, today)
        frames = [sc.read_day_csv(blob, gzipped=url.endswith(".gz")) for url, blob in files]
        if not frames:
            continue
        rows.append(
            {
                "sensor_type": sensor["sensor_type"],
                "sensor_id": sensor["sensor_id"],
                "days": len(frames),
                "rows": sum(f.height for f in frames),
                "gz_bytes": sum(len(blob) for _, blob in files),
                "parquet_daily_bytes": sum(_parquet_size(f) for f in frames),
                "parquet_batched_bytes": _parquet_size(pl.concat(frames, how="diagonal_relaxed")),
            }
        )
    return pl.DataFrame(rows)


def _parquet_size(frame: pl.DataFrame) -> int:
    """Size in bytes of a frame written as zstd Parquet."""
    buffer = io.BytesIO()
    frame.write_parquet(buffer, compression="zstd")
    return buffer.tell()


async def timezone_check(settings: Settings, pairs: pl.DataFrame, today: date) -> pl.DataFrame:
    """Lag (hours) maximising the correlation between close sensors and their station."""
    d = settings.discovery
    close = (
        pairs.filter(pl.col("distance_km") <= d.lag_check_max_distance_km, pl.col("sensor_type") == "sds011")
        .sort("distance_km")
        .unique("sampling_point", keep="first")
        .sort("distance_km")
        .group_by("country", maintain_order=True)
        .head(d.lag_check_pairs_per_country)
    )
    results = []
    for pair in close.iter_rows(named=True):
        raw = await sensor_days(settings, pair, d.lag_check_start, d.lag_check_days, today)
        if raw.is_empty():
            continue
        sensor = (
            hourly_mean(
                raw.select(
                    ts=pl.col("timestamp").str.to_datetime("%Y-%m-%dT%H:%M:%S", strict=False),
                    pm25=pl.col("P2").cast(pl.Float64, strict=False),
                ),
                "ts",
                "pm25",
                by=[],
            )
            .select("hour", value=pl.col("mean"))
            .collect()
        )
        ref = (
            eea.scan_observations(eea_raw_path(settings, "pm25", pair["country"], pair["sampling_point"]))
            .filter(pl.col("validity").is_in(settings.eea.valid_codes))
            .select(hour=pl.col("start"), value=pl.col("value"))
            .collect()
        )
        lags = lagged_correlation(sensor, ref, d.lag_check_max_lag_h)
        results.append(
            lags.with_columns(
                country=pl.lit(pair["country"]),
                sensor_id=pl.lit(pair["sensor_id"]),
                sampling_point=pl.lit(pair["sampling_point"]),
                distance_km=pl.lit(pair["distance_km"]),
            )
        )
    return pl.concat(results) if results else pl.DataFrame()


# ------------------------------------------------------------------ orchestration
async def run(settings: Settings, today: date) -> dict[str, pl.DataFrame]:
    """Run the whole feasibility study; every intermediate table is cached in ``data/meta``."""
    cfg, meta = settings.sensor_community, _meta(settings)
    live = await live_snapshot(settings, today)
    listings = await collect_listings(settings, today)
    candidates = head_candidates(
        listings, live, cfg.pm_types + cfg.humidity_types, settings.discovery.countries
    )
    positions = await resolve_positions(settings, candidates, today)
    inventory = sensor_inventory(listings, positions, settings)
    inventory.write_parquet(meta / "sc_inventory.parquet")

    stations = await eea_stations(settings, "pm25")
    max_km = max(settings.discovery.radii_km)
    outdoor_pm = inventory.filter(pl.col("kind") == "pm", ~pl.col("indoor"))
    near = pairs_within(outdoor_pm.select("sensor_id", "lat", "lon"), stations, max_km).collect()
    near_stations = stations.join(near.select("sampling_point").unique(), on="sampling_point")
    availability_path = meta / "eea_pm25_monthly_valid_hours.parquet"
    if availability_path.exists():
        # The raw station files are temporary (deleted once in bronze); what discovery needs from
        # them, the monthly valid hours, is cached here.
        files = pl.DataFrame(schema={"sampling_point": pl.String, "path": pl.String, "bytes": pl.Int64})
        availability = pl.read_parquet(availability_path)
    else:
        files = await download_station_files(settings, near_stations, "pm25")
        availability = station_availability(settings, files)
        availability.write_parquet(availability_path)

    pairs = pair_table(inventory, stations, availability, max_km)
    pairs.write_parquet(meta / "candidate_pairs.parquet")
    summary = feasibility_summary(pairs, settings.discovery.radii_km)
    summary.write_parquet(meta / "feasibility_summary.parquet")

    lag_path = meta / "eea_clock_lag_check.parquet"
    if lag_path.exists():
        lags = pl.read_parquet(lag_path)
    else:
        lags = await timezone_check(settings, pairs, today)
        lags.write_parquet(lag_path)
    return {
        "live": live,
        "listings": listings,
        "candidates": candidates,
        "positions": positions,
        "inventory": inventory,
        "stations": stations,
        "station_files": files,
        "availability": availability,
        "pairs": pairs,
        "summary": summary,
        "lags": lags,
    }
