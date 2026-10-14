"""SDS011 readings: physical and coherence rules, then hourly aggregation.

Every reading keeps its row and gets a ``quality_flag``: ``ok`` or the first
rule it fails, in the order of ``RULES`` (that order is the funnel order).

* ``pm_missing``: PM10 or PM2.5 empty or ``NaN``;
* ``duplicate``: same sensor and timestamp as an earlier reading with the same values
  (the first copy is kept);
* ``duplicate_conflict``: same sensor and timestamp with *different* values: nobody can
  tell which one is true, all copies are flagged;
* ``pm_negative``: a negative value (the firmware writes -1/-1 for an error);
* ``pm_saturated``: the sensor's cap (PM2.5 = 999.9 with PM10 = 1999.9);
* ``pm_above_range``: above the SDS011 specification range (999.9 ug/m3);
* ``pm25_above_pm10``: PM2.5 is part of PM10, so PM2.5 > PM10 is incoherent;
* ``pm_stuck``: identical (PM10, PM2.5) readings for ``particles.stuck_min_hours`` or more,
  **and** either a known failure code (``particles.stuck_codes``, e.g. 0/0) or a reference
  station that moved by at least ``particles.stuck_reference_range_ugm3`` meanwhile. A small
  value repeated in calm, clean air is a plausible reading and stays ``ok``.

There is deliberately **no statistical outlier filter** (z-score, IQR...): the
peaks seen in humid air are exactly what the model has to learn to correct.
"""

from __future__ import annotations

import polars as pl

from aircalib.config import Particles
from aircalib.transform.runs import run_id

OK = "ok"
PM_MISSING = "pm_missing"
DUPLICATE = "duplicate"
DUPLICATE_CONFLICT = "duplicate_conflict"
PM_NEGATIVE = "pm_negative"
PM_SATURATED = "pm_saturated"
PM_ABOVE_RANGE = "pm_above_range"
PM25_ABOVE_PM10 = "pm25_above_pm10"
PM_STUCK = "pm_stuck"
RULES = (
    PM_MISSING,
    DUPLICATE,
    DUPLICATE_CONFLICT,
    PM_NEGATIVE,
    PM_SATURATED,
    PM_ABOVE_RANGE,
    PM25_ABOVE_PM10,
    PM_STUCK,
)

# Hour-level statuses (after aggregation).
NO_VALID_READING = "no_valid_reading"
INCOMPLETE_HOUR = "incomplete_hour"

FLAG = "quality_flag"
KEYS = ["sensor_id", "timestamp"]


def _flag(rule: str, mask: pl.Expr) -> pl.Expr:
    """Set ``rule`` on still-unflagged rows where ``mask`` holds."""
    unflagged = pl.col(FLAG).is_null()
    return pl.when(unflagged & mask.fill_null(False)).then(pl.lit(rule)).otherwise(pl.col(FLAG)).alias(FLAG)


def _stuck(out: pl.DataFrame, cfg: Particles, reference: pl.DataFrame | None) -> pl.Expr:
    """Mask of the readings of a long plateau that is a failure code or contradicted by the station."""
    t = pl.col("timestamp")
    out = out.with_columns(
        _run=run_id(
            pl.col(FLAG).is_null(),
            ["P1", "P2"],
            by="sensor_id",
            time_col="timestamp",
            max_gap_hours=cfg.stuck_min_hours,
        )
    )
    long_run = pl.col(FLAG).is_null() & (
        (t.max() - t.min()).over("_run") >= pl.duration(hours=cfg.stuck_min_hours)
    )
    code = pl.any_horizontal([(pl.col("P1") == p1) & (pl.col("P2") == p2) for p1, p2 in cfg.stuck_codes])
    out = out.with_columns(_long=long_run, _code=code)
    moved = pl.lit(False)
    if reference is not None:
        keys = [c for c in ("sensor_id", "segment") if c in reference.columns and c in out.columns]
        ranges = (
            out.filter(pl.col("_long") & ~pl.col("_code"))
            .select("_run", *keys, hour=t.dt.truncate("1h"))
            .unique()
            .join(reference, on=[*keys, "hour"])
            .group_by("_run")
            .agg(_range=pl.col("reference").max() - pl.col("reference").min())
        )
        moving = ranges.filter(pl.col("_range") >= cfg.stuck_reference_range_ugm3)["_run"]
        moved = pl.col("_run").is_in(moving.implode())
    return out.select(pl.col("_long") & (pl.col("_code") | moved)).to_series()


