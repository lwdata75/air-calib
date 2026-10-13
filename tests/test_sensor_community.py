import gzip
from datetime import date

import polars as pl
import pytest

from aircalib.ingest import sensor_community as sc

LISTING_HTML = """
<tr><td><a href="?C=N;O=D">Name</a></td></tr>
<tr><td><a href="/2024/">Parent Directory</a></td></tr>
<tr><td><a href="2024-03-15_sds011_sensor_7334.csv.gz">x</a></td></tr>
<tr><td><a href="2024-03-15_dht22_sensor_7530_indoor.csv.gz">x</a></td></tr>
<tr><td><a href="2024-03-15_radiation_sbm-20_sensor_49588_indoor.csv.gz">x</a></td></tr>
<tr><td><a href="sensor_count.txt">x</a></td></tr>
"""


def test_parse_listing_keeps_only_sensor_files():
    names = sc.parse_listing(LISTING_HTML)
    assert names == [
        "2024-03-15_sds011_sensor_7334.csv.gz",
        "2024-03-15_dht22_sensor_7530_indoor.csv.gz",
        "2024-03-15_radiation_sbm-20_sensor_49588_indoor.csv.gz",
    ]


def test_listing_frame_parses_type_with_dash_and_indoor_suffix():
    frame = sc.listing_frame(sc.parse_listing(LISTING_HTML))
    rows = {r["sensor_id"]: r for r in frame.iter_rows(named=True)}
    assert rows[7334] == {
        "date": date(2024, 3, 15),
        "sensor_type": "sds011",
        "sensor_id": 7334,
        "indoor": False,
        "filename": "2024-03-15_sds011_sensor_7334.csv.gz",
    }
    assert rows[7530]["indoor"] is True
    assert rows[49588]["sensor_type"] == "radiation_sbm-20"


def test_listing_frame_on_empty_listing_has_schema():
    frame = sc.listing_frame([])
    assert frame.height == 0
    assert frame.schema["sensor_id"] == pl.Int64


@pytest.mark.parametrize(
    ("day", "expected"),
    [
        (date(2024, 3, 15), "https://a.org/2024/2024-03-15/"),
        (date(2026, 10, 12), "https://a.org/2026-10-12/"),
    ],
)
def test_day_url_past_year_is_nested(day, expected):
    assert sc.day_url("https://a.org/", day, today=date(2026, 10, 13)) == expected


CSV = (
    "sensor_id;sensor_type;location;lat;lon;timestamp;P1;durP1;ratioP1;P2;durP2;ratioP2\n"
    "7334;SDS011;3708;42.632;23.378;2024-03-15T00:02:11;12.55;;;7.03;;\n"
    "7334;SDS011;3708;42.632;23.378;2024-03-15T00:04:43;9.95;;;6.50;;\n"
)


def test_head_from_truncated_gzip_prefix():
    rows = "".join(
        f"7334;SDS011;3708;42.632;23.378;2024-03-15T00:{i % 60:02d}:00;{i * 1.37:.2f};;;7;;\n"
        for i in range(300)
    )
    blob = gzip.compress((CSV + rows).encode())
    prefix = blob[:300]  # a Range request returns a cut gzip stream
    assert len(blob) > 3 * len(prefix)
    text = sc.decompress_prefix(prefix).decode()
    head = sc.parse_head(text)
    assert head == {
        "sensor_id": "7334",
        "sensor_type": "SDS011",
        "location": "3708",
        "lat": "42.632",
        "lon": "23.378",
        "timestamp": "2024-03-15T00:02:11",
    }


def test_parse_head_ignores_unterminated_first_row():
    assert sc.parse_head("sensor_id;location;lat;lon\n7334;3708;42.6") is None


def test_parse_head_rejects_ragged_row():
    assert sc.parse_head("a;b;c\n1;2\n") is None


def test_read_day_csv_keeps_raw_text_columns():
    frame = sc.read_day_csv(gzip.compress(CSV.encode()), gzipped=True)
    assert frame.height == 2
    assert all(dtype == pl.String for dtype in frame.schema.values())
    assert frame["durP1"].null_count() == 2  # empty fields become nulls, not ""


def test_read_day_csv_empty_file():
    assert sc.read_day_csv(gzip.compress(b""), gzipped=True).is_empty()
