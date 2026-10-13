import json
import os
import tempfile
from pathlib import Path
import pytest
from core.monarch_auth import persist_env_var, get_monarch_status, MonarchBrowserAuth


def test_persist_env_var():
    with tempfile.NamedTemporaryFile(mode="w+", delete=False, suffix=".env") as tf:
        tf.write("# Comment\nFOO=bar\n# MONARCH_COOKIE=placeholder\nOTHER=123\n")
        tf_path = Path(tf.name)

    try:
        # Test replacing commented key
        persist_env_var("MONARCH_COOKIE", "session_id=123; csrftoken=abc", env_path=tf_path)
        content = tf_path.read_text()
        assert 'MONARCH_COOKIE="session_id=123; csrftoken=abc"' in content
        assert "FOO=bar" in content
        assert "OTHER=123" in content
        assert os.environ.get("MONARCH_COOKIE") == "session_id=123; csrftoken=abc"

        # Test updating existing key
        persist_env_var("FOO", "new_val", env_path=tf_path)
        content2 = tf_path.read_text()
        assert 'FOO="new_val"' in content2
        assert "FOO=bar" not in content2

        # Test appending new key
        persist_env_var("NEW_KEY", "created", env_path=tf_path)
        content3 = tf_path.read_text()
        assert 'NEW_KEY="created"' in content3
    finally:
        if tf_path.exists():
            tf_path.unlink()


def test_get_monarch_status(tmp_path):
    # Test when file does not exist
    status = get_monarch_status(str(tmp_path / "nonexistent.json"))
    assert status["file_exists"] is False
    assert status["holdings_count"] == 0

    # Test with sample JSON
    sample = {
        "data": {
            "portfolio": {
                "performance": {
                    "totalValue": 1000000.50,
                    "totalBasis": 800000.0,
                    "totalChangeDollars": 200000.50,
                },
                "aggregateHoldings": {
                    "edges": [
                        {
                            "node": {
                                "security": {"id": "sec-1", "ticker": "VOO"},
                                "holdings": [
                                    {"account": {"id": "acct-1"}},
                                    {"account": {"id": "acct-2"}},
                                ],
                            }
                        }
                    ]
                },
            }
        }
    }
    json_path = tmp_path / "monarch_test.json"
    json_path.write_text(json.dumps(sample))

    status = get_monarch_status(str(json_path))
    assert status["file_exists"] is True
    assert status["total_value"] == 1000000.50
    assert status["holdings_count"] == 2
    assert status["accounts_count"] == 2
    assert status["securities_count"] == 1


def test_monarch_browser_auth_state():
    auth = MonarchBrowserAuth()
    st = auth.get_status()
    assert st["status"] == "idle"

    # Test cancel when idle
    res = auth.cancel()
    assert res["status"] == "idle"
