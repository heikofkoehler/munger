# Munger

A local-first, high-security portfolio analysis tool. Reads holdings from Google Sheets (or a local CSV), deduplicates positions across accounts, normalizes asset classes, and flags concentration risk.

## Features

- **Google Sheets integration** — OAuth2 Authorization Code Flow, no service accounts
- **CSV fallback** — drop in a local file for dev/offline use
- **Position deduplication** — merges the same security held across multiple accounts
- **Asset class normalization** — maps cash and fixed income tickers to canonical types
- **Concentration risk flags** — configurable per-ticker thresholds
- **Local-first** — all financial data stays on your machine after fetch

## Setup

```bash
pip install -r requirements.txt
cp .env.example .env
# edit .env with your SHEET_ID or CSV_PATH
```

For Google Sheets, download your OAuth client secret from Google Cloud Console and save it as `credentials.json` (or set `GOOGLE_CREDENTIALS_PATH`). A browser window will open on first run to authorize access; the token is cached locally in `token.json`.

## Usage

```bash
# Local CSV (dev)
CSV_PATH=portfolio.csv python loader.py

# Google Sheets (prod)
SHEET_ID=your_sheet_id python loader.py
```

Example output:

```
Total Portfolio Value: $10,300.00

Asset Class    Weight
-------------  --------
ETF            61.16%
Fixed Income   25.24%
Cash           10.19%
Stock           3.40%

Ticker    Name                           Value      Weight    Type
--------  -----------------------------  ---------  --------  ------------
VOO       Vanguard S&P 500 ETF           $6,300.00  61.16%    ETF
VCSH      Vanguard Short-Term Corp Bond  $1,500.00  14.56%    Fixed Income
...

CONCENTRATION RISK FLAGS:
  VOO: 61.16% (threshold: 20.0%)
```

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
- No analytics or telemetry
- All logging to stdout only
