"""
loader.py — Munger portfolio data loader

Reads holdings from Google Sheets or local CSV, deduplicates by security_id,
normalizes asset classes, calculates portfolio metrics, and flags concentration risk.
"""

import os
import sys
import json
import pandas as pd
from dotenv import load_dotenv

# Load env vars from .env if present
load_dotenv()

# ---------------------------------------------------------------------------
# 1. Configuration & Security
# ---------------------------------------------------------------------------

MONARCH_JSON_PATH = "monarch_response.json"
CSV_PATH = "holdings.csv"
SHEET_ID = os.environ.get("GOOGLE_SHEET_ID")

# Tickers that yfinance consistently fails on or that are non-equities
YFINANCE_SKIP_TICKERS = {"FCASH", "USD-USD", "CASH", "TOTAL", "UNKNOWN"}


def check_gitignore():
    """Ensure sensitive files are ignored by git."""
    patterns = {"*.csv", "*.json", "*.env", "*.db", ".data/"}
    if not os.path.exists(".gitignore"):
        print("WARNING: .gitignore missing. Security risk!")
        return

    with open(".gitignore", "r") as f:
        content = f.read()
        missing = [p for p in patterns if p not in content]
        if missing:
            print(f"CRITICAL: .gitignore missing patterns: {missing}")
            print("Refusing to start until .gitignore is secured.")
            sys.exit(1)


# ---------------------------------------------------------------------------
# 2. Data Loading
# ---------------------------------------------------------------------------

def load() -> pd.DataFrame:
    """
    Dispatcher to load data from Monarch JSON, CSV, or Google Sheets.
    Priority: Monarch > CSV > Google Sheets.
    """
    if os.path.exists(MONARCH_JSON_PATH):
        return load_from_monarch(MONARCH_JSON_PATH)
    if os.path.exists(CSV_PATH):
        return load_from_csv(CSV_PATH)
    if SHEET_ID:
        return load_from_sheets(SHEET_ID)

    raise FileNotFoundError("No data source found (monarch_response.json, holdings.csv, or GOOGLE_SHEET_ID).")


def load_from_monarch(path: str) -> pd.DataFrame:
    """Parses the Monarch Money GraphQL response saved as JSON."""
    print(f"Loading from Monarch JSON: {path}")
    with open(path, "r") as f:
        data = json.load(f)

    rows = []
    # Path: data.portfolio.aggregateHoldings.edges[].node.holdings[]
    try:
        edges = data["data"]["portfolio"]["aggregateHoldings"]["edges"]
        for edge in edges:
            account_name = edge["node"]["account"]["displayName"]
            for h in edge["node"]["holdings"]:
                rows.append({
                    "ticker": h.get("ticker"),
                    "security_name": h.get("securityName"),
                    "quantity": float(h.get("quantity", 0)),
                    "value": float(h.get("value", 0)),
                    "cost_basis": float(h.get("costBasis")) if h.get("costBasis") else None,
                    "type_display": h.get("typeDisplay"),
                    "account_name": account_name,
                    "security_id": h.get("securityId"),
                })
    except (KeyError, TypeError) as e:
        print(f"Error parsing Monarch JSON: {e}")
        return pd.DataFrame()

    return pd.DataFrame(rows)


def load_from_csv(path: str) -> pd.DataFrame:
    """Loads holdings from a local CSV."""
    print(f"Loading from CSV: {path}")
    return pd.read_csv(path)


def load_from_sheets(sheet_id: str) -> pd.DataFrame:
    """Loads holdings from Google Sheets using OAuth."""
    print(f"Loading from Google Sheets: {sheet_id}")
    # Implementation omitted for brevity; returns same schema as above
    return pd.DataFrame()


# ---------------------------------------------------------------------------
# 3. Data Processing
# ---------------------------------------------------------------------------

def deduplicate(df: pd.DataFrame) -> pd.DataFrame:
    """
    Aggregates holdings by security_id.
    Sum quantity, value, and cost_basis. Preserve ticker/name/type.
    """
    if df.empty:
        return df

    # We group by security_id to merge same positions across different accounts
    agg_funcs = {
        "ticker": "first",
        "security_name": "first",
        "quantity": "sum",
        "value": "sum",
        "cost_basis": "sum",
        "type_display": "first",
        "account_name": lambda x: ", ".join(x.unique()),
    }
    return df.groupby("security_id").agg(agg_funcs).reset_index()


