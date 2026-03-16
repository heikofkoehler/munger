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

from core.config import check_gitignore


# ---------------------------------------------------------------------------
# 2. Data loading
# ---------------------------------------------------------------------------

from data.sources import load, load_from_csv, load_from_sheets, EXPECTED_COLUMNS


# ---------------------------------------------------------------------------
# 3. Normalization & Deduplication
# ---------------------------------------------------------------------------

from data.normalization import TICKER_ALIASES, TICKER_OVERRIDES, normalize_ticker, deduplicate


# ---------------------------------------------------------------------------
# 4. Asset class normalization
# ---------------------------------------------------------------------------

from data.normalization import CASH_TICKERS, FIXED_INCOME_TICKERS, MUTUAL_FUND_TICKERS, normalize_asset_class


# ---------------------------------------------------------------------------
# 5. Metrics calculation
# ---------------------------------------------------------------------------

from metrics.portfolio import calculate_metrics


# ---------------------------------------------------------------------------
# 6. Risk Reporting (Concentration & Cost Efficiency)
# ---------------------------------------------------------------------------

CONC_THRESHOLD = float(os.environ.get("CONC_THRESHOLD", 10.0))
from data.market_data import get_fund_details


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

from metrics.portfolio import calculate_institutions


# ---------------------------------------------------------------------------
# 8. Market data enrichment (yfinance)
# ---------------------------------------------------------------------------

from data.market_data import YFINANCE_SKIP_TICKERS, _market_cache, enrich_with_market_data
from metrics.portfolio import calculate_sector_allocation


# ---------------------------------------------------------------------------
# 9. Tax bucket calculation
# ---------------------------------------------------------------------------

from metrics.tax import calculate_tax_buckets


# ---------------------------------------------------------------------------
# 10. Buffett Valuation
# ---------------------------------------------------------------------------

_valuation_cache: dict = {}


