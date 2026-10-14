"""Physics of hygroscopic growth: why a humid particle looks bigger to an optical sensor.

Optical sensors (SDS011) count particles by the light they scatter. In humid
air, soluble particles absorb water and swell, so the sensor over-estimates the
dry mass that the reference station measures (the station dries its sample).
The kappa-Kohler theory gives the mass growth factor as a function of the water
activity ``a_w = RH / 100``:

    C = 1 + (kappa / density_ratio) / (1 / a_w - 1)

and the dry estimate is ``PM_dry = PM_wet / C``. ``C`` diverges when RH tends to
100 %, so the correction is **never** applied to a saturated reading nor above
``rh_max_pct``: the result is null there, and the model must rely on other inputs.
"""

from __future__ import annotations

import polars as pl

from aircalib.config import Physics


def kohler_growth_factor(rh: pl.Expr, physics: Physics, saturation_pct: float) -> pl.Expr:
    """Mass growth factor ``C`` (>= 1); null when RH is missing or NaN, <= 0, saturated or too high."""
    usable = rh.is_not_null() & rh.is_not_nan() & (rh > 0) & (rh < physics.rh_max_pct) & (rh < saturation_pct)
    water_activity = rh / 100.0
    factor = 1.0 + (physics.kappa / physics.density_ratio) / (1.0 / water_activity - 1.0)
    return pl.when(usable).then(factor).otherwise(None)


def kohler_dry_pm(pm: pl.Expr, rh: pl.Expr, physics: Physics, saturation_pct: float) -> pl.Expr:
    """PM corrected for hygroscopic growth; null where the correction is not defined."""
    return pm / kohler_growth_factor(rh, physics, saturation_pct)
