"""
main.py — Munger FastAPI backend

Serves the portfolio dashboard at http://localhost:8000.
Run with: uvicorn main:app --reload
"""

from fastapi import FastAPI
from fastapi.responses import RedirectResponse, FileResponse
from fastapi.staticfiles import StaticFiles

from loader import (
    check_gitignore,
    load,
    deduplicate,
    normalize_asset_class,
    calculate_metrics,
    calculate_risk_metrics,
    calculate_efficiency_metrics,
    calculate_sector_allocation,
    save_risk_snapshot,
    calculate_institutions,
    enrich_with_market_data,
    calculate_tax_buckets,
)

# Fail immediately if .gitignore is missing required security patterns
check_gitignore()

app = FastAPI(title="Munger", docs_url=None, redoc_url=None)
app.mount("/static", StaticFiles(directory="static"), name="static")

_cache: dict = {}


def _build_cache() -> None:
    df_raw = load()
    df = normalize_asset_class(deduplicate(df_raw))
    risk = calculate_risk_metrics(df)
    
    _cache["summary"] = {
        **calculate_metrics(df),
        "institutions": calculate_institutions(df_raw),
        "concentration": [f for f in risk["true_exposure"] if f["flagged"]],
        "risk_threshold": risk.get("threshold", 10.0),
    }
    _cache["df_clean"] = df
    _cache["df_raw"] = df_raw
    _cache["risk"] = risk
    
    # Persistent snapshot for historical trend analysis
    save_risk_snapshot(risk)
    
    # Clear other lazy caches
    _cache.pop("market", None)
    _cache.pop("tax", None)
    _cache.pop("efficiency", None)


_build_cache()


@app.get("/", include_in_schema=False)
def root():
    return FileResponse("static/index.html", headers={"Cache-Control": "no-store"})


@app.get("/api/summary")
def summary():
    # Return basic summary; frontend can request risk/market later
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


@app.get("/api/refresh")
def refresh():
    _build_cache()
    return _cache["summary"]
