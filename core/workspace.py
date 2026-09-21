"""
workspace.py — Local-first workspace management.

A Munger workspace is a plain folder on disk that owns everything about a
portfolio: settings, immutable snapshots, and derived caches. It is the unit
of "local-first": copy the folder and you copy the portfolio. Point it at a
Dropbox / iCloud / git directory and you get sync and version history for free.

Layout:
    <workspace>/
        settings.json        # non-secret configuration (data source, thresholds)
        snapshots/           # immutable, timestamped portfolio snapshots (JSON)
        cache/
            market_data.db   # yfinance SQLite cache (derived, safe to delete)
            risk_history.db  # risk metric history (derived, safe to delete)

Resolution order:
    1. explicit path argument
    2. MUNGER_WORKSPACE environment variable
    3. ~/.munger/default
"""

import os
from pathlib import Path

ENV_VAR = "MUNGER_WORKSPACE"


def repo_root() -> Path:
    """Absolute path of the munger repository root."""
    return Path(__file__).resolve().parent.parent


def resolve_workspace(explicit: "str | Path | None" = None) -> Path:
    """Resolve the workspace path without creating anything."""
    if explicit:
        return Path(explicit).expanduser()
    env = os.environ.get(ENV_VAR)
    if env:
        return Path(env).expanduser()
    return Path.home() / ".munger" / "default"


def ensure_workspace(explicit: "str | Path | None" = None) -> Path:
    """Resolve the workspace path, creating it (and subdirs) if needed."""
    ws = resolve_workspace(explicit)
    snapshots_dir(ws).mkdir(parents=True, exist_ok=True)
    cache_dir(ws).mkdir(parents=True, exist_ok=True)
    return ws


def snapshots_dir(ws: "str | Path") -> Path:
    return Path(ws) / "snapshots"


def cache_dir(ws: "str | Path") -> Path:
    return Path(ws) / "cache"


def settings_path(ws: "str | Path") -> Path:
    return Path(ws) / "settings.json"


def is_inside_repo(ws: "str | Path") -> bool:
    """True if the workspace lives inside the git repo (affects git-leak checks)."""
    try:
        return Path(ws).resolve().is_relative_to(repo_root().resolve())
    except (OSError, RuntimeError):
        return False


def cache_path(name: str) -> Path:
    """Preferred location for a derived cache file (inside the workspace)."""
    return cache_dir(resolve_workspace()) / name


def resolve_cached_file(name: str) -> Path:
    """
    Locate an existing derived-cache file: the workspace copy wins, else the
    legacy CWD copy, else the workspace path (so writers create it there).
    """
    preferred = cache_path(name)
    legacy = Path(name)
    if preferred.exists() or not legacy.exists():
        return preferred
    return legacy
