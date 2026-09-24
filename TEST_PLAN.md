# Munger test plan

Scope: the local-first portfolio analytics tool — Python/FastAPI backend
(`main.py`), vanilla-JS dashboard (`static/index.html`), Tauri desktop shell
(`src-tauri/` + frozen `sidecar.py`). Branch: `local-first`.

Run the Python suite from the repo root:

```bash
.venv/bin/python -m pytest tests/ -q
```

Run the Rust checks from `src-tauri/`:

```bash
cargo check && cargo test
```

## 1. Unit tests (existing, `tests/`)

Pure-logic coverage; no network, no server. Each file owns one module:

| File | Covers |
|---|---|
| `test_portfolio.py` | `metrics/portfolio.py` — totals, allocation, institutions |
| `test_risk.py` | `metrics/risk.py` — true exposure, concentration flags |
| `test_efficiency.py` | `metrics/efficiency.py` — fee drag, overlap |
| `test_tax.py` | `metrics/tax.py` — tax buckets |
| `test_valuation.py` | `metrics/valuation.py` — intrinsic value math |
| `test_market_data.py` | `data/market_data.py` — yfinance enrichment (mocked) |
| `test_normalization.py` | `data/normalization.py` — dedupe, asset-class mapping |
| `test_sources.py` | `data/sources.py` — CSV/Monarch/Sheets dispatch |
| `test_snapshots.py` | `core/snapshots.py` — immutable snapshot round-trip |
| `test_settings.py` | `core/settings.py` — defaults, deep merge |
| `test_workspace.py` | `core/workspace.py` — workspace resolution |

## 2. API tests (`tests/test_api.py`)

FastAPI `TestClient` against the real `main:app`. Importing `main` builds the
data cache, so the module points `MUNGER_WORKSPACE` at a temp dir and scrubs
data-source env vars **before** import; each test rebuilds the cache from the
tiny `demo_portfolio.csv` (with `get_fund_details` mocked — the demo file
holds ETFs and the real lookup needs the network).

- `GET /` serves `static/index.html` (200, HTML).
- `GET /api/summary` returns 200 with `total_value`, `positions`,
  `allocation`, `institutions`, `risk`.
- `GET /api/summary` returns 500 `"Cache not initialized"` when the data
  source is missing (cache build failure path).
- `GET /api/risk`, `/api/tax`, `/api/efficiency` return 200 with the shapes
  the dashboard renders.
- `GET /api/snapshots` lists snapshots written at cache-build time.
- `GET /api/snapshots/{name}` round-trips a real snapshot (200);
  unknown-but-wellformed names and path-traversal names (`../…`) return 404.
- `GET /api/ticker/AAPL` returns holdings/totals with yfinance mocked out.
- `GET /api/portfolios` returns the portfolio list.
- `GET /api/switch-portfolio?path=<bad>` degrades to `{}` (summary None).
- Unexpected handler errors surface as 500 `{"message": "Internal Server
  Error"}` via the global exception handler.

Deliberately **not** covered here: `/api/market` and `/api/valuation`
(they call yfinance; covered by `test_market_data.py` with mocks, and by the
manual dashboard check).

## 3. Sidecar protocol test (`tests/test_sidecar.py`)

Regression test for the blank-window bug (fixed in `5248743`): the Tauri shell
opens its window on the announced port immediately, so `MUNGER_PORT` must be
printed only **after** the server accepts connections.

- Launches `python sidecar.py` as a subprocess, reads stdout until the
  `MUNGER_PORT=<port>` line, then asserts `http://127.0.0.1:<port>/`
  returns 200 — proving the port was live at announce time.
- This exercises the real `python sidecar.py` path, not the frozen binary.

## 4. UI tests (`tests/test_ui.py`, Playwright + headless Chromium)

Boots a real `uvicorn main:app` on a loopback port against a stocks-only
fixture CSV (no fund rows, so the import-time cache build needs no network)
and drives the dashboard:

- Dashboard loads: `#loading` gains `hidden`, `#total-value` renders.
- Tab switching: clicking each tab button activates the matching
  `#panel-<name>` (network-backed tabs stubbed at the Playwright layer).
- API failure: aborting `/api/summary` makes `#loading`
  show the `Failed to load: …` message.

The tests skip gracefully on hosts where Chromium cannot reach local
HTTP servers (verified: the displayless Linux host's Chromium 152 build
blocks loopback navigations, so they skip there). Run them where a
working Chromium exists — e.g. on the Mac with
`.venv/bin/python -m pytest tests/test_ui.py -q`.

Not covered on the displayless Linux host: the native Tauri window itself
(no display server). Browser mode exercises the same `main:app` + the same
`static/index.html` the WebView loads.

## 5. Rust (`cargo check`, `cargo test`)

`src-tauri/` currently has zero Rust tests; `cargo check` must stay
warning-free and `cargo test` must pass. The shell is a thin launcher
(spawn sidecar → read `MUNGER_PORT` → open window → kill on close), so the
meaningful coverage lives in `test_sidecar.py` plus the Mac checklist below.

## 6. Mac-only manual checklist (cannot run on this Linux host)

- [ ] Close the Tauri window → Python sidecar process is gone.
- [ ] `npx --yes @tauri-apps/cli@2 build` succeeds.
- [ ] The generated `.app` and `.dmg` both launch and render the dashboard.
- [ ] Dashboard eyeball pass incl. the reported glitches (note whether each
      is visual, data, or interaction).
