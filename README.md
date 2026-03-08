# Munger

A local-first, high-security portfolio analysis dashboard. Reads holdings from Google Sheets (or a local CSV), deduplicates positions across accounts, and serves a web UI with four analysis views.

## Features

- **Google Sheets integration** — OAuth2 Authorization Code Flow, no service accounts
- **CSV fallback** — drop in a local file for dev/offline use
- **Position deduplication** — merges the same security held across multiple accounts by `security_id`
- **Asset class normalization** — maps cash and fixed income tickers to canonical types
- **Concentration risk flags** — configurable per-ticker thresholds
- **Market data enrichment** — dividend yield/rate, EPS, P/E, sector, market cap via yfinance (ticker symbols only leave the machine)
- **Tax bucket classification** — accounts classified as Taxable / Tax-Deferred / Tax-Exempt by name pattern
- **Local-first** — all financial data stays on your machine after fetch

## Dashboard

Run the FastAPI backend and open `http://localhost:8000`:

```bash
CSV_PATH=portfolio_holdings.csv uvicorn main:app --reload
```

### Portfolio tab
Net worth hero, allocation bar chart (color-coded by asset class), concentration risk flags, institutions breakdown, full positions table.

### Dividends tab
Projected annual income hero, split by tax bucket. Per-bucket tables show Ticker · Name · Value · Type · Annual $/Share · Yield · Projected Income. Sortable columns.

### Earnings tab
Weighted-average trailing P/E hero, split by tax bucket. Per-bucket tables show Ticker · Name · Value · Type · Sector · Trailing EPS · Trailing P/E · Forward P/E · Market Cap. Sortable columns.

### Tax tab
Three-bucket hero (Taxable / Tax-Deferred / Tax-Exempt) with dollar values and portfolio weights, followed by per-bucket account cards with progress bars.

Ticker symbols link to Yahoo Finance (Stock and ETF only).

## Setup

```bash
pip install -r requirements.txt
cp .env.example .env
# edit .env with your SHEET_ID or CSV_PATH
```

For Google Sheets, download your OAuth client secret from Google Cloud Console and save it as `credentials.json` (or set `GOOGLE_CREDENTIALS_PATH`). A browser window opens on first run to authorize; the token is cached locally in `token.json`.

## Google Sheet Format

The sheet must have these columns:

`account_id`, `account_name`, `account_mask`, `institution_name`, `holding_name`, `ticker`, `type_display`, `quantity`, `value`, `security_id`, `security_name`, `price_updated`

## Configuration

| Variable | Default | Description |
|---|---|---|
| `SHEET_ID` | — | Google Sheet ID (from URL) |
| `GOOGLE_CREDENTIALS_PATH` | `credentials.json` | OAuth client secret file |
| `CSV_PATH` | — | Local CSV path (overrides Sheets) |
| `CONC_THRESHOLD_GOOG` | `10.0` | GOOG concentration threshold % |
| `CONC_THRESHOLD_VOO` | `20.0` | VOO concentration threshold % |

## Security

- Secrets in `.env` — never committed
- `.gitignore` enforced at startup (`*.csv`, `*.json`, `*.env`, `*.db`)
- Only ticker symbols leave the machine (yfinance market data fetch)
- No analytics or telemetry
- All logging to stdout only
