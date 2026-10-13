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


def test_extract_chrome_monarch_cookies_mocked(tmp_path, monkeypatch):
    import sqlite3
    import hashlib
    import subprocess
    from unittest.mock import MagicMock
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    from cryptography.hazmat.backends import default_backend
    from core.monarch_auth import extract_chrome_monarch_cookies

    # Mock home path
    fake_home = tmp_path / "home"
    chrome_dir = fake_home / "Library" / "Application Support" / "Google" / "Chrome" / "Default"
    chrome_dir.mkdir(parents=True)
    cookie_db = chrome_dir / "Cookies"

    # Setup encrypted cookies in SQLite
    test_password = "dummy-keychain-pass"
    salt = b"saltysalt"
    iv = b" " * 16
    key = hashlib.pbkdf2_hmac("sha1", test_password.encode("utf-8"), salt, 1003, 16)

    def encrypt_val(val_str: str) -> bytes:
        data = val_str.encode("utf-8")
        pad_len = 16 - (len(data) % 16)
        padded = data + bytes([pad_len] * pad_len)
        cipher = Cipher(algorithms.AES(key), modes.CBC(iv), backend=default_backend())
        enc = cipher.encryptor()
        return b"v10" + enc.update(padded) + enc.finalize()

    conn = sqlite3.connect(cookie_db)
    cursor = conn.cursor()
    cursor.execute("CREATE TABLE cookies (host_key TEXT, name TEXT, value TEXT, encrypted_value BLOB)")
    cursor.execute(
        "INSERT INTO cookies VALUES (?, ?, ?, ?)",
        (".monarchmoney.com", "session_id", "", encrypt_val("sess_xyz123")),
    )
    cursor.execute(
        "INSERT INTO cookies VALUES (?, ?, ?, ?)",
        (".monarchmoney.com", "csrftoken", "", encrypt_val("csrf_abc456")),
    )
    conn.commit()
    conn.close()

    monkeypatch.setattr(Path, "home", lambda: fake_home)
    monkeypatch.setattr("sys.platform", "darwin")

    def fake_subprocess_run(cmd, *args, **kwargs):
        return MagicMock(returncode=0, stdout=test_password, stderr="")

    monkeypatch.setattr(subprocess, "run", fake_subprocess_run)

    cookie_str = extract_chrome_monarch_cookies()
    assert "session_id=sess_xyz123" in cookie_str
    assert "csrftoken=csrf_abc456" in cookie_str

