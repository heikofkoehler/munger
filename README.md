# Munger

A local-first, high-security portfolio analysis dashboard. Reads holdings from Google Sheets (or a local CSV), deduplicates positions across accounts, and serves a web UI with four analysis views.

## Features

- **Monarch Money integration** — fetches live portfolio via GraphQL API, stores full JSON response locally
- **Google Sheets integration** — OAuth2 Authorization Code Flow, no service accounts
- **CSV fallback** — drop in a local file for dev/offline use
- **Position deduplication** — merges the same security held across multiple accounts by `security_id`
- **Asset class normalization** — maps cash and fixed income tickers to canonical types
- **Concentration risk flags** — any position exceeding a configurable threshold is flagged automatically
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

### Accounts tab
Three-bucket hero (Taxable / Tax-Deferred / Tax-Exempt) with dollar values and portfolio weights, followed by per-bucket account cards with progress bars. Click any account name to see a full holdings detail page (Ticker · Name · Value · Type) with a back button to return to the overview.

Ticker symbols link to Yahoo Finance (all tickers except cash placeholders).

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

Set `MONARCH_JSON_PATH=monarch_response.json` in `.env` to use it as the data source. Re-run `monarch.py` whenever you want fresh data, then hit **Refresh Data** in the dashboard.

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

## Security

- Secrets in `.env` — never committed
- `.gitignore` enforced at startup (`*.csv`, `*.json`, `*.env`, `*.db`)
- Only ticker symbols leave the machine (yfinance market data fetch)
- No analytics or telemetry
- All logging to stdout only
