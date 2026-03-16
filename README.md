# Munger

A local-first, high-security portfolio analysis dashboard. Reads holdings from Monarch Money (recommended), Google Sheets, or a local CSV, deduplicates positions across accounts, and serves a web UI with deep architectural analysis views.

## Features

- **Monarch Money integration** — fetches live portfolio via GraphQL API, stores full JSON response locally
- **Google Sheets integration** — OAuth2 Authorization Code Flow, no service accounts
- **CSV fallback** — drop in a local file for dev/offline use
- **Position deduplication** — merges the same security held across multiple accounts by `security_id`
- **Asset class normalization** — maps cash and fixed income tickers to canonical types
- **Concentration risk flags** — any position exceeding a configurable threshold is flagged automatically
- **Market data enrichment** — dividend yield/rate, EPS, P/E, sector, market cap via yfinance (ticker symbols only leave the machine)
- **Tax bucket classification** — accounts classified as Taxable / Tax-Deferred / Tax-Exempt by name pattern
- **Intrinsic Valuation** — automatically calculates 2-stage FCF DCF values and Margin of Safety for individual stocks
- **Risk & Efficiency** — unpacks ETFs for "True Exposure" mapping and projects wealth gaps based on expense ratios
- **Local-first** — all financial data stays on your machine after fetch

## Dashboard

Run the FastAPI backend and open `http://localhost:8000`:

```bash
uvicorn main:app --reload
```

### Portfolio tab
Net worth hero, allocation bar chart (color-coded by asset class), concentration risk flags, institutions breakdown, full positions table.

### Risk tab
Look-through analysis of ETFs/Mutual Funds mapping out "True Exposure" (Direct + Indirect) to specific companies.

### Efficiency tab
Calculates Weighted Expense Ratio, identifies "Red" tier high-fee funds, and projects 10/20/30 year wealth-gaps against benchmark index fees.

### Valuation tab
Buffett-style Intrinsic Valuation table calculating Free Cash Flow, Debt-to-Equity, ROE, WACC, and Margin of Safety. It even automatically aggregates Intrinsic Value look-through metrics for ETFs.

### Dividends tab
Projected annual income hero, split by tax bucket. Per-bucket tables show Ticker · Name · Value · Type · Annual $/Share · Yield · Projected Income. Sortable columns.

### Earnings tab
Weighted-average trailing P/E hero, split by tax bucket. Per-bucket tables show Ticker · Name · Value · Type · Sector · Trailing EPS · Trailing P/E · Forward P/E · Market Cap. Sortable columns.

### Accounts tab
Three-bucket hero (Taxable / Tax-Deferred / Tax-Exempt) with dollar values and portfolio weights, followed by per-bucket account cards with progress bars. Click any account name to see a full holdings detail page (Ticker · Name · Qty · Price/Share · Value · Cost Basis · Gain/Loss · Type) with a back button to return to the overview.

Ticker symbols link to Yahoo Finance (all tickers except cash placeholders).

## Command Line Interface

You can print a quick summary directly to the terminal without starting the web server:

```bash
python cli.py
```

## Setup

```bash
pip install -r requirements.txt
cp .env.example .env
# edit .env with your data source
```

### Monarch Money (recommended)

Grab your token from the Monarch UI (DevTools → Network → any request → `Authorization: Token <value>`), then fetch fresh data:

```bash
python monarch.py --token YOUR_TOKEN
# saves monarch_response.json locally
```

Set `MONARCH_JSON_PATH=monarch_response.json` in `.env` to use it as the data source. Re-run `monarch.py` whenever you want fresh data, then hit **Refresh Data** in the dashboard. If the file is missing, the backend will safely fallback to your spreadsheet or CSV configuration.

### Google Sheets

Download your OAuth client secret from Google Cloud Console and save it as `credentials.json` (or set `GOOGLE_CREDENTIALS_PATH`). A browser window opens on first run to authorize; the token is cached locally in `token.json`.

## Google Sheet Format

The sheet must have these columns:

`account_id`, `account_name`, `account_mask`, `institution_name`, `holding_name`, `ticker`, `type_display`, `quantity`, `value`, `security_id`, `security_name`, `price_updated`

## Configuration

| Variable | Default | Description |
|---|---|---|
| `MONARCH_JSON_PATH` | — | Path to stored Monarch response JSON (highest priority) |
| `MONARCH_TOKEN` | — | Monarch API token (used by `monarch.py` to fetch fresh data) |
| `CSV_PATH` | — | Local CSV path |
| `SHEET_ID` | — | Google Sheet ID (from URL) |
| `GOOGLE_CREDENTIALS_PATH` | `credentials.json` | OAuth client secret file |
| `CONC_THRESHOLD` | `10.0` | Flag any position exceeding this % of portfolio |

## Testing

The codebase is decomposed into independent modules (`core/`, `data/`, `metrics/`) which are tested via `pytest`.

```bash
pip install pytest
pytest tests/
```

## Security

- Secrets in `.env` — never committed
- `.gitignore` enforced at startup (`*.csv`, `*.json`, `*.env`, `*.db`)
- Only ticker symbols leave the machine (yfinance market data fetch)
- No analytics or telemetry
- All logging to stdout only
