"""Statuses of a pair-hour, in their order of application (= funnel order)."""

from __future__ import annotations

from aircalib.transform import clock
from aircalib.transform import particles as pt
from aircalib.transform import reference as ref

NO_STATION = "no_reference_station"
NOT_DOWNLOADED = "reference_not_downloaded"
REFERENCE_MISSING = "reference_missing"
STATION_MISMATCH = "clock_mismatch_station"
NO_OFFICIAL_HUMIDITY = "no_official_humidity"
HOUR_RULES = [
    pt.NO_VALID_READING,
    pt.INCOMPLETE_HOUR,
    NO_STATION,
    NOT_DOWNLOADED,
    REFERENCE_MISSING,
    *[f"reference_{rule}" for rule in ref.RULES if rule != ref.OUTSIDE_PERIOD],
    STATION_MISMATCH,
    clock.MISMATCH,
    NO_OFFICIAL_HUMIDITY,
]
