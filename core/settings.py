"""
settings.py — settings.json management (the VS Code-style config file).

Non-secret configuration lives in <workspace>/settings.json. Secrets
(Monarch cookie, OAuth tokens) are NEVER written here; they stay in
environment variables / .env.

Precedence for data-source configuration: explicit function arguments >
settings.json > environment variables > defaults.
"""

import copy
import json
from pathlib import Path

from core.workspace import settings_path, ensure_workspace

DEFAULTS = {
    "data_source": {
        # "auto" tries monarch_json -> csv -> sheets, in that order.
        # Set to "monarch_json" | "csv" | "sheets" to pin a single source.
        "kind": "auto",
        "monarch_json_path": None,
        "csv_path": None,
        "sheet_id": None,
    },
    "concentration_threshold": 10.0,
    "snapshots": {
        "enabled": True,
    },
}


def _deep_merge(base: dict, override: dict) -> dict:
    merged = copy.deepcopy(base)
    for key, value in override.items():
        if (
            key in merged
            and isinstance(merged[key], dict)
            and isinstance(value, dict)
        ):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = copy.deepcopy(value)
    return merged


def load_settings(workspace=None) -> dict:
    """Load settings.json merged over defaults. Missing file -> defaults."""
    ws = ensure_workspace(workspace)
    settings = copy.deepcopy(DEFAULTS)
    path = settings_path(ws)
    if not path.exists():
        return settings
    try:
        with open(path) as f:
            user = json.load(f)
    except json.JSONDecodeError as e:
        raise ValueError(f"Invalid JSON in {path}: {e}")
    if not isinstance(user, dict):
        raise ValueError(f"Invalid settings in {path}: top-level value must be an object")
    return _deep_merge(settings, user)


def init_settings(workspace=None) -> Path:
    """
    Write settings.json with defaults if it does not exist yet.
    Never overwrites an existing file. Returns the settings path.
    """
    ws = ensure_workspace(workspace)
    path = settings_path(ws)
    if not path.exists():
        path.write_text(json.dumps(DEFAULTS, indent=2) + "\n")
    return path
