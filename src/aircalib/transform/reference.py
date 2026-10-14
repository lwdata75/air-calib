"""EEA reference hours: clock correction and quality rules.

Each hour keeps its row and gets a ``quality_flag`` (first failing rule):

* ``outside_period``: UTC hour outside the study period (bronze keeps a margin);
* ``invalid``: validity code not accepted (``eea.valid_codes``) or the ``-999`` sentinel;
* ``not_verified``: not verified by the agency (E1a should always be);
* ``negative_beyond_uncertainty``: below ``eea.negative_floor_ugm3``. Validated values
  between that floor and 0 are instrument noise around zero and are kept *as reported*:
  clipping them to 0 or dropping them would bias the target upwards in clean air.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta

import polars as pl

from aircalib.config import Eea

OK = "ok"
OUTSIDE_PERIOD = "outside_period"
INVALID = "invalid"
NOT_VERIFIED = "not_verified"
NEGATIVE = "negative_beyond_uncertainty"
RULES = (OUTSIDE_PERIOD, INVALID, NOT_VERIFIED, NEGATIVE)


def utc_hour(start: pl.Expr, offset_h: int) -> pl.Expr:
    """UTC hour label of an EEA row: ``Start`` is on a fixed UTC+offset clock (measured in step 1)."""
    return start - pl.duration(hours=offset_h)


def flag_reference(lf: pl.LazyFrame | pl.DataFrame, cfg: Eea, start: date, end: date) -> pl.LazyFrame:
    """Hourly EEA rows (bronze) → ``sampling_point, pollutant, hour, value, quality_flag``.

    ``pollutant`` becomes the project name (``pm25``, ``pm10``) and ``hour`` the UTC
    hour start. ``value`` is unchanged; only rows flagged ``ok`` are usable.
    """
    names = {code: name for name, code in cfg.pollutants.items()}
    first = datetime.combine(start, time())
    after = datetime.combine(end + timedelta(days=1), time())
    hour = pl.col("hour")
    value = pl.col("value")
    flag = (
        pl.when((hour < first) | (hour >= after))
        .then(pl.lit(OUTSIDE_PERIOD))
        .when(~pl.col("validity").is_in(cfg.valid_codes) | value.is_null() | (value == cfg.missing_sentinel))
        .then(pl.lit(INVALID))
        .when(~pl.col("verification").is_in(cfg.verified_codes))
        .then(pl.lit(NOT_VERIFIED))
        .when(value < cfg.negative_floor_ugm3)
        .then(pl.lit(NEGATIVE))
        .otherwise(pl.lit(OK))
    )
    return (
        lf.lazy()
        .with_columns(hour=utc_hour(pl.col("start"), cfg.clock_offset_h))
        .with_columns(
            pollutant=pl.col("pollutant").replace_strict(names, return_dtype=pl.String),
            quality_flag=flag,
        )
        .select("sampling_point", "pollutant", "hour", "value", "validity", "verification", "quality_flag")
    )
