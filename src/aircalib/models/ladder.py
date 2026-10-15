"""The model ladder: from the raw sensor to the hybrid physics + gradient boosting.

Every model has ``fit(train)`` and ``predict(test)``; predictions are always in
ug/m3 (the EPA metrics are computed in ug/m3). Models learned on ``ln`` values
come back with Duan's smearing factor, computed on their **training** residuals.

0. ``RawSensor``: the uncorrected sensor;
1. ``LogLinear``: ``ln ref = b0 + b1 ln PM + b2 RH + b3 T`` (ordinary least squares);
2. ``Physics``: ``a PM / C(RH)`` with the fold's ``a, kappa`` (fitted without the fold);
3. ``Boosting``: LightGBM on every feature (``ln`` or ug/m3 target);
4. ``Boosting(residual_of="log_physics_pm25")``: hybrid, LightGBM learns what physics misses;
5. ``Boosting(quantiles=...)``: quantile regression for a 10-90 % interval.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import numpy as np
import polars as pl

from aircalib.evaluation.metrics import duan_smearing
from aircalib.models.data import LOG_TARGET, TARGET, matrix

if TYPE_CHECKING:
    import lightgbm as lgb


class RawSensor:
    """Rung 0: the raw sensor, no correction."""

    name = "0_capteur_brut"

    def fit(self, train: pl.DataFrame) -> None:  # noqa: ARG002 - nothing to learn
        """Nothing to learn."""
        return

    def predict(self, test: pl.DataFrame) -> dict[str, np.ndarray]:
        """The raw PM2.5 itself."""
        return {"prediction": test["pm25_mean"].to_numpy().astype(np.float64)}

    def params(self) -> dict:
        """No parameters."""
        return {}


@dataclass
class LogLinear:
    """Rung 1: ordinary least squares on ln values, back to ug/m3 with Duan's smearing."""

    features: list[str] = field(
        default_factory=lambda: ["log_pm25", "official_humidity", "official_temperature"]
    )
    name: str = "1_lineaire_log"

    def _x(self, frame: pl.DataFrame) -> np.ndarray:
        """Design matrix: intercept and features, missing values replaced by the training mean."""
        values = frame.select([pl.col(c).cast(pl.Float64).fill_null(self.means[c]) for c in self.features])
        return np.column_stack([np.ones(frame.height), values.to_numpy()])

    def fit(self, train: pl.DataFrame) -> None:
        """Least squares on the training rows; Duan's factor from the training residuals."""
        self.means = {c: float(train[c].cast(pl.Float64).mean()) for c in self.features}
        x, y = self._x(train), train[LOG_TARGET].to_numpy().astype(np.float64)
        self.coef, *_ = np.linalg.lstsq(x, y, rcond=None)
        self.smearing = duan_smearing(pl.Series(y - x @ self.coef))

    def predict(self, test: pl.DataFrame) -> dict[str, np.ndarray]:
        """Back-transformed prediction in ug/m3."""
        return {"prediction": np.exp(self._x(test) @ self.coef) * self.smearing}

    def params(self) -> dict:
        """The features used."""
        return {"features": ",".join(self.features)}


class Physics:
    """The physical estimate (computed by the caller with the fold's parameters)."""

    name = "2_physique"

    def fit(self, train: pl.DataFrame) -> None:  # noqa: ARG002 - a and kappa are fitted per fold upstream
        """Nothing to learn here: ``a`` and ``kappa`` are fitted per fold upstream."""
        return

    def predict(self, test: pl.DataFrame) -> dict[str, np.ndarray]:
        """The out-of-fold physical estimate."""
        return {"prediction": test["physics_pm25"].to_numpy().astype(np.float64)}

    def params(self) -> dict:
        """No parameters."""
        return {}


