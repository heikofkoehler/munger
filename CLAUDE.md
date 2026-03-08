# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

**Munger** is a local-first, high-security portfolio analysis dashboard. Primary data source is **Monarch Money** (GraphQL API via `monarch.py`); fallbacks are Google Sheets and local CSV. Holdings are deduplicated by `security_id` across accounts and served via a FastAPI backend with a vanilla JS frontend.

## Tech Stack

- **Backend**: Python, FastAPI (`main.py`)
- **Data pipeline**: `loader.py` — deduplication, asset class normalization, metrics, concentration risk, tax buckets
- **Monarch integration**: `monarch.py` — fetches live data, stores full JSON response locally
- **Market data**: `yfinance` — dividend yield/rate, EPS, P/E, sector, market cap (ticker symbols only leave the machine)
- **Frontend**: Vanilla JS + CSS, no framework, served as a single `static/index.html`

## Architecture

```
monarch.py      — fetches Monarch GraphQL API, saves monarch_response.json
loader.py       — load() dispatcher → deduplicate() → normalize_asset_class()
                  → calculate_metrics(), check_concentration(),
                    calculate_institutions(), calculate_tax_buckets(),
                    enrich_with_market_data()
main.py         — FastAPI app; lazy-cached endpoints:
                    /api/summary  (instant)
                    /api/market   (~16s first call, yfinance)
                    /api/tax      (instant, pandas)
                    /api/refresh  (clears derived caches)
static/index.html — four-panel dashboard (Portfolio, Dividends, Earnings, Accounts)
```

**Data source priority**: `MONARCH_JSON_PATH` > `CSV_PATH` > `SHEET_ID`

**Deduplication**: `security_id` is the primary key. Holdings of the same security across multiple accounts are merged; `quantity` and `value` (and `cost_basis` when present) are summed.

**Asset class overrides** (applied after dedup):
- `FCASH`, `CUR:USD` → `Cash`
- `VCSH`, `VGSH` → `Fixed Income`

**Tax bucket classification** (substring match on `account_name`, checked in order):
- `"Roth"` → `Tax-Exempt (Roth)`
- `"IRA"` → `Tax-Deferred`
- `"401"` → `Tax-Deferred`
- _(no match)_ → `Taxable`

**Cost basis**: present only for positions where Monarch has the data. Always check `cost_basis != null` before computing gain/loss — never use `> 0` or `|| 0` as those treat `NaN`/missing as zero.

## Security Requirements

1. All secrets in `.env` — never committed
2. `.gitignore` enforced at startup: must contain `*.csv`, `*.json`, `*.env`, `*.db`
3. No analytics/telemetry libraries
4. All logging stdout only

## Development Setup

```bash
pip install -r requirements.txt
cp .env.example .env   # set MONARCH_JSON_PATH, MONARCH_TOKEN, etc.

# Fetch fresh Monarch data
python3 monarch.py --token YOUR_TOKEN

# Run the dashboard
uvicorn main:app --reload
# open http://localhost:8000
```

## Commit Messages

Use the **imperative mood**, short subject line (≤72 chars), followed by a blank line and a body that explains *why* (not what — the diff shows what).

**Format:**
```
<Short imperative summary>

<Why this change was needed. What problem it solves.
Mention any non-obvious decisions or trade-offs.>

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>
```

**Examples of good subjects:**
```
Add Monarch Money integration as primary data source
Fix inflated gain calculation: exclude null-basis holdings
Rename Tax tab to Accounts; add account detail view with securities list
Fix NaN cost_basis breaking /api/tax JSON serialization
```

**Rules:**
- Start with a capital letter, no trailing period
- Use `Add`, `Fix`, `Update`, `Remove`, `Rename` — not `Added`, `Fixed`, `Adding`
- If fixing a bug, name the symptom and the root cause (e.g. `Fix X: Y was Z`)
- Keep the body focused — one paragraph is enough for most changes
- Always include the `Co-Authored-By` trailer
