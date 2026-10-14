import polars as pl
import pytest

from aircalib.transform.funnel import funnel


def test_funnel_counts_rows_and_entities_step_by_step():
    frame = pl.DataFrame(
        {
            "sensor": [1, 1, 1, 2, 2, 3],
            "status": ["ok", "a", "b", "a", "a", "b"],
        }
    )
    out = funnel(frame, "status", ["a", "b"], {"capteurs": "sensor"})
    rows = {r["step"]: r for r in out.iter_rows(named=True)}
    assert [r["step"] for r in out.iter_rows(named=True)] == ["start", "a", "b", "usable"]
    assert rows["start"]["remaining"] == 6
    assert (rows["a"]["removed"], rows["a"]["remaining"]) == (3, 3)
    assert (rows["b"]["removed"], rows["b"]["remaining"]) == (2, 1)
    # Sensor 2 loses its last row at rule a, sensor 3 at rule b; sensor 1 survives.
    assert (rows["a"]["capteurs_lost"], rows["b"]["capteurs_lost"]) == (1, 1)
    assert rows["usable"]["capteurs_remaining"] == 1


def test_funnel_with_weights_and_missing_entity():
    frame = pl.DataFrame({"station": ["S", None, "S"], "status": ["ok", "a", "a"], "n": [10, 5, 2]})
    out = funnel(frame, "status", ["a"], {"stations": "station"}, weight="n")
    assert out["remaining"].to_list() == [17, 10, 10]
    assert out["stations_remaining"].to_list() == [1, 1, 1]  # a missing station id is not a station


def test_funnel_rejects_unknown_status():
    frame = pl.DataFrame({"status": ["ok", "mystery"], "id": [1, 2]})
    with pytest.raises(ValueError, match="mystery"):
        funnel(frame, "status", ["a"], {"x": "id"})