def flag_readings(
    readings: pl.DataFrame, cfg: Particles, reference: pl.DataFrame | None = None
) -> pl.DataFrame:
    """Add ``quality_flag`` to raw SDS011 readings (``sensor_id, timestamp, P1, P2``); no row is removed.

    ``reference`` (``sensor_id[, segment], hour, reference``: valid hourly PM2.5 of the paired
    station) lets a plateau of ordinary values count as stuck when the station moved meanwhile;
    without it only the failure codes do. The result is sorted by sensor and timestamp.
    """
    p1, p2 = pl.col("P1"), pl.col("P2")
    unflagged = pl.col(FLAG).is_null()
    out = readings.sort(*KEYS, maintain_order=True).with_columns(pl.lit(None, dtype=pl.String).alias(FLAG))
    out = out.with_columns(_flag(PM_MISSING, p1.is_null() | p2.is_null() | p1.is_nan() | p2.is_nan()))

    # Duplicates among the readings still in play.
    copies = unflagged.cast(pl.Int32).sum().over(KEYS)
    variants = pl.struct("P1", "P2").filter(unflagged).n_unique().over(KEYS)
    nth_copy = unflagged.cast(pl.Int32).cum_sum().over(KEYS)
    out = out.with_columns(_copies=copies, _variants=variants, _nth=nth_copy)
    out = out.with_columns(
        _flag(DUPLICATE, (pl.col("_copies") > 1) & (pl.col("_variants") == 1) & (pl.col("_nth") > 1))
    )
    out = out.with_columns(_flag(DUPLICATE_CONFLICT, (pl.col("_copies") > 1) & (pl.col("_variants") > 1)))
    out = out.drop("_copies", "_variants", "_nth")

    out = out.with_columns(_flag(PM_NEGATIVE, (p1 < 0) | (p2 < 0)))
    out = out.with_columns(
        _flag(PM_SATURATED, (p2 >= cfg.pm25_saturation_ugm3) | (p1 >= cfg.pm10_saturation_ugm3))
    )
    out = out.with_columns(_flag(PM_ABOVE_RANGE, (p1 > cfg.max_ugm3) | (p2 > cfg.max_ugm3)))
    out = out.with_columns(_flag(PM25_ABOVE_PM10, p2 > p1))
    out = out.with_columns(_stuck=_stuck(out, cfg, reference))
    out = out.with_columns(_flag(PM_STUCK, pl.col("_stuck"))).drop("_stuck")
    return out.with_columns(pl.col(FLAG).fill_null(OK))


def monthly_cadence(readings: pl.DataFrame | pl.LazyFrame, max_gap_s: int) -> pl.LazyFrame:
    """Reporting cadence of each sensor and month: median gap (s) between consecutive distinct timestamps.

    All readings count (whatever their flag: the cadence is the sensor's rhythm,
    not its quality); gaps longer than ``max_gap_s`` are outages and are ignored.
    """
    t = pl.col("timestamp")
    return (
        readings.lazy()
        .select("sensor_id", "timestamp")
        .unique()
        .sort("sensor_id", "timestamp")
        .with_columns(gap_s=(t - t.shift().over("sensor_id")).dt.total_seconds())
        .filter(pl.col("gap_s").is_between(1, max_gap_s))
        .group_by("sensor_id", month=t.dt.truncate("1mo"))
        .agg(cadence_s=pl.col("gap_s").median())
    )


def _stats(column: str, name: str) -> list[pl.Expr]:
    value = pl.col(column).filter(pl.col(FLAG) == OK)
    return [
        value.mean().alias(f"{name}_mean"),
        value.median().alias(f"{name}_median"),
        value.std().alias(f"{name}_std"),
    ]


def hourly_particles(flagged: pl.DataFrame | pl.LazyFrame, cfg: Particles, by: list[str]) -> pl.LazyFrame:
    """Per ``by`` group (e.g. sensor, segment) and UTC hour: statistics of valid readings, completeness.

    ``completeness = n_valid / expected`` where ``expected = 3600 / cadence`` uses the
    sensor's own cadence that month (cadences differ between sensors). ``hour_flag`` is
    ``ok``, ``no_valid_reading`` (every reading flagged; ``main_flag`` says why) or
    ``incomplete_hour`` (completeness below ``particles.min_completeness``).
    """
    lf = flagged.lazy()
    cadence = monthly_cadence(lf, cfg.cadence_max_gap_s)
    flags = pl.col(FLAG)
    hourly = lf.group_by(*by, hour=pl.col("timestamp").dt.truncate("1h")).agg(
        *_stats("P2", "pm25"),
        *_stats("P1", "pm10"),
        n_readings=pl.len(),
        n_valid=(flags == OK).sum(),
        main_flag=flags.filter(flags != OK).mode().sort().first(),
    )
    return (
        hourly.with_columns(month=pl.col("hour").dt.truncate("1mo"))
        .join(cadence, on=["sensor_id", "month"], how="left")
        .drop("month")
        .with_columns(expected_readings=3600 / pl.col("cadence_s"))
        .with_columns(completeness=pl.col("n_valid") / pl.col("expected_readings"))
        .with_columns(
            hour_flag=pl.when(pl.col("n_valid") == 0)
            .then(pl.lit(NO_VALID_READING))
            .when(pl.col("completeness").fill_null(0) < cfg.min_completeness)
            .then(pl.lit(INCOMPLETE_HOUR))
            .otherwise(pl.lit(OK))
        )
        .sort(*by, "hour")
    )
