from datetime import date

import polars as pl

from aircalib.config import get_settings
from aircalib.ingest.discovery import (
    feasibility_summary,
    head_candidates,
    pair_table,
    sample_days,
    sensor_inventory,
)


def test_sample_days_one_per_month_inclusive():
    days = sample_days(date(2024, 1, 1), date(2025, 12, 31), 15)
    assert len(days) == 24
    assert days[0] == date(2024, 1, 15)
    assert days[-1] == date(2025, 12, 15)


def test_sample_days_drops_day_outside_period():
    assert sample_days(date(2024, 1, 20), date(2024, 2, 10), 15) == []


def _listing(rows):
    return pl.DataFrame(
        rows,
        schema={
            "date": pl.Date,
            "sensor_type": pl.String,
            "sensor_id": pl.Int64,
            "indoor": pl.Boolean,
            "filename": pl.String,
        },
        orient="row",
    )


def test_head_candidates_skip_only_sensors_seen_live_abroad():
    listings = _listing(
        [
            (date(2024, 1, 15), "sds011", 1, False, "old"),
            (date(2024, 2, 15), "sds011", 1, False, "new"),  # latest file wins
            (date(2024, 1, 15), "sds011", 2, False, "f2"),  # live in PL → skipped
            (date(2024, 1, 15), "sds011", 3, False, "f3"),  # dead today → kept
            (date(2024, 1, 15), "laerm", 4, False, "f4"),  # noise sensor → not a wanted type
        ]
    )
    live = pl.DataFrame({"sensor_id": [1, 2], "country": ["DE", "PL"]})
    out = head_candidates(listings, live, ["sds011"], ["DE", "FR"])
    assert out["sensor_id"].to_list() == [1, 3]
    assert out.filter(pl.col("sensor_id") == 1)["filename"].item() == "new"


def _fixture():
    """Two PM sensors near station A; one has a humidity probe on the same site."""
    jan, feb, mar = date(2024, 1, 1), date(2024, 2, 1), date(2024, 3, 1)
    listings = _listing(
        [
            (date(2024, 1, 15), "sds011", 10, False, "a"),
            (date(2024, 2, 15), "sds011", 10, False, "b"),
            (date(2024, 1, 15), "bme280", 11, False, "c"),  # same site as 10, January only
            (date(2024, 3, 15), "sds011", 20, False, "d"),
            (date(2024, 3, 15), "sds011", 30, True, "e"),  # indoor → excluded
            (date(2024, 3, 15), "dht22", 21, False, "f"),  # same site as 20 but humidity active in March
        ]
    )
    positions = pl.DataFrame(
        {
            "sensor_id": [10, 11, 20, 30, 21],
            "location_id": [100, 100, 200, 300, 200],
            "lat": [50.0, 50.0, 50.005, 50.0, 50.005],
            "lon": [8.0, 8.0, 8.0, 8.0, 8.0],
        }
    )
    stations = pl.DataFrame(
        {
            "sampling_point": ["A1", "A2"],
            "station_code": ["DEXX001", "DEXX001"],  # two instruments at one station
            "country": ["DE", "DE"],
            "station_type": ["background", "background"],
            "station_area": ["urban", "urban"],
            "lat": [50.0, 50.0],
            "lon": [8.0, 8.0],
        }
    )
    availability = pl.DataFrame(
        {
            "sampling_point": ["A1", "A1", "A1", "A2"],
            "month": [jan, feb, mar, jan],
            "valid_hours": [700, 600, 500, 10],
        },
        schema_overrides={"valid_hours": pl.UInt32},
    )
    return listings, positions, stations, availability


def test_pair_table_overlap_humidity_and_station_dedup():
    listings, positions, stations, availability = _fixture()
    inventory = sensor_inventory(listings, positions, get_settings())
    pairs = pair_table(inventory, stations, availability, max_km=3.0).sort("sensor_id")
    # Indoor sensor 30 is gone, the weaker sampling point A2 is dropped.
    assert pairs["sensor_id"].to_list() == [10, 20]
    assert set(pairs["sampling_point"]) == {"A1"}
    # Sensor 10 active Jan+Feb → 700 + 600 h; sensor 20 active March → 500 h.
    assert pairs["overlap_hours"].to_list() == [1300, 500]
    assert pairs["has_humidity"].to_list() == [True, True]


def test_feasibility_summary_counts_by_radius():
    listings, positions, stations, availability = _fixture()
    pairs = pair_table(sensor_inventory(listings, positions, get_settings()), stations, availability, 3.0)
    summary = feasibility_summary(pairs, [0.1, 1.0])
    small = summary.filter(pl.col("radius_km") == 0.1).row(0, named=True)
    large = summary.filter(pl.col("radius_km") == 1.0).row(0, named=True)
    assert (small["n_pairs"], small["n_stations"]) == (1, 1)  # sensor 20 is ~556 m away
    assert (large["n_pairs"], large["n_sensors"]) == (2, 2)
    assert large["total_overlap_h"] == 1800
