"""
main.py — Munger FastAPI backend

Serves the portfolio dashboard at http://localhost:8000.
Run with: uvicorn main:app --reload
"""

import logging
import traceback
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, RedirectResponse, FileResponse
from fastapi.staticfiles import StaticFiles

from core.config import check_gitignore
from data.sources import load
from data.normalization import deduplicate, normalize_asset_class
from data.market_data import enrich_with_market_data
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

@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    logger.error(f"Global error: {exc}")
    logger.error(traceback.format_exc())
    return JSONResponse(
        status_code=500,
        content={"message": "Internal Server Error", "detail": str(exc)},
    )

def _build_cache() -> None:
    try:
        logger.info("Building cache...")
        df_raw = load()
        df = normalize_asset_class(deduplicate(df_raw))
        risk = calculate_risk_metrics(df)
        
        _cache["summary"] = {
            **calculate_metrics(df),
            "institutions": calculate_institutions(df_raw),
            "concentration": [f for f in risk["true_exposure"] if f["flagged"]],
            "risk_threshold": 10.0,
        }
        _cache["df_clean"] = df
        _cache["df_raw"] = df_raw
        _cache["risk"] = risk
        
        save_risk_snapshot(risk)
        
        # Clear other lazy caches
        _cache.pop("market", None)
        _cache.pop("tax", None)
        _cache.pop("efficiency", None)
        _cache.pop("valuation", None)
        logger.info("Cache built successfully.")
    except Exception as e:
        logger.error(f"Error building cache: {e}")
        logger.error(traceback.format_exc())
        # We don't raise here to allow the server to start even if data loading fails,
        # but endpoints will return 500 with details.
        _cache["summary"] = None

# Initial build
_build_cache()


@app.get("/", include_in_schema=False)
def root():
    return FileResponse("static/index.html", headers={"Cache-Control": "no-store"})


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
    _build_cache()
    return _cache.get("summary") or {}
