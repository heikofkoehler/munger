"""
tests/test_api.py — HTTP-level tests for the FastAPI backend.

Importing main builds the data cache at import time, so this module points
MUNGER_WORKSPACE at a temp dir and scrubs data-source env vars *before*
importing main. With no data source configured the import-time build fails
gracefully (summary=None, no network); each test then rebuilds the cache from
the small demo_portfolio.csv.
"""
import os
import tempfile

# --- hermetic setup: must run before `import main` ---------------------------
_WS = tempfile.mkdtemp(prefix="munger-test-ws-")
os.environ["MUNGER_WORKSPACE"] = _WS
for _var in ("MONARCH_JSON_PATH", "CSV_PATH", "SHEET_ID"):
    os.environ.pop(_var, None)

from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

import main

REPO = Path(__file__).resolve().parent.parent
DEMO_CSV = REPO / "demo_portfolio.csv"

NO_FUND_DETAILS = {"expense_ratio": None, "holdings": []}


@pytest.fixture(autouse=True)
def _no_network_fund_details():
    # demo_portfolio.csv holds ETFs/mutual funds, whose risk/efficiency math
    # looks up fund details over the network — mock it so tests stay offline.
    # (metrics.risk has its own imported name; main.get_fund_details is
    # patched separately in the ticker test.)
    with patch("metrics.risk.get_fund_details", return_value=dict(NO_FUND_DETAILS)):
        yield


def _rebuild_demo_cache():
    main._build_cache(source_path=str(DEMO_CSV))


@pytest.fixture()
def client():
    _rebuild_demo_cache()
    # raise_server_exceptions=False so the global exception handler's
    # 500 responses are observable instead of re-raised.
    return TestClient(main.app, raise_server_exceptions=False)


def test_import_time_cache_build_is_offline_safe():
    # With no data source configured, the import-time _build_cache() must
    # fail gracefully (no network, no exception escaping the import).
    assert main._cache.get("summary") is None or isinstance(
        main._cache.get("summary"), dict
    )


def test_root_serves_dashboard(client):
    r = client.get("/")
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]
    assert "<title>Munger" in r.text


def test_summary_shape(client):
    r = client.get("/api/summary")
    assert r.status_code == 200
    body = r.json()
    for key in ("total_value", "positions", "allocation", "institutions", "risk"):
        assert key in body, f"missing key: {key}"
    assert body["total_value"] > 0
    assert len(body["positions"]) > 0
    tickers = {p["ticker"] for p in body["positions"]}
    assert {"AAPL", "VOO", "GOOG"} <= tickers


def test_summary_500_when_no_data_source(client):
    main._build_cache(source_path="/nonexistent/holdings.csv")
    r = client.get("/api/summary")
    assert r.status_code == 500
    assert "Cache not initialized" in r.json()["message"]


@pytest.mark.parametrize(
    "path,expected_key",
    [
        ("/api/risk", "true_exposure"),
        ("/api/tax", "total_value"),
        ("/api/efficiency", "total_annual_cost"),
    ],
)
def test_metric_endpoints(client, path, expected_key):
    r = client.get(path)
    assert r.status_code == 200
    body = r.json()
    assert isinstance(body, dict)
    assert expected_key in body


def test_snapshots_list_and_detail_roundtrip(client):
    r = client.get("/api/snapshots")
    assert r.status_code == 200
    snaps = r.json()["snapshots"]
    assert len(snaps) >= 1  # written at cache-build time
    assert snaps[0]["total_value"] > 0

    r2 = client.get(f"/api/snapshots/{snaps[0]['name']}")
    assert r2.status_code == 200
    assert r2.json()["version"] == 1


def test_snapshot_detail_404s(client):
    # well-formed name, no such file
    assert client.get("/api/snapshots/20200101T000000.json").status_code == 404
    # bad name pattern
    assert client.get("/api/snapshots/evil.json").status_code == 404
    # path traversal must not escape the snapshots dir
    assert client.get("/api/snapshots/..%2F..%2Fetc%2Fpasswd").status_code == 404


def test_ticker_detail(client):
    with (
        patch("main.enrich_with_market_data",
              side_effect=lambda ps: [{**p, "price": 1.0} for p in ps]),
        patch("main.get_fund_details", return_value={"holdings": []}),
    ):
        r = client.get("/api/ticker/AAPL")
    assert r.status_code == 200
    body = r.json()
    assert body["ticker"] == "AAPL"
    assert body["totals"]["value"] == 22500.0
    assert body["holdings"][0]["account_name"] == "Mock Taxable Brokerage"
    assert body["market"]["price"] == 1.0


def test_portfolios_list(client):
    r = client.get("/api/portfolios")
    assert r.status_code == 200
    body = r.json()
    assert body["portfolios"][0]["name"] == "Default (Env)"
    assert "active" in body


def test_switch_portfolio_bad_path_degrades_gracefully(client):
    r = client.get("/api/switch-portfolio", params={"path": "/nonexistent/holdings.csv"})
    assert r.status_code == 200
    assert r.json() == {}


def test_switch_portfolio_to_demo_csv(client):
    r = client.get("/api/switch-portfolio", params={"path": str(DEMO_CSV)})
    assert r.status_code == 200
    assert r.json()["total_value"] > 0


def test_global_exception_handler_returns_500_json(client):
    main._cache.clear()  # /api/risk then KeyErrors on df_clean
    r = client.get("/api/risk")
    assert r.status_code == 500
    assert r.json()["message"] == "Internal Server Error"
