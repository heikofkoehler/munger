"""
main.py — Munger FastAPI backend

Serves the portfolio dashboard at http://localhost:8000.
Run with: uvicorn main:app --reload
"""

from fastapi import FastAPI
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles

from loader import (
    check_gitignore,
    load,
    deduplicate,
    normalize_asset_class,
    calculate_metrics,
    check_concentration,
    calculate_institutions,
)

# Fail immediately if .gitignore is missing required security patterns
check_gitignore()

app = FastAPI(title="Munger", docs_url=None, redoc_url=None)
app.mount("/static", StaticFiles(directory="static"), name="static")

_cache: dict = {}


def _build_cache() -> None:
    df_raw = load()
    df = normalize_asset_class(deduplicate(df_raw))
    _cache["summary"] = {
        **calculate_metrics(df),
        "concentration": check_concentration(df),
        "institutions": calculate_institutions(df_raw),
    }


_build_cache()


@app.get("/", include_in_schema=False)
def root():
    return RedirectResponse("/static/index.html")


@app.get("/api/summary")
def summary():
    return _cache["summary"]


@app.get("/api/refresh")
def refresh():
    _build_cache()
    return _cache["summary"]
