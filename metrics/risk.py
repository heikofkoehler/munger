import os
import sys
import sqlite3
from datetime import datetime
import pandas as pd
import numpy as np
from core.database import _yf_db_get, _yf_db_set
from data.market_data import get_fund_details
from data.normalization import normalize_ticker, CASH_TICKERS

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

    div_metrics = calculate_diversification_metrics(df)

    return {
        "true_exposure": true_exposure,
        "wer": round(float(wer), 6),
        "total_annual_cost": round(float(total_annual_cost), 2),
        "threshold": CONC_THRESHOLD,
        "diversification": div_metrics,
    }

def calculate_diversification_metrics(df: pd.DataFrame, period: str = "1y") -> dict:
    """
    Calculate portfolio diversification and risk-adjusted efficiency metrics
    using historical return data from yfinance.

    Returns:
        portfolio_volatility: annualized portfolio volatility (%)
        weighted_asset_volatility: weighted average individual asset volatility (%)
        volatility_reduction_pct: % reduction in volatility from non-correlation
        diversification_ratio: weighted_vol / portfolio_vol (e.g. 1.55x)
        avg_pairwise_correlation: weighted average pairwise correlation
        annualized_return: 1-year historical annualized return (%)
        sharpe_ratio: excess return / portfolio vol (Rf = 4.5%)
        effective_holdings: 1 / sum(w^2) (inverse HHI)
        top5_concentration: % of portfolio in top 5 assets
        top10_concentration: % of portfolio in top 10 assets
        hhi: Herfindahl-Hirschman Index
        risk_contributions: list of per-asset risk contribution metrics
    """
    default_res = {
        "portfolio_volatility": None,
        "weighted_asset_volatility": None,
        "volatility_reduction_pct": None,
        "diversification_ratio": None,
        "avg_pairwise_correlation": None,
        "annualized_return": None,
        "sharpe_ratio": None,
        "effective_holdings": 0.0,
        "top5_concentration": 0.0,
        "top10_concentration": 0.0,
        "hhi": 0.0,
        "risk_contributions": [],
    }

    if df is None or df.empty or "value" not in df.columns:
        return default_res

    # Aggregate positions by normalized ticker
    pos_map = {}
    names_map = {}
    cash_tickers = set(CASH_TICKERS)
    for _, row in df.iterrows():
        val = float(pd.to_numeric(row.get("value", 0), errors="coerce") or 0)
        if val < 0.01:
            continue
        raw_t = str(row.get("ticker") or "").strip()
        if not raw_t or raw_t.lower() == "nan":
            raw_t = f"UNKNOWN_{row.get('security_id', '')}"
        t = normalize_ticker(raw_t).replace(".", "-")
        pos_map[t] = pos_map.get(t, 0.0) + val
        if t not in names_map:
            names_map[t] = str(row.get("security_name") or t)
        if str(row.get("type_display", "")).strip() == "Cash":
            cash_tickers.add(t)

    total_val = sum(pos_map.values())
    if total_val <= 0 or not pos_map:
        return default_res

    weights = {t: val / total_val for t, val in pos_map.items()}
    sorted_weights = sorted(weights.values(), reverse=True)

    # 1. Breadth Metrics (always computable without external APIs)
    sum_sq_w = sum(w**2 for w in sorted_weights)
    effective_holdings = round(1.0 / sum_sq_w, 1) if sum_sq_w > 0 else 0.0
    hhi = round(sum((w * 100.0)**2 for w in sorted_weights), 1)
    top5_conc = round(sum(sorted_weights[:5]) * 100.0, 2)
    top10_conc = round(sum(sorted_weights[:10]) * 100.0, 2)

    top_items = sorted(pos_map.items(), key=lambda x: x[1], reverse=True)[:10]
    cache_key = "PORTFOLIO_DIV_" + "_".join(f"{t}:{round(v/total_val, 3)}" for t, v in top_items)

    cached = _yf_db_get(cache_key, "diversification")
    if cached:
        return cached

    # 2. Historical Daily Return Analysis via yfinance
    try:
        import yfinance as yf

        # Identify traded tickers (exclude pure cash sweeps)
        traded_tickers = [
            t for t in pos_map
            if t not in cash_tickers
            and not t.startswith("UNKNOWN")
            and not (t.startswith("Q") and len(t) == 5)
            and not t.startswith("CUR")
            and "USD" not in t
        ]

        if not traded_tickers:
            raise ValueError("No traded tickers available")

        # Download historical prices
        data = yf.download(traded_tickers, period=period, interval="1d", progress=False)
        if data is None or data.empty:
            raise ValueError("Empty response from yfinance")

        if "Close" in data:
            prices = data["Close"]
        elif "Adj Close" in data:
            prices = data["Adj Close"]
        else:
            raise ValueError("No Close or Adj Close price series found")

        if isinstance(prices, pd.Series):
            prices = prices.to_frame(name=traded_tickers[0])

        # Drop any failed columns
        prices = prices.dropna(axis=1, how="all").ffill().dropna()
        if len(prices) < 15:
            raise ValueError("Insufficient trading day history (<15 days)")

        returns = prices.pct_change().dropna()
        valid_tickers = [t for t in traded_tickers if t in returns.columns and not returns[t].isna().all()]
        if not valid_tickers:
            raise ValueError("No valid returns found")

        combined_returns = returns[valid_tickers].copy()
        N_days = len(combined_returns)

        # Handle cash bucket: 0 volatility and 0 correlation with equities
        cash_val = sum(pos_map[t] for t in pos_map if t not in valid_tickers)
        if cash_val > 0.01:
            combined_returns["CASH"] = 0.0
            all_model_tickers = valid_tickers + ["CASH"]
            model_weights = np.array([pos_map[t] / total_val for t in valid_tickers] + [cash_val / total_val])
        else:
            all_model_tickers = valid_tickers
            model_weights = np.array([pos_map[t] / total_val for t in valid_tickers])

        # Normalize weights
        model_weights = model_weights / model_weights.sum()

        cov_daily = combined_returns.cov().values
        corr_matrix = combined_returns.corr().values
        cov_annual = cov_daily * 252.0

        # Portfolio Volatility
        port_var = float(model_weights.T @ cov_annual @ model_weights)
        port_vol = float(np.sqrt(max(0.0, port_var)))

        # Asset Volatilities
        asset_vols = np.sqrt(np.maximum(0.0, np.diag(cov_annual)))
        weighted_asset_vol = float(np.sum(model_weights * asset_vols))

        # Diversification Ratio & Reduction %
        dr = round(weighted_asset_vol / port_vol, 2) if port_vol > 0.0001 else 1.0
        vol_reduction = round((1.0 - port_vol / weighted_asset_vol) * 100.0, 1) if weighted_asset_vol > 0.0001 else 0.0

        # Weighted Average Pairwise Correlation
        M = len(all_model_tickers)
        weighted_corr_sum = 0.0
        norm_denom = 0.0
        for i in range(M):
            for j in range(M):
                if i != j:
                    w_prod = model_weights[i] * model_weights[j]
                    c_val = corr_matrix[i, j]
                    weighted_corr_sum += w_prod * (c_val if not np.isnan(c_val) else 0.0)
                    norm_denom += w_prod
        avg_corr = round(float(weighted_corr_sum / norm_denom), 2) if norm_denom > 0 else 0.0

        # Marginal Contribution to Risk
        mcr = (model_weights * (cov_annual @ model_weights)) / port_var if port_var > 0 else np.zeros_like(model_weights)

        # 1Y Annualized Portfolio Return & Sharpe
        cum_returns = (1.0 + combined_returns).prod() ** (252.0 / N_days) - 1.0
        port_ann_return = float(np.sum(model_weights * cum_returns.values))
        rf = 0.045
        sharpe = round((port_ann_return - rf) / port_vol, 2) if port_vol > 0.0001 else 0.0

        # Risk Contributions Breakdown
        risk_contributions = []
        for i, t in enumerate(all_model_tickers):
            w_pct = round(float(model_weights[i] * 100.0), 2)
            r_pct = round(float(mcr[i] * 100.0), 2)
            v_pct = round(float(asset_vols[i] * 100.0), 1)

            if t == "CASH":
                name = "Cash & Stable Reserves"
                role = "Risk Anchor"
            else:
                name = names_map.get(t, t)
                if r_pct > w_pct * 1.25:
                    role = "Risk Driver"
                elif r_pct < w_pct * 0.50:
                    role = "Risk Anchor"
                else:
                    role = "Balanced"

            risk_contributions.append({
                "ticker": t,
                "security_name": name,
                "weight_pct": w_pct,
                "risk_contrib_pct": r_pct,
                "volatility_pct": v_pct,
                "role": role,
            })

        risk_contributions.sort(key=lambda x: x["risk_contrib_pct"], reverse=True)

        result = {
            "portfolio_volatility": round(port_vol * 100.0, 2),
            "weighted_asset_volatility": round(weighted_asset_vol * 100.0, 2),
            "volatility_reduction_pct": vol_reduction,
            "diversification_ratio": dr,
            "avg_pairwise_correlation": avg_corr,
            "annualized_return": round(port_ann_return * 100.0, 2),
            "sharpe_ratio": sharpe,
            "effective_holdings": effective_holdings,
            "top5_concentration": top5_conc,
            "top10_concentration": top10_conc,
            "hhi": hhi,
            "risk_contributions": risk_contributions,
        }

        _yf_db_set(cache_key, "diversification", result)
        return result

    except Exception as e:
        print(f"calculate_diversification_metrics fetch error: {e}", file=sys.stderr)
        # Fallback to stale cache if available
        stale = _yf_db_get(cache_key, "diversification", allow_stale=True)
        if stale:
            return stale

        # Fallback with breadth metrics if offline
        return {
            "portfolio_volatility": None,
            "weighted_asset_volatility": None,
            "volatility_reduction_pct": None,
            "diversification_ratio": None,
            "avg_pairwise_correlation": None,
            "annualized_return": None,
            "sharpe_ratio": None,
            "effective_holdings": effective_holdings,
            "top5_concentration": top5_conc,
            "top10_concentration": top10_conc,
            "hhi": hhi,
            "risk_contributions": [],
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
