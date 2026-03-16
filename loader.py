"""
loader.py — Munger portfolio data loader

Reads holdings from Google Sheets or local CSV, deduplicates by security_id,
normalizes asset classes, calculates portfolio metrics, and flags concentration risk.
"""

import os
import sys

from dotenv import load_dotenv

load_dotenv()


# ---------------------------------------------------------------------------
# 1. Startup .gitignore check
# ---------------------------------------------------------------------------

from core.config import check_gitignore


# ---------------------------------------------------------------------------
# 2. Data loading
# ---------------------------------------------------------------------------

from data.sources import load, load_from_csv, load_from_sheets, EXPECTED_COLUMNS


# ---------------------------------------------------------------------------
# 3. Normalization & Deduplication
# ---------------------------------------------------------------------------

from data.normalization import TICKER_ALIASES, TICKER_OVERRIDES, normalize_ticker, deduplicate


# ---------------------------------------------------------------------------
# 4. Asset class normalization
# ---------------------------------------------------------------------------

from data.normalization import CASH_TICKERS, FIXED_INCOME_TICKERS, MUTUAL_FUND_TICKERS, normalize_asset_class


# ---------------------------------------------------------------------------
# 5. Metrics calculation
# ---------------------------------------------------------------------------

from metrics.portfolio import calculate_metrics


# ---------------------------------------------------------------------------
# 6. Risk Reporting (Concentration & Cost Efficiency)
# ---------------------------------------------------------------------------

from data.market_data import _fund_cache
from metrics.risk import CONC_THRESHOLD, calculate_efficiency_metrics, calculate_risk_metrics, check_concentration, save_risk_snapshot


# ---------------------------------------------------------------------------
# 7. Institutions summary
# ---------------------------------------------------------------------------

from metrics.portfolio import calculate_institutions


# ---------------------------------------------------------------------------
# 8. Market data enrichment (yfinance)
# ---------------------------------------------------------------------------

from data.market_data import YFINANCE_SKIP_TICKERS, _market_cache, enrich_with_market_data
from metrics.portfolio import calculate_sector_allocation


# ---------------------------------------------------------------------------
# 9. Tax bucket calculation
# ---------------------------------------------------------------------------

from metrics.tax import calculate_tax_buckets


# ---------------------------------------------------------------------------
# 10. Buffett Valuation
# ---------------------------------------------------------------------------

from metrics.valuation import _valuation_cache, _fetch_valuation_inputs, _calculate_intrinsic_value_detailed, calculate_valuation_metrics


# ---------------------------------------------------------------------------
# 11. CLI entry point
# ---------------------------------------------------------------------------

def main():
    check_gitignore()

    df_raw = load()
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
