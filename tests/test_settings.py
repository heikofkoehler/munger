import json

import pytest

from core.settings import DEFAULTS, init_settings, load_settings


def test_load_defaults_when_missing(tmp_path):
    settings = load_settings(tmp_path / "ws")
    assert settings == DEFAULTS
    assert settings["data_source"]["kind"] == "auto"
    assert settings["concentration_threshold"] == 10.0


def test_deep_merge_overrides_nested(tmp_path):
    ws = tmp_path / "ws"
    (ws).mkdir(parents=True)
    (ws / "settings.json").write_text(json.dumps({
        "data_source": {"kind": "csv", "csv_path": "/tmp/holdings.csv"},
        "concentration_threshold": 5.0,
    }))
    settings = load_settings(ws)
    # overridden
    assert settings["data_source"]["kind"] == "csv"
    assert settings["data_source"]["csv_path"] == "/tmp/holdings.csv"
    assert settings["concentration_threshold"] == 5.0
    # untouched defaults survive the merge
    assert settings["data_source"]["monarch_json_path"] is None
    assert settings["snapshots"]["enabled"] is True


def test_init_writes_defaults_without_overwrite(tmp_path):
    ws = tmp_path / "ws"
    path = init_settings(ws)
    assert path.exists()
    assert json.loads(path.read_text())["concentration_threshold"] == 10.0

    # second init must not clobber user edits
    path.write_text(json.dumps({"concentration_threshold": 7.5}))
    init_settings(ws)
    assert json.loads(path.read_text())["concentration_threshold"] == 7.5


def test_invalid_json_raises(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir(parents=True)
    (ws / "settings.json").write_text("{not valid json")
    with pytest.raises(ValueError, match="Invalid JSON"):
        load_settings(ws)


def test_non_object_json_raises(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir(parents=True)
    (ws / "settings.json").write_text("[1, 2, 3]")
    with pytest.raises(ValueError, match="must be an object"):
        load_settings(ws)
