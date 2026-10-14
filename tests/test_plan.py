from datetime import date

import polars as pl

from aircalib.config import get_settings
from aircalib.ingest.plan import activity_window, humidity_companions, month_units, sample_pairs, select_pairs


def test_activity_window_extends_to_the_neighbouring_sampled_days():
    # Seen on 15 March and 15 May: may have started on 16 Feb, may run until 14 June.
    lo, hi = activity_window([date(2024, 3, 1), date(2024, 5, 1)], date(2024, 1, 1), date(2025, 12, 31), 15)
    assert (lo, hi) == (date(2024, 2, 16), date(2024, 6, 14))


def test_activity_window_is_clipped_to_the_period_and_crosses_years():
    lo, hi = activity_window([date(2024, 1, 1)], date(2024, 1, 1), date(2025, 12, 31), 15)
    assert lo == date(2024, 1, 1)  # 16 Dec 2023 is before the study
    lo, hi = activity_window([date(2024, 12, 1)], date(2024, 1, 1), date(2025, 12, 31), 15)
    assert (lo, hi) == (date(2024, 11, 16), date(2025, 1, 14))


def test_month_units_split_the_window_by_calendar_month():
    sensors = pl.DataFrame(
        {
            "sensor_id": [1],
            "sensor_type": ["sds011"],
            "kind": ["pm"],
            "indoor": [False],
            "months": [[date(2024, 3, 1)]],
        }
    )
    units = month_units(sensors, date(2024, 1, 1), date(2025, 12, 31), 15)
    assert units["month"].to_list() == [date(2024, 2, 1), date(2024, 3, 1), date(2024, 4, 1)]
    assert units["first_day"].to_list() == [date(2024, 2, 16), date(2024, 3, 1), date(2024, 4, 1)]
    assert units["last_day"].to_list() == [
        date(2024, 2, 29),
        date(2024, 3, 31),
        date(2024, 4, 14),
    ]  # leap year
    assert units["n_days"].to_list() == [14, 31, 14]


def _pairs():
    return pl.DataFrame(
        {
            "sensor_id": [1, 2, 3, 4, 5],
            "sensor_type": ["sds011", "sds011", "sds011", "pms7003", "sds011"],
            "location_id": [10, 20, 30, 40, 50],
            "country": ["DE", "DE", "DE", "FR", "FR"],
            "distance_km": [0.2, 1.5, 2.9, 0.1, 3.2],
            "months": [[date(2025, 1, 1)]] * 5,
        }
    )


def test_select_pairs_keeps_sds011_up_to_download_radius_and_tags_bands():
    out = select_pairs(_pairs(), get_settings())
    assert out["sensor_id"].to_list() == [1, 2, 3]  # PMS excluded, 3.2 km beyond the radius
    assert out["distance_band"].to_list() == ["near", "near", "far"]  # 1.5 km itself is "near"


def test_sample_pairs_spreads_over_the_distance_range_per_country():
    pairs = select_pairs(_pairs(), get_settings())
    out = sample_pairs(pairs, n_per_country=2, month=date(2025, 1, 1))
    assert out["sensor_id"].to_list() == [1, 3]  # nearest and farthest
    assert sample_pairs(pairs, 2, date(2024, 6, 1)).is_empty()  # nobody active that month


def test_humidity_companions_share_the_site_and_are_outdoor():
    inventory = pl.DataFrame(
        {
            "sensor_id": [11, 12, 13, 14],
            "kind": ["humidity", "humidity", "humidity", "pm"],
            "indoor": [False, True, False, False],
            "location_id": [10, 10, 99, 10],
        }
    )
    out = humidity_companions(inventory, _pairs())
    assert out["sensor_id"].to_list() == [11]
