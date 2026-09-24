"""
tests/test_ui.py — browser smoke tests for the dashboard.

Boots a real `uvicorn main:app` on a loopback port (workspace in a temp
dir, data from the stocks-only tests/fixtures/ui_holdings.csv so the
import-time cache build needs no network) and drives static/index.html
with Playwright + headless Chromium.

Chromium is resolved from the Playwright browser cache, falling back to
the system binary at /opt/meta-chromium/chrome. If the browser cannot
reach the local test server (e.g. the displayless Linux host, whose
Chromium build blocks loopback HTTP), the tests skip gracefully instead
of failing — run them where Chromium works (Mac, CI).

The native Tauri window cannot be rendered on a displayless host — browser
mode exercises the same main:app + the same static/index.html the WebView
loads.
"""
import os
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
FIXTURE_CSV = REPO / "tests" / "fixtures" / "ui_holdings.csv"
PORT = 8123
BASE = f"http://127.0.0.1:{PORT}"

SYSTEM_CHROME = "/opt/meta-chromium/chrome"


@pytest.fixture(scope="module")
def server():
    ws = tempfile.mkdtemp(prefix="munger-ui-test-")
    env = {**os.environ, "MUNGER_WORKSPACE": ws, "CSV_PATH": str(FIXTURE_CSV)}
    for var in ("MONARCH_JSON_PATH", "SHEET_ID"):
        env.pop(var, None)
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "main:app",
         "--host", "127.0.0.1", "--port", str(PORT), "--log-level", "warning"],
        cwd=REPO,
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        deadline = time.time() + 60
        while time.time() < deadline:
            try:
                with urllib.request.urlopen(f"{BASE}/api/summary", timeout=2) as r:
                    if r.status == 200:
                        break
            except OSError:
                time.sleep(0.2)
        else:
            raise AssertionError("test server never became ready")
        yield BASE
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=10)


def _launch_browser(pw):
    """System Chromium first, Playwright-bundled as fallback."""
    kwargs = {"headless": True, "args": ["--no-sandbox"]}
    if Path(SYSTEM_CHROME).exists():
        return pw.chromium.launch(executable_path=SYSTEM_CHROME, **kwargs)
    return pw.chromium.launch(**kwargs)


@pytest.fixture()
def page(server):
    pw = pytest.importorskip("playwright.sync_api")
    with pw.sync_playwright() as p:
        try:
            browser = _launch_browser(p)
        except Exception as e:
            pytest.skip(f"Chromium unavailable: {e}")
        pg = browser.new_page()
        try:
            # Probe: some hosts (like this displayless Linux box) run a
            # Chromium build that cannot reach local HTTP servers at all.
            pg.goto(server, timeout=8000)
        except Exception:
            browser.close()
            pytest.skip("Chromium cannot reach the local test server in this environment")
        # /api/market and /api/valuation need the network (yfinance);
        # stub them so tab-switching tests stay hermetic.
        pg.route("**/api/market",
                 lambda route: route.fulfill(json={"positions": [], "sectors": []}))
        pg.route("**/api/valuation",
                 lambda route: route.fulfill(json=[]))
        yield pg
        browser.close()


def test_dashboard_loads_summary(page, server):
    page.goto(server)
    page.wait_for_selector("#loading.hidden", timeout=15000)
    total = page.locator("#total-value").inner_text()
    assert total.strip(), "total value did not render"
    assert "$" in total


def test_tab_switching(page, server):
    page.goto(server)
    page.wait_for_selector("#loading.hidden", timeout=15000)
    for tab in ("risk", "efficiency", "valuation", "dividends", "earnings", "accounts"):
        page.click(f"button[onclick=\"switchTab('{tab}')\"]")
        cls = page.locator(f"#panel-{tab}").get_attribute("class")
        assert "active" in (cls or ""), f"panel-{tab} did not activate"


def test_api_failure_shows_error(page, server):
    page.route("**/api/summary", lambda route: route.abort())
    page.goto(server)
    page.wait_for_function(
        "document.getElementById('loading').textContent.includes('Failed to load')",
        timeout=15000,
    )
