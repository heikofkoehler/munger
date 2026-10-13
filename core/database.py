import sys
import sqlite3
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

YF_CACHE_DB = "market_data.db"
_YF_TTL = {"market": 24, "valuation": 720, "diversification": 24}  # hours per data type (24h for market/div, 30 days for financial statements)


def _db_path() -> str:
    """
    Resolve the SQLite cache location: <workspace>/cache/market_data.db.

    Falls back to the legacy ./market_data.db if it exists and the workspace
    cache does not yet (so existing caches keep working after the upgrade).
    """
    from core.workspace import cache_dir, resolve_workspace

    ws = resolve_workspace()
    preferred = cache_dir(ws) / YF_CACHE_DB
    legacy = Path(YF_CACHE_DB)
    if not preferred.exists() and legacy.exists():
        print(
            f"note: using legacy cache at ./{YF_CACHE_DB}; "
            f"delete it to migrate to the workspace cache at {preferred}",
            file=sys.stderr,
        )
        return str(legacy)
    return str(preferred)


def _parse_ts(value: str) -> datetime:
    """Parse an ISO timestamp; assume UTC for legacy naive values."""
    dt = datetime.fromisoformat(value)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _yf_db_get(ticker: str, data_type: str, allow_stale: bool = False) -> Optional[dict]:
    """Return cached yfinance data if present and within TTL (or if allow_stale=True), else None."""
    try:
        conn = sqlite3.connect(_db_path())
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
        if not allow_stale:
            cutoff = _now() - timedelta(hours=_YF_TTL.get(data_type, 24))
            if _parse_ts(row[1]) < cutoff:
                return None
        return json.loads(row[0])
    except Exception:
        return None

def _yf_db_set(ticker: str, data_type: str, data: dict) -> None:
    """Persist yfinance data to SQLite cache."""
    try:
        path = _db_path()
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(path)
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
            (ticker, data_type, json.dumps(data), _now().isoformat())
        )
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"yf_cache write error for {ticker}/{data_type}: {e}", file=sys.stderr)
