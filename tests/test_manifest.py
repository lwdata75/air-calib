import pytest

from aircalib.ingest.manifest import FAILED, OK, Manifest


def test_manifest_resumes_on_latest_status(tmp_path):
    m = Manifest(tmp_path / "m.jsonl", ("sensor_id", "month"))
    assert m.done() == set()
    m.append({"sensor_id": 1, "month": "2024-01-01", "status": FAILED, "error": "timeout"})
    m.append({"sensor_id": 2, "month": "2024-01-01", "status": OK})
    assert m.done() == {("2", "2024-01-01")}
    m.append({"sensor_id": 1, "month": "2024-01-01", "status": OK})  # retried later
    assert m.done() == {("1", "2024-01-01"), ("2", "2024-01-01")}
    assert m.frame().height == 2  # one row per key: the latest


def test_manifest_rejects_records_without_key(tmp_path):
    m = Manifest(tmp_path / "m.jsonl", ("filename",))
    with pytest.raises(KeyError):
        m.append({"status": OK})