def normalize_asset_class(df: pd.DataFrame) -> pd.DataFrame:
    """Normalizes the asset class names from Monarch to Munger standards."""
    if df.empty:
        return df

    mapping = {
        "Cash": "Cash",
        "Fixed Income": "Fixed Income",
        "Equity": "Stock",
        "Equity / ETF": "ETF",
        "ETF": "ETF",
        "Mutual Fund": "Mutual Fund",
        "Stock": "Stock",
    }
    df["type_display"] = df["type_display"].map(lambda x: mapping.get(x, "Other"))
    return df


# ---------------------------------------------------------------------------
# 4. Portfolio Metrics
# ---------------------------------------------------------------------------

def calculate_metrics(df: pd.DataFrame) -> dict:
    """Calculates top-level portfolio statistics."""
    if df.empty:
        return {"total_value": 0, "allocation": {}, "positions": []}

    total_value = df["value"].sum()
    
    # Asset Allocation
    alloc = df.groupby("type_display")["value"].sum() / total_value * 100
    
    positions = df.to_dict("records")
    for p in positions:
        p["weight_pct"] = (p["value"] / total_value) * 100

    return {
        "total_value": float(total_value),
        "allocation": alloc.to_dict(),
        "positions": sorted(positions, key=lambda x: x["value"], reverse=True),
    }


# ---------------------------------------------------------------------------
# 5. Market Data Enrichment
# ---------------------------------------------------------------------------

_market_cache: dict = {}


def enrich_with_market_data(positions: list) -> list:
    """Enriches position data with real-time stats from yfinance."""
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

    # 1. Fetch data for primary tickers
    for t in unique_tickers:
        if t in _market_cache:
            continue
        try:
            ticker_obj = yf.Ticker(t)
            info = ticker_obj.info
            _market_cache[t] = {k: info.get(yf_key) for k, yf_key in _YF_MAP.items()}
            
            # 2. Look-through for ETFs
            # If it's an ETF and missing Trailing PE, or if we want better accuracy via look-through
            if info.get("quoteType") == "ETF":
                details = get_fund_details(t)
                if details.get("holdings"):
                    total_earn_yield = 0.0
                    weight_covered = 0.0
                    
                    # Collect and fetch underlying tickers if not in cache
                    underlying_tickers = [h["ticker"] for h in details["holdings"] if h["ticker"] not in _market_cache]
                    for ut in underlying_tickers:
                        try:
                            u_info = yf.Ticker(ut).info
                            _market_cache[ut] = {k: u_info.get(yf_key) for k, yf_key in _YF_MAP.items()}
                        except Exception:
                            _market_cache[ut] = {k: None for k in _FIELDS}
                    
                    for h in details["holdings"]:
                        h_ticker = h["ticker"]
                        h_data = _market_cache.get(h_ticker, {})
                        pe = h_data.get("trailing_pe")
                        if pe and pe > 0:
                            total_earn_yield += (1.0 / pe) * h["weight"]
                            weight_covered += h["weight"]
                    
                    if weight_covered > 0.10: # Only override if we have decent coverage
                        avg_yield = total_earn_yield / weight_covered
                        if avg_yield > 0:
                            _market_cache[t]["trailing_pe"] = 1.0 / avg_yield
                            print(f"Look-through: ETF {t} calculated PE {1.0/avg_yield:.2f} via {weight_covered:.1%} coverage", flush=True)

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
        cursor.execute('''CREATE TABLE IF NOT EXISTS risk_snapshots
                          (timestamp TEXT, wer REAL, total_annual_cost REAL)''')
        
        now = datetime.now().isoformat()
        cursor.execute("INSERT INTO risk_snapshots VALUES (?, ?, ?)",
                       (now, risk_data["wer"], risk_data["total_annual_cost"]))
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"Error saving risk snapshot: {e}", file=sys.stderr)


