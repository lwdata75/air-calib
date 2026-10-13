"""Typed project configuration, loaded from ``configs/default.yaml``.

Values can be overridden by environment variables prefixed with ``AIRCALIB_``
(nested keys use ``__``), e.g. ``AIRCALIB_PERIOD__START=2025-01-01``.
"""

from __future__ import annotations

import os
from datetime import date
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

    def resolve(self, root: Path) -> Paths:
        """Return a copy where relative paths are anchored at ``root``."""
        return Paths(
            data_dir=self.data_dir if self.data_dir.is_absolute() else root / self.data_dir,
            reports_dir=self.reports_dir if self.reports_dir.is_absolute() else root / self.reports_dir,
        )

    @property
    def raw(self) -> Path:
        return self.data_dir / "raw"

    @property
    def bronze(self) -> Path:
        return self.data_dir / "bronze"

    @property
    def silver(self) -> Path:
        return self.data_dir / "silver"

    @property
    def gold(self) -> Path:
        return self.data_dir / "gold"

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


class SensorCommunity(BaseModel):
    archive_url: str
    live_snapshot_url: str
    pm_types: list[str]
    humidity_types: list[str]
    head_bytes: int


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


class Sample(BaseModel):
    n_sensors: int
    months: int


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


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Load (once) and return the project settings."""
    return Settings()  # type: ignore[call-arg]
