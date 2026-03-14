"""
loader.py — Munger portfolio data loader

Reads holdings from Google Sheets or local CSV, deduplicates by security_id,
normalizes asset classes, calculates portfolio metrics, and flags concentration risk.
"""

import os
import sys

from dotenv import load_dotenv

load_dotenv()


# ---------------------------------------------------------------------------
# 1. Startup .gitignore check
# ---------------------------------------------------------------------------

def check_gitignore():
    """Verify .gitignore exists and contains required security patterns."""
    required = {"*.csv", "*.json", "*.env", "*.db"}
    gitignore_path = os.path.join(os.path.dirname(__file__), ".gitignore")

    if not os.path.exists(gitignore_path):
        raise RuntimeError(".gitignore not found — refusing to start. "
                           "Create .gitignore with: *.csv, *.json, *.env, *.db")

    with open(gitignore_path) as f:
        lines = {line.strip() for line in f if line.strip() and not line.startswith("#")}

    missing = required - lines
    if missing:
        raise RuntimeError(
            f".gitignore is missing required patterns: {sorted(missing)}. "
            "Add them before running."
        )


# ---------------------------------------------------------------------------
# 2. Data loading
# ---------------------------------------------------------------------------

EXPECTED_COLUMNS = [
    "account_id", "account_name", "account_mask", "institution_name",
    "holding_name", "ticker", "type_display", "quantity", "value",
    "security_id", "security_name", "price_updated",
]


def load_from_csv(path: str):
    """Load holdings from a local CSV file."""
    import pandas as pd
    df = pd.read_csv(path)
    return df


