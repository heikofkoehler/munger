import argparse
import os
import sys

from dotenv import load_dotenv
load_dotenv()

# Local-first bootstrap (before imports that read env at import time).
from core.workspace import ensure_workspace
from core.settings import load_settings, init_settings

WORKSPACE = ensure_workspace()
SETTINGS = load_settings(WORKSPACE)

os.environ.setdefault("CONC_THRESHOLD", str(SETTINGS.get("concentration_threshold", 10.0)))

from core.config import check_gitignore
from data.sources import load
from data.normalization import deduplicate, normalize_asset_class
from metrics.portfolio import calculate_metrics, calculate_institutions
from metrics.risk import check_concentration, calculate_risk_metrics, calculate_efficiency_metrics, CONC_THRESHOLD
from metrics.tax import calculate_tax_buckets
from core.export import build_export_payload, generate_csv_report, generate_pdf_report


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
    parser.add_argument("--export-csv", metavar="PATH",
                        help="export full portfolio and analysis report to CSV file")
    parser.add_argument("--export-pdf", metavar="PATH",
                        help="export full portfolio and analysis report to PDF file")
    args = parser.parse_args()

    if args.init:
        path = init_settings(WORKSPACE)
        print(f"Settings: {path}")
        print(f"Workspace: {WORKSPACE}")
        return

    check_gitignore()

    if args.list_snapshots:
        _print_snapshots()
        return

    df_raw = load(settings=SETTINGS)
    df = deduplicate(df_raw)
    df = normalize_asset_class(df)
    metrics = calculate_metrics(df)
    concentration = check_concentration(df)

    if args.export_csv or args.export_pdf:
        risk = calculate_risk_metrics(df)
        tax = calculate_tax_buckets(normalize_asset_class(df_raw))
        efficiency = calculate_efficiency_metrics(df)
        summary_data = {
            **metrics,
            "institutions": calculate_institutions(df_raw),
            "concentration": [f for f in risk["true_exposure"] if f["flagged"]],
            "risk_threshold": CONC_THRESHOLD,
            "active_portfolio": "CLI",
        }
        payload = build_export_payload(
            summary=summary_data,
            df_raw=df_raw,
            df_clean=df,
            risk=risk,
            tax=tax,
            efficiency=efficiency,
        )
        if args.export_csv:
            with open(args.export_csv, "w", encoding="utf-8") as f:
                f.write(generate_csv_report(payload))
            print(f"Exported CSV analysis report to: {args.export_csv}")
        if args.export_pdf:
            with open(args.export_pdf, "wb") as f:
                f.write(generate_pdf_report(payload))
            print(f"Exported PDF analysis report to: {args.export_pdf}")
        return

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
