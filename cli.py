import argparse
import os
import sys

# Local-first bootstrap (before imports that read env at import time).
from core.workspace import ensure_workspace
from core.settings import load_settings, init_settings

WORKSPACE = ensure_workspace()
SETTINGS = load_settings(WORKSPACE)

os.environ.setdefault("CONC_THRESHOLD", str(SETTINGS.get("concentration_threshold", 10.0)))

from core.config import check_gitignore
from data.sources import load
from data.normalization import deduplicate, normalize_asset_class
from metrics.portfolio import calculate_metrics
from metrics.risk import check_concentration, CONC_THRESHOLD


def _print_snapshots():
    from core.snapshots import list_snapshots

    try:
        from tabulate import tabulate
        _tabulate = tabulate
    except ImportError:
        def _tabulate(rows, headers=(), tablefmt="simple", **_):
            lines = ["  ".join(str(h) for h in headers)]
            for row in rows:
                lines.append("  ".join(str(c) for c in row))
            return "\n".join(lines)

    snaps = list_snapshots(WORKSPACE)
    if not snaps:
        print("No snapshots yet. Run the app or refresh to create one.")
        return
    rows = [
        (
            s["name"],
            s["timestamp"],
            f"${s['total_value']:,.2f}" if s["total_value"] is not None else "?",
            s["source"],
            s["position_count"],
        )
        for s in snaps
    ]
    print(_tabulate(
        rows,
        headers=["Snapshot", "Timestamp", "Total", "Source", "Positions"],
        tablefmt="simple",
    ))


def main():
    parser = argparse.ArgumentParser(description="Munger portfolio CLI")
    parser.add_argument("--list-snapshots", action="store_true",
                        help="list workspace snapshots instead of the portfolio summary")
    parser.add_argument("--init", action="store_true",
                        help="create <workspace>/settings.json with defaults (never overwrites)")
    args = parser.parse_args()

    if args.init:
        path = init_settings(WORKSPACE)
        print(f"Settings: {path}")
        print(f"Workspace: {WORKSPACE}")
        return

    check_gitignore(WORKSPACE)

    if args.list_snapshots:
        _print_snapshots()
        return

    df_raw = load(settings=SETTINGS)
    df = deduplicate(df_raw)
    df = normalize_asset_class(df)
    metrics = calculate_metrics(df)
    concentration = check_concentration(df)

    try:
        from tabulate import tabulate
        _tabulate = tabulate
    except ImportError:
        def _tabulate(rows, headers=(), tablefmt="simple", **_):
            lines = ["  ".join(str(h) for h in headers)]
            for row in rows:
                lines.append("  ".join(str(c) for c in row))
            return "\n".join(lines)

    print(f"\nTotal Portfolio Value: ${metrics['total_value']:,.2f}\n")

    # Allocation summary
    alloc_rows = sorted(metrics["allocation"].items(), key=lambda x: x[1], reverse=True)
    print(_tabulate(
        [(k, f"{v:.2f}%") for k, v in alloc_rows],
        headers=["Asset Class", "Weight"],
        tablefmt="simple",
    ))
    print()

    # Positions table
    pos_rows = [
        (p["ticker"], p["security_name"][:40], f"${p['value']:,.2f}", f"{p['weight_pct']:.2f}%", p["type_display"])
        for p in metrics["positions"]
    ]
    print(_tabulate(
        pos_rows,
        headers=["Ticker", "Name", "Value", "Weight", "Type"],
        tablefmt="simple",
    ))
    print()

    # Concentration flags
    if concentration:
        print(f"CONCENTRATION RISK FLAGS (>{CONC_THRESHOLD}%):")
        for f in concentration:
            print(f"  {f['ticker']}: {f['weight_pct']:.2f}%")
    else:
        print(f"No concentration flags triggered (threshold: {CONC_THRESHOLD}%).")

if __name__ == "__main__":
    try:
        main()
    except (RuntimeError, ValueError, FileNotFoundError) as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)
