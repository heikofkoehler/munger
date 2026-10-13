import json

import pytest

from core.snapshots import list_snapshots, load_snapshot, write_snapshot

SUMMARY = {
    "total_value": 1_000_000.0,
    "positions": [
        {"ticker": "VOO", "security_name": "Vanguard S&P 500",
         "value": 600_000.0, "weight_pct": 60.0, "type_display": "ETF", "quantity": 1000.0},
        {"ticker": "GOOG", "security_name": "Alphabet",
         "value": 400_000.0, "weight_pct": 40.0, "type_display": "Stock", "quantity": 2000.0},
    ],
    "allocation": {"ETF": 60.0, "Stock": 40.0},
    "institutions": [{"institution_name": "Schwab", "value": 1_000_000.0, "weight_pct": 100.0}],
}


def test_write_list_load_roundtrip(tmp_path):
    ws = tmp_path / "ws"
    path = write_snapshot(ws, source_label="csv", summary=SUMMARY)
    assert path.suffix == ".json"
    assert path.parent.name == "snapshots"

    entries = list_snapshots(ws)
    assert len(entries) == 1
    entry = entries[0]
    assert entry["name"] == path.name
    assert entry["total_value"] == 1_000_000.0
    assert entry["source"] == "csv"
    assert entry["position_count"] == 2

    loaded = load_snapshot(ws, path.name)
    assert loaded["version"] == 1
    assert loaded["total_value"] == 1_000_000.0
    assert loaded["positions"][0]["ticker"] == "VOO"
    assert loaded["allocation"] == {"ETF": 60.0, "Stock": 40.0}


def test_snapshots_are_immutable_and_unique(tmp_path):
    ws = tmp_path / "ws"
    first = write_snapshot(ws, source_label="csv", summary=SUMMARY)
    before = first.read_text()
    second = write_snapshot(ws, source_label="csv", summary=SUMMARY)
    # names differ even within the same second; first file untouched
    assert first.name != second.name
    assert first.read_text() == before
    assert len(list_snapshots(ws)) == 2


def test_nan_values_become_null(tmp_path):
    ws = tmp_path / "ws"
    summary = dict(SUMMARY, total_value=float("nan"),
                   positions=[dict(SUMMARY["positions"][0], quantity=float("nan"))])
    path = write_snapshot(ws, source_label="csv", summary=summary)
    raw = path.read_text()
    assert "NaN" not in raw  # strict JSON, parseable by any reader
    loaded = load_snapshot(ws, path.name)
    assert loaded["total_value"] is None
    assert loaded["positions"][0]["quantity"] is None


def test_list_newest_first_and_skips_junk(tmp_path):
    from core.workspace import ensure_workspace, snapshots_dir
    ws = ensure_workspace(tmp_path / "ws")
    (snapshots_dir(ws) / "notes.txt").write_text("not a snapshot")
    (snapshots_dir(ws) / "20200101T000000.json").write_text("not json {")
    a = write_snapshot(ws, source_label="a", summary=SUMMARY)
    b = write_snapshot(ws, source_label="b", summary=SUMMARY)
    names = [e["name"] for e in list_snapshots(ws)]
    assert names == sorted([a.name, b.name], reverse=True)


def test_load_rejects_path_traversal():
    with pytest.raises(ValueError, match="Invalid snapshot name"):
        load_snapshot("/tmp", "../evil.json")
    with pytest.raises(ValueError, match="Invalid snapshot name"):
        load_snapshot("/tmp", "/etc/passwd")


def test_load_missing_snapshot_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        load_snapshot(tmp_path / "ws", "20200101T000000.json")
