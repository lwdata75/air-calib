"""Humidity probe quality: which readings are measurements, which are probe failures.

A cheap probe (DHT22, BME280) that has been wet for a long time often stays
stuck at, or very close to, 100 % relative humidity. Real data show other
failure modes: a DHT22 stuck at exactly 1.0 %, a BME280 returning ``NaN`` or an
integer overflow (255, 655.35), a HTU21D pinned to the ends of its conversion
formula (-6 %, 119 %). None of these is a measurement: in silver they are
treated as missing (never as a true value) and replaced by the nearest official
weather station.

Every reading gets one status (thresholds from ``configs/default.yaml``):

* ``missing``: null or ``NaN``;
* ``out_of_range``: below 0 or above ``max_pct``;
* ``stuck_low``: below ``floor_pct`` (official stations almost never read that dry);
* ``saturated``: at or above ``saturation_pct``;
* ``stuck``: a usable value repeated identically for ``stuck_min_hours`` or more
  (needs the readings in time order, see ``with_status``);
* ``usable``: everything else.
"""

from __future__ import annotations

import itertools

import polars as pl

from aircalib.config import Humidity
from aircalib.transform.runs import in_constant_run

MISSING, USABLE = "missing", "usable"
OUT_OF_RANGE, STUCK_LOW, SATURATED, STUCK = "out_of_range", "stuck_low", "saturated", "stuck"
FAULTS = (SATURATED, STUCK, STUCK_LOW, OUT_OF_RANGE)  # order = priority when shares tie


def is_saturated(rh: pl.Expr, saturation_pct: float) -> pl.Expr:
    """Saturated reading; ``NaN`` is not (Polars orders NaN above every number)."""
    return rh.is_not_nan() & (rh >= saturation_pct)


def reading_status(rh: pl.Expr, cfg: Humidity) -> pl.Expr:
    """Status of one humidity reading (see the module docstring)."""
    return (
        pl.when(rh.is_null() | rh.is_nan())
        .then(pl.lit(MISSING))
        .when((rh < 0) | (rh > cfg.max_pct))
        .then(pl.lit(OUT_OF_RANGE))
        .when(rh < cfg.floor_pct)
        .then(pl.lit(STUCK_LOW))
        .when(rh >= cfg.saturation_pct)
        .then(pl.lit(SATURATED))
        .otherwise(pl.lit(USABLE))
    )


def with_status(lf: pl.LazyFrame | pl.DataFrame, cfg: Humidity) -> pl.LazyFrame:
    """Readings sorted by probe and time with their ``status``, stuck runs included."""
    usable = pl.col("status") == USABLE
    stuck = in_constant_run(
        usable, ["humidity"], by="sensor_id", time_col="timestamp", min_hours=cfg.stuck_min_hours
    )
    return (
        lf.lazy()
        .filter(pl.col("timestamp").is_not_null())
        .sort("sensor_id", "timestamp")
        .with_columns(status=reading_status(pl.col("humidity"), cfg))
        .with_columns(status=pl.when(stuck).then(pl.lit(STUCK)).otherwise(pl.col("status")))
    )


def _shares(status: pl.Expr) -> list[pl.Expr]:
    return [(status == s).mean().alias(f"{s}_share") for s in (*FAULTS, USABLE)]


def status_shares(lf: pl.LazyFrame | pl.DataFrame, by: list[str], cfg: Humidity) -> pl.LazyFrame:
    """Per group: readings present (``n``), missing ones, and the share of each status among the present."""
    return (
        with_status(lf, cfg)
        .group_by(by)
        .agg(
            *_shares(pl.col("status").filter(pl.col("status") != MISSING)),
            n_missing=(pl.col("status") == MISSING).sum(),
            n=(pl.col("status") != MISSING).sum(),
        )
        .sort(by)
    )


def age_class(age_years: pl.Expr, breaks: list[float]) -> pl.Expr:
    """Age bucket (e.g. ``[1, 2, 4]`` → ``<1``, ``1-2``, ``2-4``, ``>=4`` years); null stays null."""
    expr = pl.when(age_years < breaks[0]).then(pl.lit(f"<{breaks[0]:g}"))
    for low, high in itertools.pairwise(breaks):
        expr = expr.when(age_years < high).then(pl.lit(f"{low:g}-{high:g}"))
    return expr.when(age_years >= breaks[-1]).then(pl.lit(f">={breaks[-1]:g}"))


