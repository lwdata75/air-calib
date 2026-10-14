"""Model features built from a pair's own hourly series (no reference value is ever used).

Every dynamic feature looks at the **past only**: a value at hour ``h`` depends on
hours ``<= h`` (rolling statistics) or ``< h`` (missing rates), never on a later
hour. ``tests/test_features.py`` checks it by altering the future.
"""

from __future__ import annotations

import math

import polars as pl

from aircalib.config import Features


def log_floor(value: pl.Expr, floor: float) -> pl.Expr:
    """``ln(max(value, floor))``; null stays null. The floor is documented in ``features.log_floor_ugm3``."""
    # max_horizontal ignores nulls: a missing value must stay missing, not become the floor.
    floored = pl.max_horizontal(value, pl.lit(floor)).log()
    return pl.when(value.is_not_null()).then(floored).alias(value.meta.output_name())


def dew_point(temperature: pl.Expr, rh: pl.Expr, cfg: Features) -> pl.Expr:
    """Dew point (°C), Magnus formula: ``Td = b γ / (a - γ)`` with ``γ = ln(RH/100) + a T / (b + T)``."""
    gamma = (rh / 100).log() + cfg.dew_point_a * temperature / (cfg.dew_point_b + temperature)
    return pl.when(rh > 0).then(cfg.dew_point_b * gamma / (cfg.dew_point_a - gamma))


def cyclic(value: pl.Expr, period: float, name: str) -> list[pl.Expr]:
    """``sin`` and ``cos`` of a periodic value: 23 h and 0 h end up close, as they are in reality."""
    angle = 2 * math.pi * value / period
    return [angle.sin().alias(f"{name}_sin"), angle.cos().alias(f"{name}_cos")]


def time_features(hour_utc: pl.Expr, timezone: str) -> list[pl.Expr]:
    """Hour of the day, day of the week and month in local civil time, each encoded cyclically."""
    local = hour_utc.dt.replace_time_zone("UTC").dt.convert_time_zone(timezone)
    return [
        *cyclic(local.dt.hour(), 24, "hour"),
        *cyclic(local.dt.weekday() - 1, 7, "weekday"),
        *cyclic(local.dt.month() - 1, 12, "month"),
    ]


def rolling_past(column: str, hours: list[int], by: str = "pair_id", time: str = "hour") -> list[pl.Expr]:
    """Mean and standard deviation of ``column`` over the hours ``(h - w, h]`` of the same ``by`` group.

    Time-based windows: a gap in the series shortens the window instead of reaching
    further back. The frame must be sorted by ``by`` then ``time``.
    """
    exprs = []
    for w in hours:
        window = f"{w}h"
        exprs.append(
            pl.col(column).rolling_mean_by(time, window, closed="right").over(by).alias(f"{column}_mean_{w}h")
        )
        exprs.append(
            pl.col(column).rolling_std_by(time, window, closed="right").over(by).alias(f"{column}_std_{w}h")
        )
    return exprs


def missing_rate_past(valid: str, hours: list[int], by: str = "pair_id", time: str = "hour") -> list[pl.Expr]:
    """Share of the previous ``w`` hours ``[h - w, h)`` without a valid value (current hour excluded).

    ``valid`` is a boolean column; hours absent from the frame count as missing.
    """
    count = pl.col(valid).cast(pl.Float64)
    return [
        (1 - count.rolling_sum_by(time, f"{w}h", closed="left").over(by).fill_null(0) / w).alias(
            f"missing_rate_{w}h"
        )
        for w in hours
    ]