def calculate_risk_metrics(df: pd.DataFrame) -> dict:
    """
    Calculates the 'True Exposure' for every security by performing look-through 
    on funds. Also calculates total portfolio cost metrics.
    """
    if df.empty:
        return {"true_exposure": [], "wer": 0, "total_annual_cost": 0}

    total_value = df["value"].sum()
    exposure = {} # ticker -> {direct: val, indirect: val, name: str}

    # 1. First pass: direct positions
    for _, row in df.iterrows():
        ticker = row.get("ticker")
        if not ticker or ticker in YFINANCE_SKIP_TICKERS:
            continue
        
        if ticker not in exposure:
            exposure[ticker] = {"direct": 0.0, "indirect": 0.0, "name": row["security_name"]}
        
        if row["type_display"] == "Stock":
            exposure[ticker]["direct"] += row["value"]

    # 2. Second pass: fund look-through
    fund_costs = []
    for _, row in df.iterrows():
        ticker = row.get("ticker")
        if ticker and row["type_display"] in ["ETF", "Mutual Fund"]:
            details = get_fund_details(ticker)
            
            # Expense Ratio
            er = details.get("expense_ratio")
            if er is not None:
                fund_costs.append(row["value"] * er)

            # Underlying holdings
            for h in details.get("holdings", []):
                u_ticker = h["ticker"]
                if u_ticker not in exposure:
                    exposure[u_ticker] = {"direct": 0.0, "indirect": 0.0, "name": u_ticker}
                exposure[u_ticker]["indirect"] += row["value"] * h["weight"]

    # 3. Finalize true exposure
    results = []
    for ticker, vals in exposure.items():
        total_exp = vals["direct"] + vals["indirect"]
        weight_pct = (total_exp / total_value) * 100
        if total_exp > 0:
            results.append({
                "ticker": ticker,
                "security_name": vals["name"],
                "direct_value": round(vals["direct"], 2),
                "indirect_value": round(vals["indirect"], 2),
                "total_value": round(total_exp, 2),
                "weight_pct": round(weight_pct, 2),
                "flagged": weight_pct > CONC_THRESHOLD
            })

    total_annual_cost = sum(fund_costs)
    wer = total_annual_cost / total_value if total_value > 0 else 0

    return {
        "true_exposure": sorted(results, key=lambda x: x["total_value"], reverse=True),
        "wer": round(float(wer), 6),
        "total_annual_cost": round(float(total_annual_cost), 2)
    }


def check_concentration(df: pd.DataFrame) -> list:
    """Identifies positions exceeding the CONC_THRESHOLD."""
    if df.empty:
        return []
    total = df["value"].sum()
    df["weight_pct"] = (df["value"] / total) * 100
    flags = df[df["weight_pct"] > CONC_THRESHOLD].to_dict("records")
    return flags


def calculate_sector_allocation(positions: list) -> dict:
    """Calculates portfolio weight by sector."""
    sectors = {}
    total = sum(p["value"] for p in positions)
    if total == 0:
        return {}

    for p in positions:
        sec = p.get("sector") or "Other"
        sectors[sec] = sectors.get(sec, 0) + p["value"]

    # Convert to percentages
    return {k: (v / total) * 100 for k, v in sectors.items()}


def calculate_institutions(positions: list) -> dict:
    """Finds top institutional owners for the portfolio's top positions."""
    # Placeholder for future enhancement (e.g. scraping or premium API)
    return {}


# ---------------------------------------------------------------------------
# 7. Efficiency Metrics
# ---------------------------------------------------------------------------

