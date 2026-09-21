"""
snapshots.py — immutable, timestamped portfolio snapshots.

Every successful data refresh writes one JSON file into
<workspace>/snapshots/. Snapshot files are the source of truth for history:
human-readable, git-diffable, and trivially portable — copy the folder and
the history comes along.

A snapshot captures the *computed* portfolio state (positions, allocation,
institutions, total value), not secrets or raw credentials. The raw source
files (CSV / Monarch JSON) remain the source of truth for holdings data.
"""

import json
import math
import re
from datetime import datetime, timezone
from pathlib import Path

from core.workspace import snapshots_dir, ensure_workspace

SNAPSHOT_VERSION = 1

# Strict filename pattern: 20260921T120000.json or 20260921T120000-2.json.
# load_snapshot() rejects anything else to prevent path traversal.
_NAME_RE = re.compile(r"^[0-9]{8}T[0-9]{6}(?:-\d+)?\.json$")


def _json_safe(obj):
    """Recursively convert NaN/Infinity to None so json output stays valid."""
    if isinstance(obj, float) and (math.isnan(obj) or math.isinf(obj)):
        return None
    if isinstance(obj, dict):
        return {k: _json_safe(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_json_safe(v) for v in obj]
    return obj


def write_snapshot(workspace, *, source_label: str, summary: dict) -> Path:
    """
    Persist an immutable snapshot of the computed portfolio state.

    summary: dict with total_value, positions, allocation, institutions
             (the shape produced by metrics/portfolio.calculate_metrics plus
             the institutions list from main.py's cache builder).
    Returns the path of the written snapshot file.
    """
    directory = snapshots_dir(ensure_workspace(workspace))
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    name = f"{stamp}.json"
    counter = 2
    while (directory / name).exists():
        name = f"{stamp}-{counter}.json"
        counter += 1

    payload = _json_safe({
        "version": SNAPSHOT_VERSION,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "source": source_label,
        "total_value": summary.get("total_value"),
        "positions": summary.get("positions", []),
        "allocation": summary.get("allocation", {}),
        "institutions": summary.get("institutions", []),
    })
    path = directory / name
    path.write_text(json.dumps(payload, indent=2) + "\n")
    return path


def list_snapshots(workspace) -> "list[dict]":
    """
    List snapshot metadata, newest first. Each entry: name, timestamp,
    total_value, source, position_count. Skips files that fail to parse.
    """
    directory = snapshots_dir(ensure_workspace(workspace))
    entries = []
    for path in sorted(directory.glob("*.json"), reverse=True):
        if not _NAME_RE.match(path.name):
            continue
        try:
            data = json.loads(path.read_text())
        except (json.JSONDecodeError, OSError):
            continue
        entries.append({
            "name": path.name,
            "timestamp": data.get("timestamp"),
            "total_value": data.get("total_value"),
            "source": data.get("source"),
            "position_count": len(data.get("positions", [])),
        })
    return entries


def load_snapshot(workspace, name: str) -> dict:
    """Load a full snapshot by filename. Rejects names outside the pattern."""
    if not _NAME_RE.match(name):
        raise ValueError(f"Invalid snapshot name: {name!r}")
    path = snapshots_dir(ensure_workspace(workspace)) / name
    if not path.is_file():
        raise FileNotFoundError(f"Snapshot not found: {name}")
    return json.loads(path.read_text())
