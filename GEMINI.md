# GEMINI.md

This file provides guidance to Gemini CLI when working with code in this repository.

## Role & Objective
**Role**: Expert Fintech Engineer & Python Architect
**Objective**: Build and maintain **Munger**, a local-first, high-security portfolio analysis tool and dashboard, similar to Monarch Money or Empower.

## Project Overview
Primary data source is **Monarch Money** (GraphQL API via `monarch.py`); fallbacks are Google Sheets and local CSV. Holdings are deduplicated by `security_id` across accounts and served via a FastAPI backend with a vanilla JS frontend.

## Tech Stack
- **Backend**: Python, FastAPI (`main.py`)
- **Data pipeline**: `core/`, `data/`, `metrics/` modules for deduplication, asset class normalization, metrics, concentration risk, tax buckets, efficiency.
- **Monarch integration**: `monarch.py` — fetches live data, stores full JSON response locally.
- **Google Sheets integration**: OAuth2 Authorization Code Flow (`google-auth-oauthlib`), no service accounts.
- **Market data**: `yfinance` — dividend yield/rate, EPS, P/E, sector, market cap (ticker symbols only leave the machine).
- **Frontend**: Vanilla JS + CSS, no framework, served as a single `static/index.html`.

## Data Environment & Architecture

```text
monarch.py      — fetches Monarch GraphQL API, saves monarch_response.json
data/sources.py — load() dispatcher (JSON > CSV > Sheets)
data/normalization.py — deduplicate() by security_id, normalize_asset_class()
metrics/        — logic for risk, efficiency, portfolio, tax, valuation
main.py         — FastAPI app; lazy-cached endpoints:
                    /api/summary  (instant)
                    /api/market   (~16s first call, yfinance)
                    /api/tax      (instant, pandas)
                    /api/efficiency (calculates wealth gap)
                    /api/refresh  (clears derived caches)
static/index.html — multi-panel dashboard
```

**Data source priority**: `MONARCH_JSON_PATH` > `CSV_PATH` > `SHEET_ID`

**Deduplication**: `security_id` is the primary key. Holdings of the same security across multiple accounts are merged; `quantity` and `value` (and `cost_basis` when present) are summed.

**Asset class overrides** (applied after dedup):
- `FCASH`, `CUR:USD`, `SPAXX`, `FDRXX` → `Cash`
- `VCSH`, `VGSH`, `BND`, `AGG`, `VBTIX` → `Fixed Income`
- `VFFSX` → `Mutual Fund`

**Tax bucket classification** (substring match on `account_name`, checked in order):
- `"Roth"` → `Tax-Exempt (Roth)`
- `"IRA"` → `Tax-Deferred`
- `"401"` → `Tax-Deferred`
- _(no match)_ → `Taxable`

**Cost basis**: present only for positions where Monarch has the data. Always check `cost_basis != null` before computing gain/loss — never use `> 0` or `|| 0` as those treat `NaN`/missing as zero.

## Security Requirements
1. **Zero Cloud**: No financial data should ever leave the local machine except for the initial fetch (Google Sheet or Monarch). Only ticker symbols leave the machine (yfinance market data fetch).
2. **Secrets**: All secrets in `.env` — never committed.
3. **Gitignore Enforced**: `.gitignore` enforced at startup: must contain `*.csv`, `*.json`, `*.env`, `*.db`.
4. **No Telemetry**: Explicitly do not include any analytics libraries like Segment, Mixpanel, or Google Analytics.
5. **Logging**: All logging must be stdout to the local console only.
6. **Token Refreshment (Sheets)**: Instead of storing a persistent `service_account.json`, use the Authorization Code Flow. The app will prompt you to log in once via a browser, and then it will store a temporary `token.json` locally.

## UI Requirements
- Clean "Monarch-style" dashboard.
- **Net Worth Hero**: Large display of the sum of the value column.
- **Allocation Chart**: Interactive bar chart showing the breakdown of Stock vs. ETF vs. Cash.
- **Institutions Table**: A summary of total value held per institution.
- **Efficiency Tab**: Dollar-impact analysis showing total annual fee bill and wealth gap projections using precise asset-by-asset compounding against a zero-fee benchmark.

## Development Setup

```bash
pip install -r requirements.txt
cp .env.example .env   # set MONARCH_JSON_PATH, MONARCH_TOKEN, etc.

# Fetch fresh Monarch data
python monarch.py --token YOUR_TOKEN

# Run the dashboard
uvicorn main:app --reload
# open http://localhost:8000

# Run tests
pytest tests/
```

## Commit Messages

Use the **imperative mood**, short subject line (≤72 chars), followed by a blank line and a body that explains *why* (not what — the diff shows what).

**Format:**
```text
<Short imperative summary>

<Why this change was needed. What problem it solves.
Mention any non-obvious decisions or trade-offs.>
```

**Examples of good subjects:**
```text
Add Monarch Money integration as primary data source
Fix inflated gain calculation: exclude null-basis holdings
Rename Tax tab to Accounts; add account detail view with securities list
Fix NaN cost_basis breaking /api/tax JSON serialization
Feat: Redesign wealth gap projection UI and increase granularity to 5 years
```

**Rules:**
- Start with a capital letter, no trailing period.
- Use `Add`, `Fix`, `Update`, `Remove`, `Rename` — not `Added`, `Fixed`, `Adding`.
- If fixing a bug, name the symptom and the root cause (e.g. `Fix X: Y was Z`).
- Keep the body focused — one paragraph is enough for most changes.