def _fetch_valuation_inputs(ticker_symbol: str, rf_rate: float):
    """
    Fetch raw financial data for WACC and FCF DCF calculation.
    """
    import yfinance as yf
    import pandas as pd

    cached = _yf_db_get(ticker_symbol, "valuation")
    if cached:
        return cached

    try:
        t = yf.Ticker(ticker_symbol.replace("-", "."))
        info = t.info
        if not info or not info.get("shortName"):
            t = yf.Ticker(ticker_symbol)
            info = t.info
            if not info or not info.get("shortName"):
                return None

        # FCF-WACC model is not applicable to financials (banks, insurers):
        # their loan issuance is recorded as cash outflow, making FCF meaningless.
        if info.get("sector") in ("Financial Services", "Financials"):
            return None

        # FCF logic
        cf = t.cashflow
        fcf_series = cf.loc["Free Cash Flow"] if "Free Cash Flow" in cf.index else None
        if fcf_series is None or fcf_series.empty:
            return None

        fcf0 = float(fcf_series.iloc[0])
        if fcf0 < 0 and len(fcf_series) >= 3:
            fcf0 = float(fcf_series.iloc[0:3].mean())
        if fcf0 <= 0:
            return None  # Negative FCF produces meaningless DCF; skip

        # Capital Structure
        e = info.get("marketCap") or 0
        d = info.get("totalDebt") or 0
        cash = info.get("totalCash") or 0
        beta = info.get("beta") or 1.0 # Default to market beta if missing
        shares = info.get("impliedSharesOutstanding") or info.get("sharesOutstanding") or 0

        # Profitability / Tax
        fin = t.financials
        interest_expense = 0
        if "Interest Expense" in fin.index and not pd.isna(fin.loc["Interest Expense"].iloc[0]):
            interest_expense = abs(float(fin.loc["Interest Expense"].iloc[0]))
            
        tax_rate = 0.21
        if "Tax Provision" in fin.index and "Pretax Income" in fin.index:
            try:
                tax_provision = float(fin.loc["Tax Provision"].iloc[0])
                pretax_income = float(fin.loc["Pretax Income"].iloc[0])
                if pretax_income > 0:
                    calculated_rate = tax_provision / pretax_income
                    if 0 <= calculated_rate <= 0.5:
                        tax_rate = calculated_rate
            except Exception:
                pass

        # Growth: average quarterly YoY and annual earnings growth if both available
        g_quarterly = info.get("earningsQuarterlyGrowth")
        g_annual = info.get("earningsGrowth")
        valid = [x for x in [g_quarterly, g_annual] if x is not None and not pd.isna(x)]
        g = sum(valid) / len(valid) if valid else 0.05
        if g > 0.20: g = 0.20
        if g < 0: g = 0.0

        result = {
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
        _yf_db_set(ticker_symbol, "valuation", result)
        return result
    except Exception as e:
        import traceback
        traceback.print_exc()
        print(f"Error fetching inputs for {ticker_symbol}: {e}")
        return None

def _calculate_intrinsic_value_detailed(inputs: dict, rf_rate: float, erp: float = 0.0438):
    """
    Perform 2-Stage FCF DCF using WACC.
    """
    if not inputs or not inputs.get("shares") or inputs["shares"] == 0: return None

    # 1. WACC
    re = rf_rate + (inputs["beta"] * erp)
    rd = (inputs["interest_expense"] / inputs["d"]) * (1 - inputs["tax_rate"]) if inputs["d"] > 0 else 0
    
    total_cap = inputs["e"] + inputs["d"]
    wacc = ((inputs["e"] / total_cap) * re) + ((inputs["d"] / total_cap) * rd) if total_cap > 0 else re
    
    # 2. 2-Stage Projection
    g = inputs["g"]
    g_terminal = min(rf_rate, 0.03)
    if g_terminal >= wacc: g_terminal = wacc - 0.005

    pv_stage1 = 0
    fcf = inputs["fcf0"]
    for t in range(1, 6):
        fcf *= (1 + g)
        pv_stage1 += fcf / ((1 + wacc) ** t)
    
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

    try:
        tnx = yf.Ticker("^TNX")
        rf_rate_raw = tnx.info.get("regularMarketPrice") or tnx.info.get("previousClose")
        rf_rate = float(rf_rate_raw) / 100 if rf_rate_raw else 0.04
    except Exception:
        rf_rate = 0.04

    erp = 0.0438

    unique_tickers = set()
    for p in positions:
        ticker = p.get("ticker")
        if not ticker or not isinstance(ticker, str) or ticker.lower() == "nan" or ticker in YFINANCE_SKIP_TICKERS or ticker.startswith("UNKNOWN"):
            continue
        is_equity = p.get("type_display") in ["Stock", "Equity", "Equity / ETF", "ETF"]
        if not is_equity and p.get("type_display") not in ["Cash", "Fixed Income", "Mutual Fund"]:
            is_equity = True
        if is_equity:
            unique_tickers.add(ticker)

    print(f"Valuation: analyzing {len(unique_tickers)} tickers: {unique_tickers}", flush=True)

    results = []
    for ticker in unique_tickers:
        if ticker in _valuation_cache:
            results.append(_valuation_cache[ticker])
            continue
        
        is_fund = any(p.get("ticker") == ticker and p.get("type_display") == "ETF" for p in positions)
        
        if is_fund:
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
                        if u_val and u_inputs["current_price"] > 0:
                            ratio = u_val["intrinsic_price"] / u_inputs["current_price"]
                            total_intrinsic_ratio += ratio * weight
                            weight_covered += weight
                
                if weight_covered == 0: continue
                
                avg_ratio = total_intrinsic_ratio / weight_covered
                current_price = t.info.get("navPrice") or t.info.get("regularMarketPrice") or t.info.get("previousClose") or 0
                intrinsic_price = current_price * avg_ratio
                mos = (1 - (current_price / intrinsic_price)) if intrinsic_price > 0 else -1

                pos_match = [p for p in positions if p.get("ticker") == ticker]
                total_qty = sum(p.get("quantity") or 0 for p in pos_match)

                val_data = {
                    "ticker": ticker,
                    "security_name": t.info.get("shortName") or ticker,
                    "current_price": round(float(current_price), 2),
                    "intrinsic_price": round(float(intrinsic_price), 2),
                    "mos": round(float(mos), 4),
                    "quality_score": -1,
                    "wacc": 0, "g": 0, "fcf0": 0, "cash": 0, "d": 0, "shares": 0,
                    "owner_earnings_ps": 0,
                    "portfolio_owner_earnings": 0,
                }
                _valuation_cache[ticker] = val_data
                results.append(val_data)
                continue
            except Exception: continue

        inputs = _fetch_valuation_inputs(ticker, rf_rate)
        if not inputs: continue
        
        val = _calculate_intrinsic_value_detailed(inputs, rf_rate, erp)
        if not val: continue

        score = 0
        roe = (inputs["fcf0"] / (inputs["e"] / inputs["current_price"] * inputs["shares"])) if inputs["shares"] > 0 and inputs["current_price"] > 0 else 0
        if roe > 0.15: score += 25
        if inputs["e"] > 0 and (inputs["d"] / inputs["e"]) < 0.5: score += 25
        if inputs.get("gross_margins", 0) > 0.40: score += 25
        if inputs.get("profit_margins", 0) > 0.10: score += 25

        pos_match = [p for p in positions if p.get("ticker") == ticker]
        total_qty = sum(p.get("quantity") or 0 for p in pos_match)

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
            "cash": round(float(inputs["cash"]), 2),
            "d": round(float(inputs["d"]), 2),
            "shares": inputs["shares"],
            "owner_earnings_ps": round(float(val["fcf0"] / inputs["shares"] if inputs["shares"] > 0 else 0), 2),
            "portfolio_owner_earnings": round(float(total_qty * (val["fcf0"] / inputs["shares"] if inputs["shares"] > 0 else 0)), 2),
            "roe": round(float(roe), 4),
            "debt_to_equity": round(float(inputs["d"] / inputs["e"]) if inputs["e"] > 0 else 0, 4),
            "gross_margin": round(float(inputs.get("gross_margins", 0)), 4),
            "net_margin": round(float(inputs.get("profit_margins", 0)), 4),
            "discount_rate": val["wacc"],
            "growth_rate": val["g"],
            "terminal_growth_rate": min(rf_rate, 0.03),
        }
        _valuation_cache[ticker] = val_data
        results.append(val_data)

    return sorted(results, key=lambda x: x["intrinsic_price"], reverse=True)


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
