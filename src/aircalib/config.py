"""Typed project configuration, loaded from ``configs/default.yaml``.

Values can be overridden by environment variables prefixed with ``AIRCALIB_``
(nested keys use ``__``), e.g. ``AIRCALIB_PERIOD__START=2025-01-01``.
"""

from __future__ import annotations

import os
from datetime import date, timedelta
from functools import lru_cache
from pathlib import Path

from pydantic import BaseModel, model_validator
from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
    YamlConfigSettingsSource,
)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG = PROJECT_ROOT / "configs" / "default.yaml"


class Paths(BaseModel):
    data_dir: Path
    reports_dir: Path
    sample_subdir: str
    sample: bool = False

    def resolve(self, root: Path) -> Paths:
        """Return a copy where relative paths are anchored at ``root``."""
        return self.model_copy(
            update={
                "data_dir": self.data_dir if self.data_dir.is_absolute() else root / self.data_dir,
                "reports_dir": self.reports_dir
                if self.reports_dir.is_absolute()
                else root / self.reports_dir,
            }
        )

    @property
    def layers(self) -> Path:
        """Root of the medallion layers (``data/sample`` for ``--sample`` runs)."""
        return self.data_dir / self.sample_subdir if self.sample else self.data_dir

    @property
    def raw(self) -> Path:
        """Downloaded files, as received; deleted after conversion unless ``keep_raw``."""
        return self.layers / "raw"

    @property
    def bronze(self) -> Path:
        return self.layers / "bronze"

    @property
    def silver(self) -> Path:
        return self.layers / "silver"

    @property
    def gold(self) -> Path:
        return self.layers / "gold"

    @property
    def meta(self) -> Path:
        """Small reference tables (inventories, station metadata, candidate pairs)."""
        return self.data_dir / "meta"


class Period(BaseModel):
    start: date
    end: date

    @model_validator(mode="after")
    def _ordered(self) -> Period:
        if self.end < self.start:
            raise ValueError(f"period.end ({self.end}) is before period.start ({self.start})")
        return self


class Study(BaseModel):
    main_country: str
    transfer_country: str


class Http(BaseModel):
    user_agent: str
    max_concurrency: int
    max_retries: int
    backoff_base_s: float
    backoff_max_s: float
    timeout_s: float
    progress_log_s: float


class SensorCommunity(BaseModel):
    archive_url: str
    live_snapshot_url: str
    pm_types: list[str]
    humidity_types: list[str]
    head_bytes: int
    pm_columns: list[str]
    humidity_columns: list[str]
    timestamp_format: str
    keep_raw: bool
    unit_workers: int


class Discovery(BaseModel):
    sample_day_of_month: int
    listing_concurrency: int
    countries: list[str]
    radii_km: list[float]
    lag_check_pairs_per_country: int
    lag_check_max_distance_km: float
    lag_check_start: date
    lag_check_days: int
    lag_check_max_lag_h: int
    history_start_year: int
    history_months: list[int]


class Eea(BaseModel):
    api_url: str
    metadata_url: str
    pollutant_vocabulary: str
    pollutants: dict[str, int]
    dataset: int
    aggregation: str
    measurement_types: list[str]
    timezones: list[str]
    valid_codes: list[int]
    verified_codes: list[int]
    missing_sentinel: float
    keep_raw: bool
    bronze_margin_days: int
    clock_offset_h: int
    negative_floor_ugm3: float
    method_classes: dict[str, str]
    method_unknown: str


class Pairing(BaseModel):
    radius_download_km: float
    radius_train_km: float
    sensor_types: list[str]

    @model_validator(mode="after")
    def _nested(self) -> Pairing:
        if self.radius_train_km > self.radius_download_km:
            raise ValueError("pairing.radius_train_km must not exceed pairing.radius_download_km")
        return self


class Particles(BaseModel):
    max_ugm3: float
    pm25_saturation_ugm3: float
    pm10_saturation_ugm3: float
    stuck_min_hours: float
    stuck_codes: list[tuple[float, float]]
    stuck_reference_range_ugm3: float
    cadence_max_gap_s: int
    min_completeness: float


class Humidity(BaseModel):
    saturation_pct: float
    max_pct: float
    floor_pct: float
    age_breaks_years: list[float]
    faulty_hour_share: float
    dead_month_share: float
    stuck_min_hours: float
    probe_preference: list[str]


class Weather(BaseModel):
    n_nearest: int
    max_distance_km: float
    clock_check_step_min: int
    clock_check_max_offset_min: int
    tie_km: float


class Clock(BaseModel):
    max_lag_h: int
    min_hours: int
    min_r: float
    min_z: float
    instrument_lag_h: dict[str, int]


class Dwd(BaseModel):
    base_url: str
    station_list: str
    station_list_encoding: str
    missing_sentinel: float
    keep_raw: bool


class MeteoFrance(BaseModel):
    dataset_api_url: str
    resource_title_regex: str
    communes_url: str
    department_aliases: dict[str, str]
    columns: list[str]
    keep_raw: bool


class Evaluation(BaseModel):
    epa_r2_min: float
    epa_slope_range: tuple[float, float]
    epa_intercept_range: tuple[float, float]
    epa_rmse_max: float
    min_hours: int
    min_days: int
    daily_min_hours: int
    humidity_bins: list[float]
    level_bins: list[float]


class Physics(BaseModel):
    kappa: float
    density_ratio: float
    rh_max_pct: float


class Sample(BaseModel):
    n_sensors_per_country: int
    month: date


class Settings(BaseSettings):
    """Root settings object. Build it with :func:`get_settings`."""

    model_config = SettingsConfigDict(
        env_prefix="AIRCALIB_",
        env_nested_delimiter="__",
        yaml_file=DEFAULT_CONFIG,
        extra="forbid",
    )

    paths: Paths
    period: Period
    study: Study
    http: Http
    sensor_community: SensorCommunity
    discovery: Discovery
    eea: Eea
    pairing: Pairing
    particles: Particles
    humidity: Humidity
    weather: Weather
    clock: Clock
    dwd: Dwd
    meteo_france: MeteoFrance
    evaluation: Evaluation
    physics: Physics
    sample: Sample

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,  # noqa: ARG003 - signature imposed by pydantic
        file_secret_settings: PydanticBaseSettingsSource,  # noqa: ARG003
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        yaml_file = Path(os.environ.get("AIRCALIB_CONFIG", DEFAULT_CONFIG))
        return (
            init_settings,
            env_settings,
            YamlConfigSettingsSource(settings_cls, yaml_file=yaml_file),
        )

    @model_validator(mode="after")
    def _anchor_paths(self) -> Settings:
        self.paths = self.paths.resolve(PROJECT_ROOT)
        return self

    def for_sample(self) -> Settings:
        """Settings of a ``--sample`` run: layers under ``data/<sample_subdir>``, one month."""
        first = self.sample.month.replace(day=1)
        following = date(first.year + first.month // 12, first.month % 12 + 1, 1)
        return self.model_copy(
            update={
                "paths": self.paths.model_copy(update={"sample": True}),
                "period": Period(start=first, end=following - timedelta(days=1)),
            }
        )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Load (once) and return the project settings."""
    return Settings()  # type: ignore[call-arg]
