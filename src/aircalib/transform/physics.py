"""Physical model of the raw sensor: a gain and hygroscopic growth.

Optical sensors (SDS011) count particles by the light they scatter. Two effects
make them differ from the reference, which dries and weighs its sample:

* a **gain** ``a``: in dry air the raw SDS011 reads only ~0.42 of the reference
  (notebook 04); the most likely cause is that it misses the finest particles,
  which carry much of the PM2.5 mass (hypothesis);
* **hygroscopic growth**: in humid air soluble particles take up water, scatter
  more light, and the sensor reads more. The kappa-Kohler theory gives the mass
  growth factor as a function of the water activity ``a_w = RH / 100``:

      C(RH) = 1 + (kappa / density_ratio) / (1 / a_w - 1)

Combined, ``PM_ref ~ a * PM_raw / C(RH)``, linear in logarithms:

      ln PM_ref = ln a + ln PM_raw - ln C(RH)

so for a given ``kappa`` the best ``ln a`` is a mean, and ``kappa`` is found by a
one-dimensional search. ``C`` diverges as RH tends to 100 %: RH is capped at
``physics.rh_cap_pct`` (official humidity, where 100 % is real fog). A probe's
saturated reading never reaches this module: it is missing upstream.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
import polars as pl

from aircalib.config import Physics


def growth_factor(rh: pl.Expr, kappa: float, cfg: Physics) -> pl.Expr:
    """Mass growth factor ``C`` (>= 1) at ``min(RH, rh_cap_pct)``; null when RH is missing, NaN or <= 0."""
    usable = rh.is_not_null() & rh.is_not_nan() & (rh > 0)
    water_activity = pl.min_horizontal(rh, pl.lit(cfg.rh_cap_pct)) / 100.0
    factor = 1.0 + (kappa / cfg.density_ratio) / (1.0 / water_activity - 1.0)
    return pl.when(usable).then(factor).otherwise(None)


def physical_estimate(pm: pl.Expr, rh: pl.Expr, gain: float, kappa: float, cfg: Physics) -> pl.Expr:
    """Reference estimate of the physical model: ``a * PM_raw / C(RH)``."""
    return gain * pm / growth_factor(rh, kappa, cfg)


def _growth_terms(rh: np.ndarray, cfg: Physics) -> np.ndarray:
    """``a_w / (1 - a_w)`` = ``1 / (1/a_w - 1)`` at the capped humidity."""
    water_activity = np.minimum(rh, cfg.rh_cap_pct) / 100.0
    return water_activity / (1.0 - water_activity)


@dataclass(frozen=True)
class PhysicalFit:
    gain: float
    kappa: float
    mse_log: float  # mean squared error on ln PM
    mse_log_no_growth: float  # same with kappa = 0 (gain only), to judge what humidity adds
    n: int


def fit_gain_kappa(log_raw: np.ndarray, log_ref: np.ndarray, rh: np.ndarray, cfg: Physics) -> PhysicalFit:
    """Least squares fit of ``ln a`` and ``kappa`` in ``ln ref = ln a + ln raw - ln C(RH; kappa)``.

    Inputs are aligned arrays without missing values (caller's job). For each kappa
    the optimal ``ln a`` is the mean residual, so the error is a function of kappa
    alone: a coarse grid over ``kappa_bounds``, then golden-section refinement.
    """
    terms = _growth_terms(rh, cfg)
    base = log_ref - log_raw

    def error(kappa: float) -> tuple[float, float]:
        residual = base + np.log1p(kappa / cfg.density_ratio * terms)
        log_gain = float(residual.mean())
        return float(((residual - log_gain) ** 2).mean()), log_gain

    lo, hi = cfg.kappa_bounds
    grid = np.linspace(lo, hi, cfg.fit_grid_points)
    errors = [error(k)[0] for k in grid]
    i = int(np.argmin(errors))
    step = (hi - lo) / (cfg.fit_grid_points - 1)
    a, b = max(lo, grid[i] - step), min(hi, grid[i] + step)
    ratio = (math.sqrt(5) - 1) / 2
    while b - a > cfg.fit_tolerance:
        c, d = b - ratio * (b - a), a + ratio * (b - a)
        if error(c)[0] <= error(d)[0]:
            b = d
        else:
            a = c
    kappa = (a + b) / 2
    mse, log_gain = error(kappa)
    return PhysicalFit(
        gain=math.exp(log_gain), kappa=kappa, mse_log=mse, mse_log_no_growth=error(0.0)[0], n=len(base)
    )
