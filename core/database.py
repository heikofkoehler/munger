import sys
import sqlite3
import json
from datetime import datetime, timedelta
from typing import Optional

YF_CACHE_DB = "market_data.db"
_YF_TTL = {"market": 24, "valuation": 6}  # hours per data type

def _yf_db_get(ticker: str, data_type: str) -> Optional[dict]:
    """Return cached yfinance data if present and within TTL, else None."""
    try:
        conn = sqlite3.connect(YF_CACHE_DB)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS yf_cache (
                ticker TEXT NOT NULL,
                data_type TEXT NOT NULL,
                data TEXT NOT NULL,
                fetched_at TEXT NOT NULL,
                PRIMARY KEY (ticker, data_type)
            )
        """)
        row = conn.execute(
            "SELECT data, fetched_at FROM yf_cache WHERE ticker=? AND data_type=?",
            (ticker, data_type)
        ).fetchone()
        conn.close()
        if not row:
            return None
        cutoff = datetime.utcnow() - timedelta(hours=_YF_TTL[data_type])
        if datetime.fromisoformat(row[1]) < cutoff:
            return None
        return json.loads(row[0])
    except Exception:
        return None

def _yf_db_set(ticker: str, data_type: str, data: dict) -> None:
    """Persist yfinance data to SQLite cache."""
    try:
        conn = sqlite3.connect(YF_CACHE_DB)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS yf_cache (
                ticker TEXT NOT NULL,
                data_type TEXT NOT NULL,
                data TEXT NOT NULL,
                fetched_at TEXT NOT NULL,
                PRIMARY KEY (ticker, data_type)
            )
        """)
        conn.execute(
            "INSERT OR REPLACE INTO yf_cache (ticker, data_type, data, fetched_at) VALUES (?, ?, ?, ?)",
            (ticker, data_type, json.dumps(data), datetime.utcnow().isoformat())
        )
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"yf_cache write error for {ticker}/{data_type}: {e}", file=sys.stderr)
