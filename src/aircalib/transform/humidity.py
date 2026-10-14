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
* ``usable``: everything else.
"""

from __future__ import annotations

import itertools

import polars as pl

from aircalib.config import Humidity

MISSING, USABLE = "missing", "usable"
OUT_OF_RANGE, STUCK_LOW, SATURATED = "out_of_range", "stuck_low", "saturated"
FAULTS = (SATURATED, STUCK_LOW, OUT_OF_RANGE)


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


def _shares(status: pl.Expr) -> list[pl.Expr]:
    return [(status == s).mean().alias(f"{s}_share") for s in (*FAULTS, USABLE)]


def status_shares(lf: pl.LazyFrame | pl.DataFrame, by: list[str], cfg: Humidity) -> pl.LazyFrame:
    """Per group: readings present (``n``), missing ones, and the share of each status among the present."""
    status = reading_status(pl.col("humidity"), cfg)
    return (
        lf.lazy()
        .with_columns(status=status)
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


def hourly_humidity(lf: pl.LazyFrame | pl.DataFrame, cfg: Humidity) -> pl.LazyFrame:
    """Per probe and UTC hour, over the readings present:

    * ``humidity``: mean of the usable readings (null when there are none);
    * ``raw_humidity``: mean of the physically possible readings (0–``max_pct``), faults included,
      to show what the probe claimed;
    * the share of each status, ``faulty`` (more than ``faulty_hour_share`` not usable) and the
      dominant ``fault`` of a faulty hour.
    """
    rh = pl.col("humidity")
    status = pl.col("status")
    possible = status != OUT_OF_RANGE
    sat, low, oor = (pl.col(f"{s}_share") for s in FAULTS)
    return (
        lf.lazy()
        .filter(pl.col("timestamp").is_not_null())
        .with_columns(status=reading_status(rh, cfg))
        .filter(status != MISSING)
        .group_by("sensor_id", "sensor_type", hour=pl.col("timestamp").dt.truncate("1h"))
        .agg(
            *_shares(status),
            humidity=rh.filter(status == USABLE).mean(),
            raw_humidity=rh.filter(possible).mean(),
            n=pl.len(),
        )
        .with_columns(faulty=(1 - pl.col(f"{USABLE}_share")) > cfg.faulty_hour_share)
        .with_columns(
            fault=pl.when(~pl.col("faulty"))
            .then(None)
            .when((sat >= low) & (sat >= oor))
            .then(pl.lit(SATURATED))
            .when(low >= oor)
            .then(pl.lit(STUCK_LOW))
            .otherwise(pl.lit(OUT_OF_RANGE))
        )
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