@dataclass
class Boosting:
    """LightGBM regression; ``log_target`` on ``ln ref`` (Duan back-transform) or on ug/m3."""

    features: list[str]
    categories: dict[str, list[str]]
    lgb_params: dict
    num_boost_round: int
    log_target: bool = True
    residual_of: str | None = None  # hybrid: learn ln ref - this column
    monotone: tuple[str, ...] = ()  # features with a non-decreasing effect
    quantiles: tuple[float, ...] = ()  # quantile regression instead of the mean
    name: str = "3_lightgbm"
    importance: dict[str, float] = field(default_factory=dict)

    def _target(self, frame: pl.DataFrame) -> np.ndarray:
        """Training target: ln ref, ug/m3, or the ln residual of the hybrid."""
        if self.residual_of:
            return (frame[LOG_TARGET] - frame[self.residual_of]).to_numpy().astype(np.float64)
        return frame[LOG_TARGET if self.log_target else TARGET].to_numpy().astype(np.float64)

    def _offset(self, frame: pl.DataFrame) -> np.ndarray:
        """The log offset added back to a hybrid prediction (0 otherwise)."""
        return frame[self.residual_of].to_numpy().astype(np.float64) if self.residual_of else 0.0

    def _train_one(self, x: np.ndarray, y: np.ndarray, extra: dict) -> lgb.Booster:
        """Train one LightGBM booster (categoricals, monotone constraints, extra objective)."""
        import lightgbm as lgb  # noqa: PLC0415 - heavy import, needs the OpenMP runtime (models.runtime)

        cat_idx = [i for i, c in enumerate(self.features) if c in self.categories]
        params = {**self.lgb_params, **extra, "verbose": -1}
        if self.monotone:
            params["monotone_constraints"] = [1 if c in self.monotone else 0 for c in self.features]
            params["monotone_constraints_method"] = "advanced"
        data = lgb.Dataset(x, y, feature_name=self.features, categorical_feature=cat_idx, free_raw_data=True)
        return lgb.train(params, data, num_boost_round=self.num_boost_round)

    def fit(self, train: pl.DataFrame) -> None:
        """Train one booster (or one per quantile); importances from the main booster."""
        x, y = matrix(train, self.features, self.categories), self._target(train)
        if self.quantiles:
            self.boosters = {
                q: self._train_one(x, y, {"objective": "quantile", "alpha": q}) for q in self.quantiles
            }
            booster = self.boosters[sorted(self.quantiles)[len(self.quantiles) // 2]]
        else:
            booster = self._train_one(x, y, {"objective": "regression"})
            self.boosters = {None: booster}
            if self.log_target or self.residual_of:
                self.smearing = duan_smearing(pl.Series(y - booster.predict(x)))
        gains = booster.feature_importance(importance_type="gain")
        total = float(gains.sum()) or 1.0
        self.importance = {c: float(g) / total for c, g in zip(self.features, gains, strict=True)}

    def predict(self, test: pl.DataFrame) -> dict[str, np.ndarray]:
        """Prediction in ug/m3 (and the quantiles, if any)."""
        x, offset = matrix(test, self.features, self.categories), self._offset(test)
        if self.quantiles:
            # Quantiles survive the monotone exp exactly: no smearing.
            out = {f"q{round(100 * q)}": np.exp(self.boosters[q].predict(x) + offset) for q in self.quantiles}
            out["prediction"] = out[f"q{round(100 * sorted(self.quantiles)[len(self.quantiles) // 2])}"]
            return out
        raw = self.boosters[None].predict(x)
        if self.log_target or self.residual_of:
            return {"prediction": np.exp(raw + offset) * self.smearing}
        return {"prediction": raw}

    def params(self) -> dict:
        """Parameters to log in MLflow."""
        return {
            **{f"lgb_{k}": v for k, v in self.lgb_params.items()},
            "num_boost_round": self.num_boost_round,
            "log_target": self.log_target,
            "residual_of": self.residual_of or "",
            "monotone": ",".join(self.monotone),
            "quantiles": ",".join(str(q) for q in self.quantiles),
            "n_features": len(self.features),
        }


@dataclass
class Recalibrated:
    """A model whose predictions are recalibrated so that their EPA slope is 1.

    A model trained on the squared error shrinks toward the mean: regressed on the
    reference, its predictions have a slope close to R² (< 1). The recalibration
    inverts that regression, ``p' = (p - b) / s`` where ``p = s ref + b`` is fitted on
    **out-of-fold predictions of the training folds** (an inner cross-validation by
    station): the test fold is never used, and the training predictions are honest
    (not in-sample). The price is a wider spread, hence a larger RMSE.
    """

    make_model: object
    name: str = "recalibre"

    def fit(self, train: pl.DataFrame) -> None:
        """Slope and intercept of inner out-of-fold predictions, then the model on all training rows."""
        inner = []
        for fold in train["fold"].unique().sort().to_list():
            model = self.make_model()
            model.fit(train.filter(pl.col("fold") != fold))
            held = train.filter(pl.col("fold") == fold)
            inner.append((held[TARGET].to_numpy(), model.predict(held)["prediction"]))
        ref = np.concatenate([r for r, _ in inner])
        pred = np.concatenate([p for _, p in inner])
        self.slope, self.intercept = np.polyfit(ref, pred, 1)
        self.model = self.make_model()
        self.model.fit(train)
        self.importance = getattr(self.model, "importance", {})

    def predict(self, test: pl.DataFrame) -> dict[str, np.ndarray]:
        """Recalibrated prediction ``(p - b) / s``; the raw one is kept as ``uncalibrated``."""
        out = self.model.predict(test)
        out["uncalibrated"] = out["prediction"]
        out["prediction"] = (out["prediction"] - self.intercept) / self.slope
        return out

    def params(self) -> dict:
        """The wrapped model's parameters and the recalibration slope and intercept."""
        return {
            **self.model.params(),
            "recal_slope": round(float(self.slope), 4),
            "recal_intercept": round(float(self.intercept), 4),
        }