def calculate_efficiency_metrics(df: pd.DataFrame) -> dict:
    """
    Calculates cost efficiency metrics (WER, expense ratio impact).
    """
    if df.empty:
        return {"wer": 0, "total_annual_cost": 0, "projections": []}

    total_value = df["value"].sum()
    fund_costs = []
    
    for _, row in df.iterrows():
        ticker = row.get("ticker")
        if ticker and row.get("type_display") in ["ETF", "Mutual Fund"]:
            details = get_fund_details(ticker)
            er = details.get("expense_ratio")
            if er is not None:
                fund_costs.append({
                    "ticker": ticker,
                    "value": row["value"],
                    "er": er,
                    "annual_cost": row["value"] * er
                })

    total_annual_cost = sum(c["annual_cost"] for c in fund_costs)
    wer = (total_annual_cost / total_value) if total_value > 0 else 0

    # Projection of costs over 30 years vs a benchmark (e.g. 0.05% for VOO)
    benchmark_er = 0.0005
    optimized_annual_cost = total_value * benchmark_er
    optimized_wer = benchmark_er
    
    growth_rate = 0.07 # 7% assumed market return
    projections = []
    for years in [5, 10, 20, 30]:
        current_val = total_value * ((1 + (growth_rate - wer)) ** years)
        optimized_val = total_value * ((1 + (growth_rate - optimized_wer)) ** years)
        wealth_gap = optimized_val - current_val
        
        projections.append({
            "years": years,
            "current_val": round(float(current_val), 2),
            "optimized_val": round(float(optimized_val), 2),
            "wealth_gap": round(float(wealth_gap), 2)
        })

    return {
        "wer": round(float(wer), 6),
        "total_annual_cost": round(float(total_annual_cost), 2),
        "projections": projections,
        "fund_costs": fund_costs
    }


# ---------------------------------------------------------------------------
# 8. Buffett Valuation (New Intrinsic Value Methodology)
# ---------------------------------------------------------------------------

_valuation_cache: dict = {}

def _fetch_valuation_inputs(ticker_symbol: str, rf_rate: float):
    """
    Fetch raw financial data for WACC and FCF DCF calculation.
    """
    import yfinance as yf
    try:
        t = yf.Ticker(ticker_symbol.replace("-", "."))
        info = t.info
        if not info or not info.get("shortName"):
            t = yf.Ticker(ticker_symbol)
            info = t.info
            if not info or not info.get("shortName"):
                return None

        # FCF logic
        cf = t.cashflow
        fcf_series = cf.loc["Free Cash Flow"] if "Free Cash Flow" in cf.index else None
        if fcf_series is None or fcf_series.empty:
            return None
        
        fcf0 = float(fcf_series.iloc[0])
        if fcf0 < 0:
            # Use mean of last 3 if current is negative
            fcf0 = float(fcf_series.iloc[0:3].mean())

        # Capital Structure
        e = info.get("marketCap") or 0
        d = info.get("totalDebt") or 0
        cash = info.get("totalCash") or 0
        beta = info.get("beta") or 1.0 # Default to market beta if missing
        shares = info.get("impliedSharesOutstanding") or info.get("sharesOutstanding") or 0

        # Profitability / Tax
        fin = t.financials
        interest_expense = abs(float(fin.loc["Interest Expense"].iloc[0])) if "Interest Expense" in fin.index else 0
        
        tax_provision = float(fin.loc["Tax Provision"].iloc[0]) if "Tax Provision" in fin.index else 0
        pretax_income = float(fin.loc["Pretax Income"].iloc[0]) if "Pretax Income" in fin.index else 0
        tax_rate = (tax_provision / pretax_income) if pretax_income > 0 else 0.21
        if tax_rate < 0 or tax_rate > 0.5: tax_rate = 0.21

        # Growth
        # Try growth estimates or fall back to quarterly growth
        g = info.get("earningsQuarterlyGrowth") or 0.05 # Default 5%
        if g > 0.30: g = 0.30 # Cap high growth at 30% for stability

        return {
            "ticker": ticker_symbol,
            "name": info.get("longName") or info.get("shortName"),
            "fcf0": fcf0,
            "e": e,
            "d": d,
            "cash": cash,
            "beta": beta,
            "shares": shares,
            "interest_expense": interest_expense,
            "tax_rate": tax_rate,
            "g": g,
            "current_price": info.get("currentPrice") or info.get("regularMarketPrice") or info.get("previousClose") or 0,
            "gross_margins": info.get("grossMargins") or 0,
            "profit_margins": info.get("profitMargins") or 0,
        }
    except Exception as e:
        print(f"Error fetching inputs for {ticker_symbol}: {e}")
        return None

