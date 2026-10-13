import polars as pl
import pytest

from aircalib.transform.pairing import haversine_km, pairs_within


def _distance(lat1, lon1, lat2, lon2):
    return pl.select(haversine_km(pl.lit(lat1), pl.lit(lon1), pl.lit(lat2), pl.lit(lon2))).item()


def test_haversine_zero_distance():
    assert _distance(52.5, 13.4, 52.5, 13.4) == pytest.approx(0.0)


def test_haversine_paris_berlin():
    # Notre-Dame de Paris → Brandenburger Tor ≈ 878 km
    assert _distance(48.853, 2.3499, 52.5163, 13.3777) == pytest.approx(878, abs=3)


def test_one_millidegree_latitude_is_about_111_m():
    # Sensor.Community rounds non-exact positions to 3 decimals.
    assert _distance(50.0, 8.0, 50.001, 8.0) == pytest.approx(0.111, abs=0.001)


def test_pairs_within_radius_boundaries():
    sensors = pl.DataFrame({"sensor_id": [1, 2, 3], "lat": [50.0, 50.0089, 50.05], "lon": [8.0, 8.0, 8.0]})
    stations = pl.DataFrame({"sampling_point": ["A"], "lat": [50.0], "lon": [8.0]})
    pairs = pairs_within(sensors, stations, max_km=1.0).collect().sort("sensor_id")
    assert pairs["sensor_id"].to_list() == [1, 2]  # 0 km and 0.99 km in, 5.6 km out
    assert pairs["distance_km"].to_list()[1] == pytest.approx(0.99, abs=0.01)


def test_pairs_within_returns_every_station_in_range():
    sensors = pl.DataFrame({"sensor_id": [1], "lat": [50.0], "lon": [8.0]})
    stations = pl.DataFrame({"sampling_point": ["A", "B"], "lat": [50.001, 49.999], "lon": [8.0, 8.0]})
    assert pairs_within(sensors, stations, max_km=0.5).collect().height == 2


def test_pairs_within_handles_longitude_shrinkage():
    # At 60°N one degree of longitude is ~55.6 km, not 111 km.
    sensors = pl.DataFrame({"sensor_id": [1], "lat": [60.0], "lon": [10.0]})
    stations = pl.DataFrame({"sampling_point": ["A"], "lat": [60.0], "lon": [10.017]})
    pairs = pairs_within(sensors, stations, max_km=1.0).collect()
    assert pairs["distance_km"].item() == pytest.approx(0.945, abs=0.01)
