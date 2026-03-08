# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

**Munger** is a local-first, high-security portfolio analysis tool (similar to Monarch Money or Empower). Data source is a Google Sheet with columns: `account_id`, `account_name`, `account_mask`, `institution_name`, `holding_name`, `ticker`, `type_display`, `quantity`, `value`, `security_id`, `security_name`, `price_updated`.

## Tech Stack

- **Backend**: Python, FastAPI
- **Auth**: Google OAuth2 via `google-auth-oauthlib` (Authorization Code Flow — browser login, local `token.json`, never a persistent service account)
- **Data**: Google Sheets API; all financial data stays local after initial fetch

## Architecture

```
loader.py          — reads Sheet/CSV, deduplicates by security_id, calculates weights
FastAPI backend    — serves calculation engine results via local API
Dashboard UI       — "Monarch-style": net worth hero, allocation sunburst, institutions table
```

**Deduplication**: `security_id` is the primary key for merging holdings across accounts (e.g., VOO held in multiple accounts → single Position View).

**Asset classes**:
- `FCASH` and `CUR:USD` → unified "Cash"
- `VCSH`, `VGSH` → "Fixed Income" (not "Equities")
- `GOOG` → tracked separately for concentration risk

## Security Requirements

1. All secrets in a local `.env` file — never committed
2. OAuth2 Authorization Code Flow; store temporary `token.json` locally
3. A startup check must verify `.gitignore` contains `*.csv`, `*.json`, `*.env`, `*.db` before the app starts
4. No analytics/telemetry libraries (Segment, Mixpanel, Google Analytics, etc.)
5. All logging is stdout only — no remote log shipping

## Development Setup

```bash
# Install dependencies (once requirements.txt exists)
pip install -r requirements.txt

# Run the FastAPI backend
uvicorn main:app --reload

# Run the loader script
python loader.py
```

## Commands (once established)

- **Lint**: TBD (project uses Python — likely `ruff` or `flake8`)
- **Tests**: TBD (likely `pytest`)
- **Run single test**: `pytest tests/test_<name>.py -v`