def hourly_humidity(
    lf: pl.LazyFrame | pl.DataFrame, cfg: Humidity, by: tuple[str, ...] = ("sensor_id", "sensor_type")
) -> pl.LazyFrame:
    """Per probe (``by``) and UTC hour, over the readings present:

    * ``humidity``, ``temperature``: means over the usable readings (null when there are none);
    * ``raw_humidity``: mean of the physically possible readings (0–``max_pct``), faults included,
      to show what the probe claimed;
    * the share of each status, ``faulty`` (more than ``faulty_hour_share`` not usable) and the
      dominant ``fault`` of a faulty hour.
    """
    rh = pl.col("humidity")
    status = pl.col("status")
    possible = status != OUT_OF_RANGE
    shares = [pl.col(f"{s}_share") for s in FAULTS]
    worst = pl.max_horizontal(shares)
    fault = pl.when(~pl.col("faulty")).then(None)
    for name, share in zip(FAULTS, shares, strict=True):
        fault = fault.when(share == worst).then(pl.lit(name))
    return (
        with_status(lf, cfg)
        .filter(status != MISSING)
        .group_by(*by, hour=pl.col("timestamp").dt.truncate("1h"))
        .agg(
            *_shares(status),
            humidity=rh.filter(status == USABLE).mean(),
            temperature=pl.col("temperature").filter(status == USABLE).mean(),
            raw_humidity=rh.filter(possible).mean(),
            n=pl.len(),
        )
        .with_columns(faulty=(1 - pl.col(f"{USABLE}_share")) > cfg.faulty_hour_share)
        .with_columns(fault=fault)
    )


def saturated_vs_official(
    probe_hourly: pl.DataFrame, official_hourly: pl.DataFrame, saturation_pct: float
) -> pl.DataFrame:
    """Official humidity during the hours where a probe is faulty because it is saturated.

    ``official_hourly`` has ``sensor_id, hour, official_humidity`` (the official
    station linked to each probe). A probe at 100 % while the official station
    reads 70 % is a broken probe, not fog.
    """
    return (
        probe_hourly.filter(pl.col("fault") == SATURATED)
        .join(official_hourly, on=["sensor_id", "hour"], how="inner")
        .with_columns(official_saturated=is_saturated(pl.col("official_humidity"), saturation_pct))
    )


# ------------------------------------------------------------------ dead vs episodic
DEAD, HEALTHY = "dead", "healthy"


def monthly_quality(probe_hourly: pl.LazyFrame | pl.DataFrame) -> pl.LazyFrame:
    """Per probe and month: hours with data, faulty hours (total and by dominant fault) and their share."""
    return (
        probe_hourly.lazy()
        .group_by("sensor_id", "sensor_type", month=pl.col("hour").dt.truncate("1mo").dt.date())
        .agg(
            *[(pl.col("fault") == f).sum().alias(f"{f}_hours") for f in FAULTS],
            hours=pl.len(),
            faulty_hours=pl.col("faulty").sum(),
        )
        .with_columns(faulty_month_share=pl.col("faulty_hours") / pl.col("hours"))
        .sort("sensor_id", "month")
    )


def classify_months(monthly: pl.LazyFrame | pl.DataFrame, dead_month_share: float) -> pl.LazyFrame:
    """Tell a *dead* probe-month from a healthy one with episodic faults.

    * dead: faulty in more than ``dead_month_share`` of its hours → the whole
      month is "no reliable local humidity", even the usable hours (a probe
      stuck near 100 % is not trustworthy when it briefly reads 97 %);
    * healthy: only the faulty hours are discarded; the others are recoverable.
    """
    dead = pl.col("faulty_month_share") > dead_month_share
    return monthly.lazy().with_columns(
        status=pl.when(dead).then(pl.lit(DEAD)).otherwise(pl.lit(HEALTHY)),
        recoverable_hours=pl.when(dead).then(0).otherwise(pl.col("hours") - pl.col("faulty_hours")),
    )


def probe_status(classified: pl.LazyFrame | pl.DataFrame) -> pl.LazyFrame:
    """One row per probe: months observed, dead months, and a class over the whole period."""
    return (
        classified.lazy()
        .group_by("sensor_id", "sensor_type")
        .agg(
            months=pl.len(),
            dead_months=(pl.col("status") == DEAD).sum(),
            hours=pl.col("hours").sum(),
            faulty_hours=pl.col("faulty_hours").sum(),
            recoverable_hours=pl.col("recoverable_hours").sum(),
        )
        .with_columns(
            probe_class=pl.when(pl.col("dead_months") == 0)
            .then(pl.lit("saine"))
            .when(pl.col("dead_months") == pl.col("months"))
            .then(pl.lit("morte toute la période"))
            .otherwise(pl.lit("morte certains mois"))
        )
        .sort("sensor_id")
    )
