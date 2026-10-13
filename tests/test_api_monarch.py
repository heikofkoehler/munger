import os
import tempfile
from pathlib import Path
from unittest.mock import patch

# --- hermetic setup: must run before `import main` ---------------------------
_WS = tempfile.mkdtemp(prefix="munger-test-ws-")
os.environ["MUNGER_WORKSPACE"] = _WS
for _var in ("MONARCH_JSON_PATH", "CSV_PATH", "SHEET_ID"):
    os.environ.pop(_var, None)

import pytest
from fastapi.testclient import TestClient
from main import app

NO_FUND_DETAILS = {"expense_ratio": None, "holdings": []}


@pytest.fixture(autouse=True)
def _no_network_fund_details():
    with patch("metrics.risk.get_fund_details", return_value=dict(NO_FUND_DETAILS)):
        yield


@pytest.fixture
def client():
    return TestClient(app)


def test_monarch_status_endpoint(client):
    r = client.get("/api/monarch/status")
    assert r.status_code == 200
    data = r.json()
    assert "has_credentials" in data
    assert "file_exists" in data
    assert "login_process" in data
    assert data["login_process"]["status"] in ["idle", "running", "success", "error", "cancelled"]


def test_monarch_login_status_endpoint(client):
    r = client.get("/api/monarch/login-status")
    assert r.status_code == 200
    data = r.json()
    assert "status" in data


def test_monarch_login_cancel_endpoint(client):
    r = client.post("/api/monarch/login-cancel")
    assert r.status_code == 200


def test_monarch_credentials_validation(client):
    # Empty body
    r = client.post("/api/monarch/credentials", json={})
    assert r.status_code == 400
    assert r.json()["success"] is False

    # Invalid credentials
    r = client.post("/api/monarch/credentials", json={"cookie": "invalid_cookie=123"})
    assert r.status_code == 400
    assert r.json()["success"] is False
    assert "Failed to authenticate" in r.json()["error"]


def test_monarch_sync_no_credentials(client):
    with patch.dict(os.environ, {}, clear=True):
        r = client.post("/api/monarch/sync")
        assert r.status_code == 401
        data = r.json()
        assert data["success"] is False
        assert data["needs_login"] is True


def test_monarch_import_chrome_success(client):
    fake_cookie = "session_id=fake123; csrftoken=fake456"
    fake_sync_data = {
        "data": {
            "portfolio": {
                "performance": {"totalValue": 550000.0, "totalChangeDollars": 1200.0}
            }
        }
    }
    with patch("main.extract_chrome_monarch_cookies", return_value=fake_cookie), \
         patch("main._perform_monarch_sync", return_value=fake_sync_data), \
         patch("main.persist_env_var") as mock_persist:
        r = client.post("/api/monarch/import-chrome")
        assert r.status_code == 200
        data = r.json()
        assert data["success"] is True
        assert data["total_value"] == 550000.0
        mock_persist.assert_called_once_with("MONARCH_COOKIE", fake_cookie)


def test_monarch_import_chrome_failure(client):
    with patch("main.extract_chrome_monarch_cookies", side_effect=ValueError("No active cookies")):
        r = client.post("/api/monarch/import-chrome")
        assert r.status_code == 400
        data = r.json()
        assert data["success"] is False
        assert "No active cookies" in data["error"]

