from datetime import datetime

import polars as pl

from aircalib.config import get_settings
from aircalib.transform.humidity import (
    age_class,
    classify_months,
    hourly_humidity,
    is_saturated,
    monthly_quality,
    probe_status,
    reading_status,
    saturated_vs_official,
    status_shares,
)
from aircalib.transform.positions import position_history

CFG = get_settings().humidity


def test_reading_status_covers_every_failure_seen_in_the_archive():
    values = [None, float("nan"), -6.0, 119.0, 655.35, 0.0, 1.0, 99.9, 100.0, 99.8, 5.0, 55.0]
    frame = pl.DataFrame({"rh": values}, schema={"rh": pl.Float64})
    out = frame.select(s=reading_status(pl.col("rh"), CFG))["s"].to_list()
    assert out == [
        "missing",
        "missing",  # BME280 NaN
        "out_of_range",  # HTU21D formula floor
        "out_of_range",  # HTU21D formula ceiling
        "out_of_range",  # BME280 uint16 overflow
        "stuck_low",
        "stuck_low",  # DHT22 stuck at 1.0 %
        "saturated",
        "saturated",
        "usable",
        "usable",  # the floor itself is usable
        "usable",
    ]


def test_nan_is_not_saturated():
    frame = pl.DataFrame({"rh": [float("nan"), 100.0]})
    assert frame.select(s=is_saturated(pl.col("rh"), 99.9))["s"].to_list() == [False, True]


def test_status_shares_ignore_missing_values():
    frame = pl.DataFrame(
        {"t": ["dht22"] * 5 + ["bme280"] * 2, "humidity": [99.9, 100.0, 55.0, None, float("nan"), 99.8, 1.0]}
    )
    out = status_shares(frame, ["t"], CFG).collect()
    dht = out.filter(pl.col("t") == "dht22").row(0, named=True)
    assert (dht["n"], dht["n_missing"]) == (3, 2)
    assert dht["saturated_share"] == 2 / 3  # 99.9 counts, null and NaN do not
    bme = out.filter(pl.col("t") == "bme280").row(0, named=True)
    assert (bme["saturated_share"], bme["stuck_low_share"]) == (0.0, 0.5)  # 99.8 is not saturated


def test_age_class_buckets():
    frame = pl.DataFrame({"age": [0.2, 1.0, 3.9, 7.0, None]})
    out = frame.select(c=age_class(pl.col("age"), [1, 2, 4]))["c"].to_list()
    assert out == ["<1", "1-2", "2-4", ">=4", None]


def _probe(hour_values):
    """Raw probe readings: {hour: [values]} for sensor 1 on 2024-01-01."""
    rows = [(h, m, v) for h, values in hour_values.items() for m, v in enumerate(values)]
    return pl.DataFrame(
        {
            "sensor_id": [1] * len(rows),
            "sensor_type": ["DHT22"] * len(rows),
            "timestamp": [datetime(2024, 1, 1, h, m) for h, m, _ in rows],
            "humidity": [v for _, _, v in rows],
        },
        schema_overrides={"humidity": pl.Float64},
    )


def test_hourly_humidity_averages_only_usable_readings():
    probe = _probe({5: [99.9, 99.9, 80.0], 6: [70.0, 72.0, None], 7: [1.0, 1.0, 60.0], 8: [119.0, 50.0]})
    hourly = hourly_humidity(probe, CFG).collect().sort("hour")
    assert hourly["humidity"].to_list() == [80.0, 71.0, 60.0, 50.0]
    assert hourly["raw_humidity"].to_list()[0] == (99.9 + 99.9 + 80.0) / 3  # what the probe claimed
    assert hourly["raw_humidity"].to_list()[3] == 50.0  # 119 % is impossible, not even "claimed"
    assert hourly["n"].to_list() == [3, 2, 3, 2]  # the null is not a reading
    assert hourly["faulty"].to_list() == [True, False, True, False]  # 1 of 2 bad is not a majority
    assert hourly["fault"].to_list() == ["saturated", None, "stuck_low", None]


def test_saturated_hours_compared_with_official():
    probe = _probe({5: [99.9, 99.9, 80.0], 6: [70.0], 7: [1.0, 1.0]})
    hourly = hourly_humidity(probe, CFG).collect()
    official = pl.DataFrame(
        {
            "sensor_id": [1, 1, 1],
            "hour": [datetime(2024, 1, 1, h) for h in (5, 6, 7)],
            "official_humidity": [72.0, 75.0, 60.0],
        }
    )
    out = saturated_vs_official(hourly, official, 99.9)
    assert out["hour"].to_list() == [datetime(2024, 1, 1, 5)]  # hour 7 is stuck low, not saturated
    assert out["official_saturated"].to_list() == [False]  # a broken probe, not fog


def test_position_history_detects_a_move():
    frame = pl.DataFrame(
        {
            "sensor_id": [1, 1, 1, 2],
            "location": [10, 10, 11, 20],
            "lat": [50.0, 50.0, 50.2, 48.0],
            "lon": [8.0, 8.0, 8.1, 2.0],
            "timestamp": [datetime(2024, 1, d) for d in (1, 2, 3, 1)],
        }
    )
    out = position_history(frame).collect()
    assert out.group_by("sensor_id").len().sort("sensor_id")["len"].to_list() == [2, 1]


def _hourly(sensor_id, month, faults):
    """Hourly probe rows: one per entry, ``None`` = usable hour, else the dominant fault."""
    return pl.DataFrame(
        {
            "sensor_id": [sensor_id] * len(faults),
            "sensor_type": ["DHT22"] * len(faults),
            "hour": [datetime(2024, month, 1 + i // 24, i % 24) for i in range(len(faults))],
            "faulty": [f is not None for f in faults],
            "fault": faults,
        },
        schema_overrides={"fault": pl.String},
    )


def test_dead_months_lose_all_hours_healthy_months_lose_only_faulty_ones():
    sat, low = "saturated", "stuck_low"
    frames = pl.concat(
        [
            _hourly(1, 1, [sat] * 9 + [None]),  # 90 % faulty → dead month
            _hourly(1, 2, [sat] + [None] * 9),  # 10 % → healthy, 9 hours kept
            _hourly(2, 1, [None] * 10),  # never faulty
            _hourly(3, 1, [low] * 6 + [sat] * 4),  # stuck low or saturated all month
        ]
    )
    monthly = monthly_quality(frames).collect().sort("sensor_id", "month")
    assert monthly["stuck_low_hours"].to_list() == [0, 0, 0, 6]
    assert monthly["saturated_hours"].to_list() == [9, 1, 0, 4]
    classified = classify_months(monthly, 0.5).collect().sort("sensor_id", "month")
    assert classified["status"].to_list() == ["dead", "healthy", "healthy", "dead"]
    assert classified["recoverable_hours"].to_list() == [0, 9, 10, 0]
    status = probe_status(classified).collect().sort("sensor_id")
    assert status["probe_class"].to_list() == ["morte certains mois", "saine", "morte toute la période"]


def test_month_exactly_at_threshold_is_healthy():
    monthly = monthly_quality(_hourly(1, 1, ["saturated", None])).collect()
    assert classify_months(monthly, 0.5).collect()["status"].item() == "healthy"  # 50 % is not "more than"
