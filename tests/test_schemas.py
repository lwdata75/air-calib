from datetime import datetime

import pandera.errors
import polars as pl
import pytest

from aircalib.config import get_settings
from aircalib.schemas import bronze as contracts


def _pm_row(**overrides):
    row = {
        "sensor_id": [1],
        "sensor_type": ["SDS011"],
        "location": [10],
        "lat": [50.0],
        "lon": [8.0],
        "timestamp": [datetime(2024, 1, 1)],
        "P1": [10.0],
        "P2": [5.0],
    }
    row.update(overrides)
    return pl.DataFrame(row).with_columns(pl.col("timestamp").cast(pl.Datetime("us")))


def test_bronze_contracts_are_strict():
    for model in (
        contracts.SensorCommunityPm,
        contracts.SensorCommunityHumidity,
        contracts.Eea,
        contracts.Dwd,
        contracts.MeteoFrance,
    ):
        schema = model.to_schema()
        assert schema.strict is True, model.__name__
        assert schema.coerce is False, model.__name__


def test_pm_contract_accepts_a_good_frame_and_rejects_drift():
    contracts.SensorCommunityPm.validate(_pm_row())
    with pytest.raises(pandera.errors.SchemaError):
        contracts.SensorCommunityPm.validate(_pm_row().with_columns(durP1=pl.lit("")))  # unexpected column
    with pytest.raises(pandera.errors.SchemaError):
        contracts.SensorCommunityPm.validate(_pm_row(P2=["5.0"]))  # text instead of float
    with pytest.raises(pandera.errors.SchemaError):
        contracts.SensorCommunityPm.validate(_pm_row(sensor_id=[None]))  # identifier missing


def test_config_value_columns_match_the_contracts():
    cfg = get_settings().sensor_community
    id_and_time = {"sensor_id", "sensor_type", "location", "lat", "lon", "timestamp"}
    assert set(contracts.SensorCommunityPm.to_schema().columns) - id_and_time == set(cfg.pm_columns)
    assert set(contracts.SensorCommunityHumidity.to_schema().columns) - id_and_time == set(
        cfg.humidity_columns
    )