def _calculate_intrinsic_value_detailed(inputs: dict, rf_rate: float, erp: float = 0.0438):
    """
    Perform 2-Stage FCF DCF using WACC.
    """
    if not inputs or not inputs["shares"]: return None

    # 1. WACC
    # Cost of Equity (Re)
    re = rf_rate + (inputs["beta"] * erp)
    
    # Cost of Debt (Rd)
    rd = (inputs["interest_expense"] / inputs["d"]) * (1 - inputs["tax_rate"]) if inputs["d"] > 0 else 0
    
    # WACC
    total_cap = inputs["e"] + inputs["d"]
    if total_cap > 0:
        wacc = ((inputs["e"] / total_cap) * re) + ((inputs["d"] / total_cap) * rd)
    else:
        wacc = re
    
    # 2. 2-Stage Projection
    g = inputs["g"]
    
    # Terminal Growth (g_terminal) <= Rf or 3%
    g_terminal = min(rf_rate, 0.03)
    
    # Growth Convergence fix: g < WACC - 0.5% for terminal
    if g_terminal >= wacc:
        g_terminal = wacc - 0.005

    # Stage 1 (5 years)
    pv_stage1 = 0
    fcf = inputs["fcf0"]
    for t in range(1, 6):
        fcf *= (1 + g)
        pv_stage1 += fcf / ((1 + wacc) ** t)
    
    # Stage 2 (Terminal Value)
    tv = (fcf * (1 + g_terminal)) / (wacc - g_terminal)
    pv_tv = tv / ((1 + wacc) ** 5)
    
    # 3. Value Conversion
    enterprise_value = pv_stage1 + pv_tv
    equity_value = enterprise_value + inputs["cash"] - inputs["d"]
    intrinsic_price = equity_value / inputs["shares"]
    
    mos = (1 - (inputs["current_price"] / intrinsic_price)) if intrinsic_price > 0 else -1
    
    return {
        "intrinsic_price": round(float(intrinsic_price), 2),
        "mos": round(float(mos), 4),
        "wacc": round(float(wacc), 4),
        "g": round(float(g), 4),
        "re": round(float(re), 4),
        "rd": round(float(rd), 4),
        "fcf0": round(float(inputs["fcf0"]), 2),
        "equity_value": round(float(equity_value), 2)
    }

