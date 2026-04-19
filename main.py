"""
main.py — Munger FastAPI backend

Serves the portfolio dashboard at http://localhost:8000.
Run with: uvicorn main:app --reload
"""

import logging
import traceback
import pandas as pd
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, RedirectResponse, FileResponse
from fastapi.staticfiles import StaticFiles

from core.config import check_gitignore
from data.sources import load
from data.vanguard import download_voo_holdings
from data.normalization import deduplicate, normalize_asset_class
from data.market_data import enrich_with_market_data, get_fund_details
from metrics.portfolio import calculate_metrics, calculate_institutions, calculate_sector_allocation
from metrics.risk import calculate_risk_metrics, calculate_efficiency_metrics, save_risk_snapshot
from metrics.tax import calculate_tax_buckets
from metrics.valuation import calculate_valuation_metrics

# Initialize Logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Fail immediately if .gitignore is missing required security patterns
check_gitignore()

app = FastAPI(title="Munger", docs_url=None, redoc_url=None)
app.mount("/static", StaticFiles(directory="static"), name="static")

_cache: dict = {}
_current_source: str = None

@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    logger.error(f"Global error: {exc}")
    logger.error(traceback.format_exc())
    return JSONResponse(
        status_code=500,
        content={"message": "Internal Server Error", "detail": str(exc)},
    )

def _build_cache(source_path: str = None) -> None:
    global _current_source
    try:
        if source_path:
            _current_source = source_path
        
        logger.info(f"Building cache (source: {_current_source or 'default'})...")
        df_raw = load(override_path=_current_source)
        df = normalize_asset_class(deduplicate(df_raw))
        risk = calculate_risk_metrics(df)
        
        # Clear existing caches
        _cache.clear()
        
        _cache["summary"] = {
            **calculate_metrics(df),
            "institutions": calculate_institutions(df_raw),
            "concentration": [f for f in risk["true_exposure"] if f["flagged"]],
            "risk_threshold": 10.0,
            "active_portfolio": _current_source or "Default"
        }
        _cache["df_clean"] = df
        _cache["df_raw"] = df_raw
        _cache["risk"] = risk
        
        save_risk_snapshot(risk)
        logger.info("Cache built successfully.")
    except Exception as e:
        logger.error(f"Error building cache: {e}")
        logger.error(traceback.format_exc())
        _cache["summary"] = None

# Initial build
_build_cache()


@app.get("/", include_in_schema=False)
def root():
    return FileResponse("static/index.html", headers={"Cache-Control": "no-store"})


@app.get("/api/portfolios")
def list_portfolios():
    """List available CSV and JSON portfolios in the root directory."""
    import os
    files = [f for f in os.listdir(".") if f.endswith((".csv", ".json")) and not f.startswith(".")]
    # Add default if configured via env
    portfolios = [{"name": "Default (Env)", "path": None}]
    for f in sorted(files):
        portfolios.append({"name": f, "path": f})
    return {"portfolios": portfolios, "active": _current_source}


@app.get("/api/switch-portfolio")
def switch_portfolio(path: str = None):
    if path == "null" or path == "":
        path = None
    _build_cache(source_path=path)
    return _cache.get("summary") or {}


@app.get("/api/summary")
def summary():
    if not _cache.get("summary"):
        return JSONResponse(status_code=500, content={"message": "Cache not initialized. Check logs."})
    
    s = dict(_cache["summary"])
    if "risk" in _cache:
        s["risk"] = _cache["risk"]
        s["concentration"] = [f for f in _cache["risk"]["true_exposure"] if f["flagged"]]
    else:
        s["risk"] = None
        s["concentration"] = []
    return s


@app.get("/api/risk")
def risk():
    if "risk" not in _cache:
        r = calculate_risk_metrics(_cache["df_clean"])
        _cache["risk"] = r
        save_risk_snapshot(r)
    return _cache["risk"]


@app.get("/api/market")
def market():
    if "market" not in _cache:
        enriched = enrich_with_market_data(_cache["summary"]["positions"])
        sectors = calculate_sector_allocation(_cache["summary"]["positions"])
        _cache["market"] = {"positions": enriched, "sectors": sectors}
    return _cache["market"]


@app.get("/api/tax")
def tax():
    if "tax" not in _cache:
        _cache["tax"] = calculate_tax_buckets(normalize_asset_class(_cache["df_raw"]))
    return _cache["tax"]


@app.get("/api/efficiency")
def efficiency():
    if "efficiency" not in _cache:
        _cache["efficiency"] = calculate_efficiency_metrics(_cache["df_clean"])
    return _cache["efficiency"]


