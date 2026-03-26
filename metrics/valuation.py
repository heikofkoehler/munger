import sys
import pandas as pd
import yfinance as yf
from core.database import _yf_db_get, _yf_db_set
from data.market_data import YFINANCE_SKIP_TICKERS

_valuation_cache: dict = {}

def _fetch_valuation_inputs(ticker_symbol: str, rf_rate: float):
    """
    Fetch raw financial data for WACC and FCF DCF calculation.
    """
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
                total_wacc = 0.0
                total_g = 0.0
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
                            total_wacc += u_val["wacc"] * weight
                            total_g += u_val["g"] * weight
                            weight_covered += weight
                
                if weight_covered == 0: continue
                
                avg_ratio = total_intrinsic_ratio / weight_covered
                avg_wacc = total_wacc / weight_covered
                avg_g = total_g / weight_covered

                current_price = t.info.get("navPrice") or t.info.get("regularMarketPrice") or t.info.get("previousClose") or 0
                intrinsic_price = current_price * avg_ratio
                mos = (1 - (current_price / intrinsic_price)) if intrinsic_price > 0 else -1

                # To make Sensitivity Analysis work for ETFs, we need to provide fcf0, cash, debt, and shares
                # that result in the same intrinsic price and respond to WACC/G changes similarly.
                # We'll use a simplified model: eqVal = PV_stage1 + PV_TV, with cash=0, debt=0, shares=1.
                # We need to find fcf0 such that DCF(fcf0, avg_wacc, avg_g) == intrinsic_price.
                
                # Simplified 2-stage DCF to back-calculate fcf0
                def back_calc_fcf0(target_price, wacc, g, tg):
                    if target_price <= 0: return 0
                    pv_factor = 0
                    fcf_step = 1.0
                    for t in range(1, 6):
                        fcf_step *= (1 + g)
                        pv_factor += fcf_step / ((1 + wacc) ** t)
                    
                    cur_tg = tg
                    if cur_tg >= wacc: cur_tg = wacc - 0.005
                    tv_factor = (fcf_step * (1 + cur_tg)) / (wacc - cur_tg)
                    pv_tv_factor = tv_factor / ((1 + wacc) ** 5)
                    
                    total_factor = pv_factor + pv_tv_factor
                    return target_price / total_factor if total_factor > 0 else 0

                tg = min(rf_rate, 0.03)
                fcf0_pseudo = back_calc_fcf0(intrinsic_price, avg_wacc, avg_g, tg)

                val_data = {
                    "ticker": ticker,
                    "security_name": t.info.get("shortName") or ticker,
                    "current_price": round(float(current_price), 2),
                    "intrinsic_price": round(float(intrinsic_price), 2),
                    "mos": round(float(mos), 4),
                    "quality_score": -1,
                    "wacc": round(float(avg_wacc), 4),
                    "g": round(float(avg_g), 4),
                    "fcf0": round(float(fcf0_pseudo), 2),
                    "cash": 0, "d": 0, "shares": 1,
                    "owner_earnings_ps": 0,
                    "portfolio_owner_earnings": 0,
                    "discount_rate": round(float(avg_wacc), 4),
                    "growth_rate": round(float(avg_g), 4),
                    "terminal_growth_rate": tg,
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
