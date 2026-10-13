from datetime import date, datetime
from decimal import Decimal

import polars as pl

from aircalib.config import get_settings
from aircalib.ingest import eea


def _meta_row(spo, year, *, mtype="automatic", pollutant="PM2.5", lat="50.1", tz="UTC+01"):
    row = dict.fromkeys(eea.METADATA_COLUMNS, "")
    row.update(
        {
            "Sampling Point Id": spo,
            "Air Quality Station EoI Code": "DEXX001",
            "Air Pollutant": pollutant,
            "Latitude": lat,
            "Longitude": "8.2",
            "Measurement Type": mtype,
            "Timezone": tz,
            "Year": year,
            "Operational Activity Begin": "01/01/2005 00:00:00",
        }
    )
    return row


def test_sampling_points_keeps_latest_declaration_and_automatic_only():
    meta = pl.DataFrame(
        [
            _meta_row("SPO.A", "", lat="49.0"),
            _meta_row("SPO.A", "2025", lat="50.1"),
            _meta_row("SPO.B", "2025", mtype="active"),  # gravimetric daily sampler
            _meta_row("SPO.C", "2025", pollutant="PM10"),
            _meta_row("SPO.D", "2025", lat=""),  # real extract: some points have no coordinates
            _meta_row("SPO.E", "2025", tz="UTC-04"),  # overseas station, other clock
        ]
    )
    out = eea.sampling_points(meta, "PM2.5", ["automatic"], ["UTC", "UTC+01"])
    assert out["sampling_point"].to_list() == ["SPO.A"]
    assert out["lat"].item() == 50.1
    assert out["country"].item() == "DE"
    assert out["activity_begin"].item() == datetime(2005, 1, 1)


def test_monthly_valid_hours_drops_invalid_unverified_and_sentinel():
    cfg = get_settings().eea
    raw = pl.DataFrame(
        {
            "Samplingpoint": ["DE/SPO.A"] * 5,
            "Pollutant": [6001] * 5,
            "Start": [datetime(2024, 1, 1, h) for h in range(5)],
            "End": [datetime(2024, 1, 1, h + 1) for h in range(5)],
            "Value": [Decimal("5.5"), Decimal("1.9"), Decimal("-999"), Decimal("7.0"), Decimal("8.0")],
            "Unit": ["ug.m-3"] * 5,
            "Validity": [1, 2, -1, 1, 1],
            "Verification": [1, 1, 1, 3, 1],
        }
    )
    lf = raw.lazy().select(
        sampling_point=pl.col("Samplingpoint").str.split("/").list.last(),
        start=pl.col("Start"),
        value=pl.col("Value").cast(pl.Float64),
        validity=pl.col("Validity"),
        verification=pl.col("Verification"),
    )
    out = eea.monthly_valid_hours(lf, cfg, date(2024, 1, 1), date(2025, 12, 31)).collect()
    # kept: valid (1), below detection limit (2) and the last row; dropped: -999 and unverified.
    assert out.row(0, named=True) == {"sampling_point": "SPO.A", "month": date(2024, 1, 1), "valid_hours": 3}


def test_pollutant_uri():
    cfg = get_settings().eea
    assert eea.pollutant_uri(cfg, 6001) == "http://dd.eionet.europa.eu/vocabulary/aq/pollutant/6001"