def load_from_sheets(sheet_id: str):
    """
    Load holdings from Google Sheets via OAuth2 Authorization Code Flow.

    Credentials JSON path is read from GOOGLE_CREDENTIALS_PATH env var
    (default: credentials.json). Token is stored/refreshed in token.json.
    """
    import pandas as pd
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow
    from google.auth.transport.requests import Request
    from googleapiclient.discovery import build

    SCOPES = ["https://www.googleapis.com/auth/spreadsheets.readonly"]
    creds_path = os.environ.get("GOOGLE_CREDENTIALS_PATH", "credentials.json")
    token_path = "token.json"

    creds = None
    if os.path.exists(token_path):
        creds = Credentials.from_authorized_user_file(token_path, SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            if not os.path.exists(creds_path):
                raise FileNotFoundError(
                    f"OAuth credentials file not found: {creds_path}. "
                    "Download it from Google Cloud Console and set GOOGLE_CREDENTIALS_PATH."
                )
            flow = InstalledAppFlow.from_client_secrets_file(creds_path, SCOPES)
            creds = flow.run_local_server(port=0)
        with open(token_path, "w") as token_file:
            token_file.write(creds.to_json())

    service = build("sheets", "v4", credentials=creds)
    result = (
        service.spreadsheets()
        .values()
        .get(spreadsheetId=sheet_id, range="A1:Z")
        .execute()
    )
    rows = result.get("values", [])
    if not rows:
        raise ValueError("Sheet returned no data.")

    headers = rows[0]
    data = rows[1:]
    df = pd.DataFrame(data, columns=headers)
    return df


def load(sheet_id: str = None, csv_path: str = None, monarch_json: str = None):
    """
    Dispatcher: load from Monarch JSON, CSV, or Google Sheets (checked in that order).
    """
    monarch_json = monarch_json or os.environ.get("MONARCH_JSON_PATH")
    csv_path = csv_path or os.environ.get("CSV_PATH")
    sheet_id = sheet_id or os.environ.get("SHEET_ID")

    if monarch_json:
        print(f"Loading from Monarch JSON: {monarch_json}", flush=True)
        from monarch import load_from_json
        return load_from_json(monarch_json)
    if csv_path:
        print(f"Loading from CSV: {csv_path}", flush=True)
        return load_from_csv(csv_path)
    if sheet_id:
        print(f"Loading from Google Sheets: {sheet_id}", flush=True)
        return load_from_sheets(sheet_id)

    raise ValueError(
        "No data source configured. Set MONARCH_JSON_PATH, CSV_PATH, or SHEET_ID."
    )


# ---------------------------------------------------------------------------
# 3. Normalization & Deduplication
# ---------------------------------------------------------------------------

# Map different stock classes or common aliases to a single "Master Ticker"
# for concentration risk aggregation.
TICKER_ALIASES = {
    "GOOG":  "GOOGL", # Alphabet Inc.
    "BRK-A": "BRK-B", # Berkshire Hathaway
    "BRKA":  "BRK-B",
    "BRKB":  "BRK-B",
}

# Manual overrides for securities with missing or broken ticker symbols
TICKER_OVERRIDES = {
    "UNKNOWN_189993187450742649": "VBTIX", # Vanguard Total Bond Market Index Fund
    "UNKNOWN_189993188208175994": "VFFSX", # Vanguard 500 Index Fund
    "Inst Tot Bd Mkt Ix Tr":      "VBTIX",
    "Instl 500 Index Trust":      "VFFSX",
}


def normalize_ticker(ticker: str, aggregate_classes: bool = False) -> str:
    """
    Normalize ticker symbols to a standard format.
    Converts . to - (e.g., BRK.B -> BRK-B).
    
    If aggregate_classes is True, it will also map different share classes 
    to a single master ticker (e.g., GOOG -> GOOGL).
    """
    if not ticker or not isinstance(ticker, str):
        return ""
    
    # Check manual overrides first
    if ticker in TICKER_OVERRIDES:
        ticker = TICKER_OVERRIDES[ticker]

    t = ticker.strip().upper()
    # Standardize on Yahoo Finance format (hyphen instead of dot/slash)
    t = t.replace(".", "-").replace("/", "-")
    # Remove any extra spaces around the hyphen
    if "-" in t:
        parts = [p.strip() for p in t.split("-")]
        t = "-".join(parts)
    
    # Strip common class suffixes if they don't have a hyphen yet
    # e.g. "BRKB" -> "BRK-B" logic handled above partially, but let's be explicit
    if t == "BRKB": t = "BRK-B"
    if t == "BRKA": t = "BRK-B" if aggregate_classes else "BRK-A"

    if aggregate_classes:
        return TICKER_ALIASES.get(t, t)
    
    return t


def deduplicate(df):
    """
    Deduplicate holdings by ticker (position view).

    Sums quantity and value across accounts. Preserves security_id, security_name,
    type_display from the first occurrence per ticker.
    """
    import pandas as pd

    df = df.copy()
    df["quantity"] = pd.to_numeric(df["quantity"], errors="coerce").fillna(0)
    df["value"] = pd.to_numeric(df["value"], errors="coerce").fillna(0)
    
    # 1. Apply TICKER_OVERRIDES first (using security_id or raw name)
    # This ensures that items with empty tickers are merged correctly.
    def get_initial_ticker(row):
        t = row.get("ticker") or ""
        if not t or t.startswith("UNKNOWN"):
            # Check if security_id or name is in overrides
            sid = str(row.get("security_id", ""))
            name = str(row.get("security_name", ""))
            return TICKER_OVERRIDES.get(sid, TICKER_OVERRIDES.get(name, t))
        return t

    df["ticker"] = df.apply(get_initial_ticker, axis=1)

    # 2. Normalize tickers for consistent display and merging.
    if "ticker" in df.columns:
        df["ticker"] = df["ticker"].apply(lambda t: normalize_ticker(t, aggregate_classes=False))

    # Identify by ticker. If ticker is empty, use security_id as fallback
    df["group_id"] = df["ticker"].where(df["ticker"] != "", df["security_id"])

    # Metadata to preserve from first occurrence
    meta = df.groupby("group_id")[["ticker", "security_id", "security_name", "type_display"]].first()

    # Summed numeric columns
    numeric_cols = ["quantity", "value"]
    if "cost_basis" in df.columns:
        df["cost_basis"] = pd.to_numeric(df["cost_basis"], errors="coerce").fillna(0)
        numeric_cols.append("cost_basis")
    numeric = df.groupby("group_id")[numeric_cols].sum()

    result = meta.join(numeric).reset_index(drop=True)
    return result


# ---------------------------------------------------------------------------
# 4. Asset class normalization
# ---------------------------------------------------------------------------

CASH_TICKERS = {"FCASH", "CUR:USD", "SPAXX", "FDRXX"}
FIXED_INCOME_TICKERS = {"VCSH", "VGSH", "BND", "AGG", "VBTIX"}
MUTUAL_FUND_TICKERS = {"VFFSX"}


def normalize_asset_class(df):
    """Normalize type_display based on ticker overrides."""
    df = df.copy()
    df.loc[df["ticker"].isin(CASH_TICKERS), "type_display"] = "Cash"
    df.loc[df["ticker"].isin(FIXED_INCOME_TICKERS), "type_display"] = "Fixed Income"
    df.loc[df["ticker"].isin(MUTUAL_FUND_TICKERS), "type_display"] = "Mutual Fund"
    return df


# ---------------------------------------------------------------------------
# 5. Metrics calculation
# ---------------------------------------------------------------------------

def calculate_metrics(df) -> dict:
    """
    Calculate portfolio metrics.

    Returns:
        {
            "total_value": float,
            "positions": [{"ticker", "security_name", "value", "weight_pct", "type_display"}, ...],
            "allocation": {"Stock": pct, "ETF": pct, ...},
        }
    """
    total = df["value"].sum()

    positions = []
    for _, row in df.iterrows():
        ticker = normalize_ticker(row["ticker"] or f"UNKNOWN_{row['security_id']}", aggregate_classes=False)
        weight = (row["value"] / total * 100) if total else 0.0
        positions.append({
            "ticker": ticker,
            "security_name": row["security_name"],
            "value": round(float(row["value"]), 2),
            "weight_pct": round(float(weight), 4),
            "type_display": row["type_display"],
            "quantity": round(float(row["quantity"]), 6),
        })

    # Sort by value descending
    positions.sort(key=lambda p: p["value"], reverse=True)

    # Allocation by type_display
    alloc_raw = df.groupby("type_display")["value"].sum()
    allocation = {k: round(v / total * 100, 4) for k, v in alloc_raw.items()} if total else {}

    return {
        "total_value": round(float(total), 2),
        "positions": positions,
        "allocation": allocation,
    }


# ---------------------------------------------------------------------------
# 6. Risk Reporting (Concentration & Cost Efficiency)
# ---------------------------------------------------------------------------

CONC_THRESHOLD = float(os.environ.get("CONC_THRESHOLD", 10.0))
_fund_cache: dict = {}  # keyed by ticker


def get_fund_details(ticker: str) -> dict:
    """
    Fetch expense ratio and top holdings for a fund ticker.
    Returns: {"expense_ratio": float or None, "holdings": [{"ticker": str, "weight": float}, ...]}
    """
    import yfinance as yf
    import pandas as pd

    if ticker in _fund_cache:
        return _fund_cache[ticker]

    try:
        t = yf.Ticker(ticker)
        info = t.info
        raw_ratio = info.get("netExpenseRatio") or info.get("expenseRatio")
        # yfinance returns these as percentages (e.g. 0.03 for 0.03%), 
        # so divide by 100 for decimal representation (0.0003)
        expense_ratio = float(raw_ratio) / 100 if raw_ratio is not None else None

        holdings = []
        if hasattr(t, "funds_data") and t.funds_data.top_holdings is not None:
            df_holdings = t.funds_data.top_holdings
            if not df_holdings.empty:
                # The index is the ticker symbol
                for symbol, row in df_holdings.iterrows():
                    weight = row.get("Holding Percent") or row.get("Weight") or 0.0
                    holdings.append({"ticker": str(symbol), "weight": float(weight)})

        res = {"expense_ratio": expense_ratio, "holdings": holdings}
        _fund_cache[ticker] = res
        return res
    except Exception as e:
        print(f"Error fetching fund details for {ticker}: {e}", file=sys.stderr)
        return {"expense_ratio": None, "holdings": []}


def save_risk_snapshot(risk_data: dict, db_path: str = "risk_history.db"):
    """
    Save a snapshot of risk metrics (WER and total cost) to a local SQLite database.
    """
    import sqlite3
    from datetime import datetime

    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS risk_snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                wer REAL NOT NULL,
                total_annual_cost REAL NOT NULL
            )
        """)
        
        cursor.execute("""
            INSERT INTO risk_snapshots (timestamp, wer, total_annual_cost)
            VALUES (?, ?, ?)
        """, (
            datetime.now().isoformat(),
            risk_data["wer"],
            risk_data["total_annual_cost"]
        ))
        
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"Error saving risk snapshot: {e}", file=sys.stderr)


def calculate_efficiency_metrics(df, growth_rate=0.07, benchmark_fee=0.0005) -> dict:
    """
    Calculate detailed portfolio efficiency metrics, including wealth gap projections
    and high-cost asset benchmarking.
    """
    total_value = df["value"].sum()
    if total_value <= 0:
        return {
            "weighted_expense_ratio": 0.0,
            "total_annual_cost": 0.0,
            "projections": [],
            "high_cost_assets": []
        }

    # 1. Individual Asset Analysis
    total_annual_cost = 0.0
    optimized_annual_cost = 0.0
    high_cost_assets = []
    
    for _, row in df.iterrows():
        val = float(row["value"])
        ticker = row["ticker"]
        exp_ratio = 0.0
        
        # Check ANY ticker for expense ratio (some funds are misclassified as Stocks)
        if ticker:
            details = get_fund_details(ticker)
            exp_ratio = float(details.get("expense_ratio") or 0.0)
            
            if exp_ratio > 0:
                annual_cost = val * exp_ratio
                total_annual_cost += annual_cost
                
                # Optimized cost: cap at benchmark_fee (e.g. 0.05%)
                optimized_annual_cost += val * min(exp_ratio, benchmark_fee)
                
                # Benchmarking
                status = "Green"
                if exp_ratio > 0.005: status = "Red"
                elif exp_ratio > 0.002: status = "Amber"
                
                potential_savings = val * (exp_ratio - benchmark_fee) if exp_ratio > benchmark_fee else 0.0
                
                high_cost_assets.append({
                    "ticker": ticker,
                    "name": row["security_name"],
                    "value": round(val, 2),
                    "exp_ratio": round(exp_ratio, 4),
                    "annual_cost": round(annual_cost, 2),
                    "potential_savings": round(float(potential_savings), 2),
                    "status": status
                })
            else:
                # No fee found (Stock or non-fund cash)
                optimized_annual_cost += 0.0
        else:
            # No ticker
            optimized_annual_cost += 0.0

    wer = total_annual_cost / total_value
    optimized_wer = optimized_annual_cost / total_value
    
    # 2. Wealth Gap Projections (10, 20, 30 years)
    projections = []
    for years in [10, 20, 30]:
        # Scenario A: Current (r - f)
        current_val = total_value * ((1 + (growth_rate - wer)) ** years)
        # Scenario B: Optimized (r - f_opt)
        optimized_val = total_value * ((1 + (growth_rate - optimized_wer)) ** years)
        wealth_gap = optimized_val - current_val
        
        projections.append({
            "years": years,
            "current_val": round(float(current_val), 2),
            "optimized_val": round(float(optimized_val), 2),
            "wealth_gap": round(float(wealth_gap), 2)
        })

    return {
        "weighted_expense_ratio": round(float(wer), 6),
        "total_annual_cost": round(float(total_annual_cost), 2),
        "projections": projections,
        "high_cost_assets": sorted(high_cost_assets, key=lambda x: x["annual_cost"], reverse=True)
    }


def calculate_risk_metrics(df) -> dict:
    """
    Calculate True Exposure (direct + indirect) and Weighted Expense Ratio.

    Returns:
    {
        "true_exposure": [{"ticker": str, "security_name": str, "value": float, "weight_pct": float, "direct": float, "indirect": float, "flagged": bool}, ...],
        "wer": float,
        "total_annual_cost": float,
    }
    """
    total_value = df["value"].sum()
    if total_value == 0:
        return {"true_exposure": [], "wer": 0.0, "total_annual_cost": 0.0}

    # 1. Identify Funds (ETF/Mutual Fund) and calculate costs
    total_annual_cost = 0.0
    fund_holdings_map = {}  # fund_ticker -> {holdings: [...]}
    funds_to_exclude = set()

    for _, row in df.iterrows():
        is_fund = row["type_display"] in ["ETF", "Mutual Fund"]
        if is_fund and row["ticker"]:
            details = get_fund_details(row["ticker"])
            if details["expense_ratio"]:
                pos_cost = row["value"] * details["expense_ratio"]
                total_annual_cost += pos_cost

            if details["holdings"]:
                fund_holdings_map[row["ticker"]] = {
                    "holdings": details["holdings"],
                    "value": row["value"]
                }
                funds_to_exclude.add(row["ticker"])

    wer = total_annual_cost / total_value

    # 2. Calculate True Exposure
    # Separate direct and indirect exposures
    exposure_direct = {}    # ticker -> value
    exposure_indirect = {}  # ticker -> value
    ticker_names = {}       # ticker -> name (best guess)

    for _, row in df.iterrows():
        # For concentration risk, we AGGREGATE share classes (e.g. GOOG -> GOOGL)
        ticker = normalize_ticker(row["ticker"] or f"UNKNOWN_{row['security_id']}", aggregate_classes=True)
        exposure_direct[ticker] = exposure_direct.get(ticker, 0.0) + row["value"]
        ticker_names[ticker] = row["security_name"]

    # Add indirect exposures from funds
    for fund_ticker, data in fund_holdings_map.items():
        fund_value = data["value"]
        for h in data["holdings"]:
            # Aggregate indirect holdings too (e.g. VOO might hold both GOOG and GOOGL)
            h_ticker = normalize_ticker(h["ticker"], aggregate_classes=True)
            h_weight = h["weight"]
            indirect_value = fund_value * h_weight
            exposure_indirect[h_ticker] = exposure_indirect.get(h_ticker, 0.0) + indirect_value
            if h_ticker not in ticker_names:
                ticker_names[h_ticker] = f"Indirect: {h_ticker}"

    # Prepare results
    all_tickers = set(exposure_direct.keys()) | set(exposure_indirect.keys())
    true_exposure = []
    for ticker in all_tickers:
        # If it's a fund we looked through, we don't list it as a stock, 
        # but we might want to keep it if it has no underlying (already handled by set logic)
        if ticker in funds_to_exclude and ticker not in exposure_indirect:
            # It's a fund we expanded, so we don't show it as its own ticker 
            # UNLESS it was also an indirect holding of another fund (unlikely but possible)
            continue

        dir_val = exposure_direct.get(ticker, 0.0)
        ind_val = exposure_indirect.get(ticker, 0.0)
        total_val = dir_val + ind_val
        weight_pct = (total_val / total_value * 100)
        
        true_exposure.append({
            "ticker": ticker,
            "security_name": ticker_names.get(ticker, ticker),
            "value": round(float(total_val), 2),
            "direct": round(float(dir_val), 2),
            "indirect": round(float(ind_val), 2),
            "weight_pct": round(float(weight_pct), 4),
            "flagged": bool(weight_pct > CONC_THRESHOLD)
        })

    true_exposure.sort(key=lambda x: x["weight_pct"], reverse=True)

    return {
        "true_exposure": true_exposure,
        "wer": round(float(wer), 6),
        "total_annual_cost": round(float(total_annual_cost), 2),
        "threshold": CONC_THRESHOLD,
    }


def check_concentration(df) -> list:
    """
    Deprecated: use calculate_risk_metrics instead.
    Flag any position whose portfolio weight exceeds CONC_THRESHOLD.
    """
    total = df["value"].sum()
    results = []
    for _, row in df.iterrows():
        weight = (row["value"] / total * 100) if total else 0.0
        if weight > CONC_THRESHOLD:
            results.append({
                "ticker": row["ticker"],
                "security_name": row["security_name"],
                "weight_pct": round(float(weight), 4),
                "threshold": CONC_THRESHOLD,
                "flagged": True,
            })
    return sorted(results, key=lambda x: x["weight_pct"], reverse=True)


# ---------------------------------------------------------------------------
# 7. Institutions summary
# ---------------------------------------------------------------------------

def calculate_institutions(df_raw) -> list:
    """Summarize total value per institution from the raw (pre-dedup) DataFrame."""
    import pandas as pd
    df = df_raw.copy()
    df["value"] = pd.to_numeric(df["value"], errors="coerce").fillna(0)
    grouped = df.groupby("institution_name")["value"].sum().reset_index()
    total = grouped["value"].sum()
    grouped["weight_pct"] = (grouped["value"] / total * 100).round(4)
    grouped = grouped.sort_values("value", ascending=False)
    return grouped.to_dict(orient="records")


# ---------------------------------------------------------------------------
# 8. Market data enrichment (yfinance)
# ---------------------------------------------------------------------------

YFINANCE_SKIP_TICKERS: set = {"FCASH", "CUR:USD"}
_market_cache: dict = {}  # keyed by ticker string


def calculate_sector_allocation(positions: list) -> dict:
    """
    Calculate portfolio allocation by economic sector.
    Returns: {"Technology": 40.5, "Financial Services": 20.1, ...}
    """
    # Use global market cache to resolve sectors for tickers
    sector_values = {}
    total_market_value = 0

    for p in positions:
        ticker = p.get("ticker")
        val = p.get("value") or 0
        
        # Default to "Other/Unknown" if no sector found
        sector = "Other/Unknown"
        if p.get("type_display") == "Fixed Income":
            sector = "Fixed Income"
        elif p.get("type_display") == "Cash":
            sector = "Cash"
        elif ticker and ticker in _market_cache:
            sector = _market_cache[ticker].get("sector") or "Other/Unknown"

        sector_values[sector] = sector_values.get(sector, 0.0) + val
        total_market_value += val

    if total_market_value == 0:
        return {}

    # Convert to percentages
    return {
        s: round((v / total_market_value) * 100, 2)
        for s, v in sector_values.items()
    }


def enrich_with_market_data(positions: list) -> list:
    """
    Enrich each position dict with market data from yfinance.

    Adds: dividend_yield, dividend_rate, ex_dividend_date, payout_ratio,
          trailing_eps, forward_eps, trailing_pe, forward_pe,
          market_cap, sector, industry, earnings_timestamp.
    Fields are None if ticker is skipped or lookup fails.
    Only ticker symbols leave the machine.
    """
    import yfinance as yf

    _FIELDS = [
        "dividend_yield", "dividend_rate", "ex_dividend_date", "payout_ratio",
        "trailing_eps", "forward_eps", "trailing_pe", "forward_pe",
        "market_cap", "sector", "industry", "earnings_timestamp",
    ]
    _YF_MAP = {
        "dividend_yield":    "dividendYield",
        "dividend_rate":     "dividendRate",
        "ex_dividend_date":  "exDividendDate",
        "payout_ratio":      "payoutRatio",
        "trailing_eps":      "trailingEps",
        "forward_eps":       "forwardEps",
        "trailing_pe":       "trailingPE",
        "forward_pe":        "forwardPE",
        "market_cap":        "marketCap",
        "sector":            "sector",
        "industry":          "industry",
        "earnings_timestamp": "earningsTimestamp",
    }

    # Collect unique tickers to fetch
    unique_tickers = {
        p["ticker"] for p in positions
        if p.get("ticker") and p["ticker"] not in YFINANCE_SKIP_TICKERS
    }

    for t in unique_tickers:
        if t in _market_cache:
            continue
        try:
            info = yf.Ticker(t).info
            _market_cache[t] = {k: info.get(yf_key) for k, yf_key in _YF_MAP.items()}
        except Exception:
            _market_cache[t] = {k: None for k in _FIELDS}

    enriched = []
    for pos in positions:
        p = dict(pos)
        ticker = p.get("ticker", "")
        if ticker and ticker not in YFINANCE_SKIP_TICKERS:
            market = _market_cache.get(ticker, {k: None for k in _FIELDS})
        else:
            market = {k: None for k in _FIELDS}
        p.update(market)
        enriched.append(p)

    return enriched


# ---------------------------------------------------------------------------
# 9. Tax bucket calculation
# ---------------------------------------------------------------------------

_TAX_RULES = [
    ("Roth", "Tax-Exempt (Roth)"),  # must precede IRA so "Roth IRA" → exempt
    ("IRA",  "Tax-Deferred"),
    ("401",  "Tax-Deferred"),
]


def _classify_account(account_name: str) -> str:
    for pattern, bucket in _TAX_RULES:
        if pattern in account_name:
            return bucket
    return "Taxable"


def calculate_tax_buckets(df_raw) -> dict:
    """
    Group accounts into tax buckets based on account_name patterns.

    Returns a dict with total_value and per-bucket breakdown including
    accounts and their holdings sorted by value desc.
    """
    import pandas as pd

    df = df_raw.copy()
    df["value"] = pd.to_numeric(df["value"], errors="coerce").fillna(0)

    total_value = float(df["value"].sum())
    buckets: dict = {}

    for account_name, acct_df in df.groupby("account_name"):
        bucket_label = _classify_account(str(account_name))
        acct_value = float(acct_df["value"].sum())
        institution = str(acct_df["institution_name"].iloc[0]) if len(acct_df) else ""

        has_cost_basis = "cost_basis" in acct_df.columns
        holdings = []
        for _, row in acct_df.iterrows():
            if float(row["value"]) < 0.01:
                continue
            
            ticker = normalize_ticker(str(row["ticker"]) or f"UNKNOWN_{row['security_id']}")
            
            # Apply asset class overrides consistently
            type_display = str(row["type_display"])
            if ticker in CASH_TICKERS: type_display = "Cash"
            elif ticker in FIXED_INCOME_TICKERS: type_display = "Fixed Income"
            elif ticker in MUTUAL_FUND_TICKERS: type_display = "Mutual Fund"

            holdings.append({
                "ticker": ticker,
                "security_name": str(row["security_name"]),
                "quantity": round(float(pd.to_numeric(row["quantity"], errors="coerce") or 0), 6),
                "value": round(float(row["value"]), 2),
                "cost_basis": round(float(cb), 2) if has_cost_basis and not pd.isna(cb := pd.to_numeric(row["cost_basis"], errors="coerce")) else None,
                "type_display": type_display,
            })
        
        holdings.sort(key=lambda h: h["value"], reverse=True)

        account_entry = {
            "account_name": str(account_name),
            "institution_name": institution,
            "value": round(acct_value, 2),
            "holdings": holdings,
        }

        if bucket_label not in buckets:
            buckets[bucket_label] = {"value": 0.0, "accounts": []}
        buckets[bucket_label]["value"] += acct_value
        buckets[bucket_label]["accounts"].append(account_entry)

    # Sort accounts within each bucket by value desc
    for label, bucket in buckets.items():
        bucket["value"] = round(bucket["value"], 2)
        bucket["weight_pct"] = round(bucket["value"] / total_value * 100, 4) if total_value else 0.0
        bucket["accounts"].sort(key=lambda a: a["value"], reverse=True)

    # Sort buckets by value desc
    sorted_buckets = dict(
        sorted(buckets.items(), key=lambda x: x[1]["value"], reverse=True)
    )

    return {
        "total_value": round(total_value, 2),
        "buckets": sorted_buckets,
    }


# ---------------------------------------------------------------------------
# 10. Buffett Valuation
# ---------------------------------------------------------------------------

_valuation_cache: dict = {}


def calculate_valuation_metrics(positions: list) -> list:
    """
    Calculate Buffett-style valuation metrics for stocks in the portfolio.
    Includes Owner Earnings, ROE, Debt-to-Equity, and a simplified DCF intrinsic value.
    """
    import yfinance as yf
    import pandas as pd
    import sys

    # 1. Get Risk-Free Rate (^TNX)
    try:
        tnx = yf.Ticker("^TNX")
        rf_rate_raw = tnx.info.get("regularMarketPrice") or tnx.info.get("previousClose")
        rf_rate = float(rf_rate_raw) / 100 if rf_rate_raw else 0.04
    except Exception:
        rf_rate = 0.04

    # 2. Identify unique stock tickers (relaxed type check)
    unique_tickers = set()
    for p in positions:
        ticker = p.get("ticker")
        if not ticker or ticker in YFINANCE_SKIP_TICKERS or ticker.startswith("UNKNOWN"):
            continue
        
        # Consider anything as "Stock" if it's explicitly labelled or if it's not Cash/Fixed Income/ETF
        is_equity = p.get("type_display") in ["Stock", "Equity", "Equity / ETF"]
        # Fallback: if it's not Cash/ETF/Mutual Fund and has a valid-looking ticker
        if not is_equity and p.get("type_display") not in ["Cash", "Fixed Income", "ETF", "Mutual Fund"]:
            is_equity = True
            
        if is_equity:
            unique_tickers.add(ticker)

    print(f"Valuation: analyzing {len(unique_tickers)} tickers: {unique_tickers}", flush=True)

    results = []
    for ticker in unique_tickers:
        if ticker in _valuation_cache:
            results.append(_valuation_cache[ticker])
            continue
        
        # Default assumptions
        growth_rate = 0.05
        terminal_growth_rate = 0.02
        
        try:
            # yfinance sometimes prefers . over - for share classes
            yf_ticker = ticker.replace("-", ".")
            t = yf.Ticker(yf_ticker)
            
            # Use fast_info if available or check info
            info = t.info
            if not info or not info.get("shortName"):
                # Try fallback to original ticker
                t = yf.Ticker(ticker)
                info = t.info
                if not info or not info.get("shortName"):
                    continue

            inc = t.income_stmt
            bal = t.balance_sheet
            cf = t.cashflow

            if inc.empty or bal.empty or cf.empty:
                print(f"Valuation: missing financial statements for {ticker}", flush=True)
                continue

            # Latest annual figures (first column)
            def get_val(df, keys):
                for k in keys:
                    if k in df.index and not df.loc[k].empty:
                        return float(df.loc[k].iloc[0])
                return 0.0

            net_income = get_val(inc, ["Net Income", "Net Income Common Stockholders"])
            if net_income == 0:
                print(f"Valuation: skipping {ticker} due to missing Net Income", flush=True)
                continue
            
            # Depreciation & Amortization
            dep = get_val(cf, ["Depreciation And Amortization", "Depreciation Amortization Depletion", "Reconciled Depreciation"])
                
            # CapEx
            capex = abs(get_val(cf, ["Capital Expenditure", "Purchase Of PPE", "Purchase Of Business"]))
            
            # Maintenance CapEx estimate: Min of total CapEx and Depreciation (conservative approximation)
            maint_capex = min(capex, dep) if dep > 0 else capex
            owner_earnings = net_income + dep - maint_capex
            
            # Equity & Debt
            equity = get_val(bal, ["Stockholders Equity", "Common Stock Equity", "Total Equity Gross Minority Interest"])
                
            roe = net_income / equity if equity > 0 else 0
            
            debt = get_val(bal, ["Total Debt", "Net Debt", "Long Term Debt"])
            debt_to_equity = debt / equity if equity > 0 else 0
            
            gross_margin = info.get("grossMargins") or 0
            net_margin = info.get("profitMargins") or 0
            
            # Simplified DCF (10 years)
            risk_premium = 0.04 # 4% Equity Risk Premium (Professional Standard)
            discount_rate = max(rf_rate, 0.058) + risk_premium # Rf (min 5.8%) + ERP
            
            # Sum of PV of Owner Earnings for 10 years
            pv_sum = 0
            current_oe = owner_earnings
            for i in range(1, 11):
                current_oe *= (1 + growth_rate)
                pv_sum += current_oe / ((1 + discount_rate) ** i)
            
            # Terminal Value (Year 10)
            terminal_val = (current_oe * (1 + terminal_growth_rate)) / (discount_rate - terminal_growth_rate)
            pv_terminal = terminal_val / ((1 + discount_rate) ** 10)
            
            intrinsic_value_total = pv_sum + pv_terminal
            shares = info.get("impliedSharesOutstanding") or info.get("sharesOutstanding")
            
            current_price = info.get("currentPrice") or info.get("regularMarketPrice") or 0
            owner_earnings_ps = 0.0
            if shares and shares > 0:
                intrinsic_price = intrinsic_value_total / shares
                mos = (1 - (current_price / intrinsic_price)) if intrinsic_price > 0 else -1
                owner_earnings_ps = owner_earnings / shares
            else:
                intrinsic_price = 0
                mos = -1

            # Calculate user's proportional share of owner earnings
            pos_match = [p for p in positions if p.get("ticker") == ticker]
            total_qty = sum(p.get("quantity") or 0 for p in pos_match)
            portfolio_owner_earnings = total_qty * owner_earnings_ps

            # Quality Score (0-100)
            # ROE > 15% (25 pts), Debt/Equity < 0.5 (25 pts), Gross Margin > 40% (25 pts), Net Margin > 10% (25 pts)
            score = 0
            if roe > 0.15: score += 25
            if debt_to_equity < 0.5: score += 25
            if gross_margin > 0.40: score += 25
            if net_margin > 0.10: score += 25

            val_data = {
                "ticker": ticker,
                "security_name": info.get("longName") or ticker,
                "current_price": round(float(current_price), 2),
                "intrinsic_price": round(float(intrinsic_price), 2),
                "mos": round(float(mos), 4),
                "quality_score": score,
                "roe": round(float(roe), 4),
                "debt_to_equity": round(float(debt_to_equity), 4),
                "owner_earnings_ps": round(float(owner_earnings_ps), 2),
                "portfolio_owner_earnings": round(float(portfolio_owner_earnings), 2),
                "discount_rate": round(float(discount_rate), 4),
                "growth_rate": round(float(growth_rate), 4),
                "terminal_growth_rate": round(float(terminal_growth_rate), 4),
                "gross_margin": round(float(gross_margin), 4),
                "net_margin": round(float(net_margin), 4),
            }
            _valuation_cache[ticker] = val_data
            results.append(val_data)
        except Exception as e:
            print(f"Error valuing {ticker}: {e}", file=sys.stderr)

    return sorted(results, key=lambda x: x["quality_score"], reverse=True)


# ---------------------------------------------------------------------------
# 11. CLI entry point
# ---------------------------------------------------------------------------

def main():
    check_gitignore()

    df_raw = load()
    df = deduplicate(df_raw)
    df = normalize_asset_class(df)
    metrics = calculate_metrics(df)
    concentration = check_concentration(df)

    try:
        from tabulate import tabulate
        _tabulate = tabulate
    except ImportError:
        def _tabulate(rows, headers=(), tablefmt="simple", **_):
            lines = ["  ".join(str(h) for h in headers)]
            for row in rows:
                lines.append("  ".join(str(c) for c in row))
            return "\n".join(lines)

    print(f"\nTotal Portfolio Value: ${metrics['total_value']:,.2f}\n")

    # Allocation summary
    alloc_rows = sorted(metrics["allocation"].items(), key=lambda x: x[1], reverse=True)
    print(_tabulate(
        [(k, f"{v:.2f}%") for k, v in alloc_rows],
        headers=["Asset Class", "Weight"],
        tablefmt="simple",
    ))
    print()

    # Positions table
    pos_rows = [
        (p["ticker"], p["security_name"][:40], f"${p['value']:,.2f}", f"{p['weight_pct']:.2f}%", p["type_display"])
        for p in metrics["positions"]
    ]
    print(_tabulate(
        pos_rows,
        headers=["Ticker", "Name", "Value", "Weight", "Type"],
        tablefmt="simple",
    ))
    print()

    # Concentration flags
    if concentration:
        print(f"CONCENTRATION RISK FLAGS (>{CONC_THRESHOLD}%):")
        for f in concentration:
            print(f"  {f['ticker']}: {f['weight_pct']:.2f}%")
    else:
        print(f"No concentration flags triggered (threshold: {CONC_THRESHOLD}%).")


if __name__ == "__main__":
    try:
        main()
    except (RuntimeError, ValueError, FileNotFoundError) as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)