def calculate_valuation_metrics(positions: list) -> list:
    """
    Calculate Intrinsic Value using FCF and WACC methodology.
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

    erp = 0.0438

    # 2. Identify unique tickers
    unique_tickers = set()
    for p in positions:
        ticker = p.get("ticker")
        if not ticker or ticker in YFINANCE_SKIP_TICKERS or ticker.startswith("UNKNOWN"):
            continue
        is_equity = p.get("type_display") in ["Stock", "Equity", "Equity / ETF", "ETF"]
        if not is_equity and p.get("type_display") not in ["Cash", "Fixed Income", "Mutual Fund"]:
            is_equity = True
        if is_equity:
            unique_tickers.add(ticker)

    results = []
    for ticker in unique_tickers:
        if ticker in _valuation_cache:
            results.append(_valuation_cache[ticker])
            continue
        
        is_fund = any(p.get("ticker") == ticker and p.get("type_display") == "ETF" for p in positions)
        
        if is_fund:
            # ETF Look-through Aggregate
            try:
                t = yf.Ticker(ticker)
                if not hasattr(t, "funds_data") or t.funds_data.top_holdings is None or t.funds_data.top_holdings.empty:
                    continue

                holdings = t.funds_data.top_holdings
                total_intrinsic_ratio = 0.0
                weight_covered = 0.0
                
                for underlying_ticker, row in holdings.iterrows():
                    weight = row.get("Holding Percent") or row.get("Weight") or 0.0
                    if weight <= 0: continue
                    
                    u_inputs = _fetch_valuation_inputs(str(underlying_ticker), rf_rate)
                    if u_inputs:
                        u_val = _calculate_intrinsic_value_detailed(u_inputs, rf_rate, erp)
                        if u_val:
                            # Use ratio of Intrinsic Value / Price to scale the ETF
                            ratio = u_val["intrinsic_price"] / u_inputs["current_price"] if u_inputs["current_price"] > 0 else 1.0
                            total_intrinsic_ratio += ratio * weight
                            weight_covered += weight
                
                if weight_covered == 0: continue
                
                avg_ratio = total_intrinsic_ratio / weight_covered
                current_price = t.info.get("navPrice") or t.info.get("regularMarketPrice") or t.info.get("previousClose") or 0
                intrinsic_price = current_price * avg_ratio
                mos = (1 - (current_price / intrinsic_price)) if intrinsic_price > 0 else -1

                val_data = {
                    "ticker": ticker,
                    "security_name": t.info.get("shortName") or ticker,
                    "current_price": round(float(current_price), 2),
                    "intrinsic_price": round(float(intrinsic_price), 2),
                    "mos": round(float(mos), 4),
                    "quality_score": -1,
                    "wacc": 0, "g": 0, "fcf0": 0,
                    "owner_earnings_ps": 0,
                    "portfolio_owner_earnings": 0,
                }
                _valuation_cache[ticker] = val_data
                results.append(val_data)
                continue
            except Exception: continue

        # --- Standard Equity Valuation ---
        inputs = _fetch_valuation_inputs(ticker, rf_rate)
        if not inputs: continue
        
        val = _calculate_intrinsic_value_detailed(inputs, rf_rate, erp)
        if not val: continue

        # Quality Score (Refined for new logic)
        score = 0
        roe = (inputs["fcf0"] / (inputs["e"] / inputs["current_price"] * inputs["shares"])) if inputs["shares"] > 0 and inputs["current_price"] > 0 else 0
        if roe > 0.15: score += 25
        if (inputs["d"] / (inputs["e"] + 1)) < 0.5: score += 25
        if inputs["gross_margins"] > 0.40: score += 25
        if inputs["profit_margins"] > 0.10: score += 25

        val_data = {
            "ticker": ticker,
            "security_name": inputs["name"],
            "current_price": round(float(inputs["current_price"]), 2),
            "intrinsic_price": val["intrinsic_price"],
            "mos": val["mos"],
            "quality_score": score,
            "wacc": val["wacc"],
            "g": val["g"],
            "fcf0": val["fcf0"],
            "owner_earnings_ps": val["fcf0"] / inputs["shares"] if inputs["shares"] > 0 else 0,
            "portfolio_owner_earnings": (sum(p.get("quantity") or 0 for p in positions if p.get("ticker") == ticker)) * (val["fcf0"] / inputs["shares"] if inputs["shares"] > 0 else 0),
            "roe": round(roe, 4),
            "debt_to_equity": round(inputs["d"] / inputs["e"], 4) if inputs["e"] > 0 else 0,
            "discount_rate": val["wacc"], # For sensitivity matrix compatibility
            "growth_rate": val["g"],
            "terminal_growth_rate": min(rf_rate, 0.03),
        }
        _valuation_cache[ticker] = val_data
        results.append(val_data)

    return sorted(results, key=lambda x: x["intrinsic_price"], reverse=True)


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
    """
    if df_raw.empty:
        return {"total_value": 0, "buckets": {}}

    buckets = {
        "Taxable": {"value": 0, "accounts": []},
        "Tax-Deferred": {"value": 0, "accounts": []},
        "Tax-Exempt (Roth)": {"value": 0, "accounts": []},
    }

    # First pass: map every account to its holdings
    acct_map = {}
    for _, row in df_raw.iterrows():
        acct_name = row["account_name"]
        if acct_name not in acct_map:
            acct_map[acct_name] = {"account_name": acct_name, "value": 0, "holdings": []}
        
        acct_map[acct_name]["value"] += row["value"]
        acct_map[acct_name]["holdings"].append(row.to_dict())

    # Second pass: group accounts into buckets
    total_val = 0
    for acct_name, acct_data in acct_map.items():
        bucket_name = _classify_account(acct_name)
        buckets[bucket_name]["value"] += acct_data["value"]
        buckets[bucket_name]["accounts"].append(acct_data)
        total_val += acct_data["value"]

    # Calculate weights
    for b in buckets.values():
        b["weight_pct"] = (b["value"] / total_val * 100) if total_val > 0 else 0

    return {
        "total_value": float(total_val),
        "buckets": buckets,
    }


# ---------------------------------------------------------------------------
# 10. CLI entry point
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