@app.get("/api/valuation")
def valuation():
    logger.info("API: valuation requested")
    if "valuation" not in _cache:
        _cache["valuation"] = calculate_valuation_metrics(_cache["summary"]["positions"])
    logger.info(f"API: valuation returning {len(_cache['valuation'])} items")
    return _cache["valuation"]


@app.get("/api/refresh")
def refresh():
    download_voo_holdings()
    _build_cache(source_path=_current_source)
    return _cache.get("summary") or {}


@app.get("/api/ticker/{symbol}")
def ticker_detail(symbol: str):
    """
    Returns data for an individual ticker, including:
    - Holdings of this ticker across all accounts
    - Market metrics (yfinance enriched)
    - Fund details for ETFs (holdings)
    """
    if "df_raw" not in _cache:
        return JSONResponse(status_code=500, content={"message": "Cache not initialized."})
    
    # 1. Holdings
    df_raw = _cache["df_raw"]
    df_ticker = df_raw[df_raw["ticker"] == symbol]
    
    if df_ticker.empty:
        # Fallback to deduplicated summary search if raw failed
        pos = []
        if "summary" in _cache:
            pos = [p for p in _cache["summary"]["positions"] if p.get("ticker") == symbol]
            
        if not pos:
            # External ticker (e.g. underlying holding like 'V', 'AAPL')
            # Fetch a quick name/type via yfinance
            try:
                import yfinance as yf
                info = yf.Ticker(symbol).info
                security_name = info.get("shortName", symbol)
                qtype = info.get("quoteType", "")
                type_display = "ETF" if qtype == "ETF" else "Mutual Fund" if qtype == "MUTUALFUND" else "Stock" if qtype == "EQUITY" else "Unknown"
            except Exception:
                security_name = symbol
                type_display = "Unknown"
        else:
            security_name = pos[0].get("security_name", symbol)
            type_display = pos[0].get("type_display", "Unknown")
    else:
        security_name = df_ticker.iloc[0]["security_name"] if not df_ticker.empty else symbol
        type_display = df_ticker.iloc[0].get("type_display", "Unknown") if not df_ticker.empty else "Unknown"

    holdings = []
    total_val = 0.0
    total_basis = 0.0
    total_qty = 0.0
    has_basis = False
    
    if not df_ticker.empty:
        for _, row in df_ticker.iterrows():
            val = row.get("value", 0.0)
            qty = row.get("quantity", 0.0)
            basis = row.get("cost_basis", None)
            
            total_val += val
            total_qty += qty
            if pd.notna(basis):
                total_basis += basis
                has_basis = True
                
            holdings.append({
                "account_name": row.get("account_name", "Unknown"),
                "institution_name": row.get("institution_name", "Unknown"),
                "quantity": qty,
                "value": val,
                "cost_basis": basis if pd.notna(basis) else None,
            })

    # 2. Enrich with market data for THIS ticker
    pos = {
        "ticker": symbol,
        "security_name": security_name,
        "type_display": type_display,
        "value": total_val,
        "quantity": total_qty,
        "cost_basis": total_basis if has_basis else None
    }
    
    # Enrich updates the pos dict directly but returns a list.
    enriched = enrich_with_market_data([pos])[0]
    
    # 3. Fund details (if ETF / Mutual Fund)
    fund_details = None
    if type_display in ["ETF", "Mutual Fund"]:
        fund_details = get_fund_details(symbol)
        
    # 4. Indirect holdings via ETFs/Funds in the portfolio
    indirect_holdings = []
    if "summary" in _cache:
        portfolio_positions = _cache["summary"]["positions"]
        for p in portfolio_positions:
            ptype = p.get("type_display", "")
            if ptype in ["ETF", "Mutual Fund"] and p.get("ticker"):
                p_ticker = p["ticker"]
                f_details = get_fund_details(p_ticker)
                if f_details and f_details.get("holdings"):
                    for h in f_details["holdings"]:
                        if h["ticker"] == symbol:
                            indirect_holdings.append({
                                "etf_ticker": p_ticker,
                                "etf_name": p.get("security_name", p_ticker),
                                "weight": h["weight"],
                                "implied_value": p.get("value", 0.0) * h["weight"]
                            })
                            break
                            
    indirect_holdings = sorted(indirect_holdings, key=lambda x: x["implied_value"], reverse=True)
        
    return {
        "ticker": symbol,
        "security_name": security_name,
        "type_display": type_display,
        "holdings": holdings,
        "indirect_holdings": indirect_holdings,
        "totals": {
            "value": total_val,
            "quantity": total_qty,
            "cost_basis": total_basis if has_basis else None
        },
        "market": enriched,
        "fund": fund_details
    }
