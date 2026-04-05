import os
import sys
import sqlite3
from datetime import datetime
import pandas as pd
from data.market_data import get_fund_details
from data.normalization import normalize_ticker

CONC_THRESHOLD = float(os.environ.get("CONC_THRESHOLD", 10.0))

def save_risk_snapshot(risk_data: dict, db_path: str = "risk_history.db"):
    """
    Save a snapshot of risk metrics (WER and total cost) to a local SQLite database.
    """
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

def calculate_efficiency_metrics(df, growth_rate=0.07, benchmark_fee=0.0) -> dict:
    """
    Calculate detailed portfolio efficiency metrics, including wealth gap projections
    and high-cost asset benchmarking using asset-by-asset compounding.
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
    high_cost_assets = []
    
    # Store data for precise projection calculation
    assets_for_projection = []

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
        
        assets_for_projection.append({"value": val, "exp_ratio": exp_ratio})

    wer = total_annual_cost / total_value
    
    # 2. Wealth Gap Projections (5, 10, 15, 20, 25, 30 years) - Precise Asset-by-Asset
    projections = []
    for years in [5, 10, 15, 20, 25, 30]:
        current_fv = 0.0
        benchmark_fv = 0.0
        
        for asset in assets_for_projection:
            v = asset["value"]
            er = asset["exp_ratio"]
            
            # Scenario A: Current (r - individual asset fee)
            current_fv += v * ((1 + (growth_rate - er)) ** years)
            
            # Scenario B: Idealized Benchmark (r - benchmark_fee)
            benchmark_fv += v * ((1 + (growth_rate - benchmark_fee)) ** years)
            
        wealth_gap = benchmark_fv - current_fv
        
        projections.append({
            "years": years,
            "current_val": round(float(current_fv), 2),
            "optimized_val": round(float(benchmark_fv), 2),
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
