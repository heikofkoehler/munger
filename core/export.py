"""
core/export.py — Local-first CSV and PDF Portfolio Export with Analysis

Generates comprehensive, multi-section portfolio analysis reports in CSV and
PDF formats with zero external dependencies and 100% local execution.
"""

from __future__ import annotations

import csv
import io
from datetime import datetime
from typing import Any, Dict, List, Optional
import pandas as pd

from data.normalization import (
    normalize_ticker,
    CASH_TICKERS,
    FIXED_INCOME_TICKERS,
    MUTUAL_FUND_TICKERS,
)
from data.market_data import get_fund_details, _market_cache
from core.database import _yf_db_get
from metrics.tax import _classify_account, classify_dividend_treatment


# ---------------------------------------------------------------------------
# Formatting Helpers
# ---------------------------------------------------------------------------

def _fmt_dollar(val: Optional[float], decimals: int = 2) -> str:
    if val is None or (isinstance(val, float) and pd.isna(val)):
        return "—"
    v = float(val)
    sign = "-" if v < 0 else ""
    return f"{sign}${abs(v):,.{decimals}f}"


def _fmt_dollar_compact(val: Optional[float]) -> str:
    if val is None or (isinstance(val, float) and pd.isna(val)):
        return "—"
    v = float(val)
    sign = "-" if v < 0 else ""
    av = abs(v)
    if av >= 1_000_000_000_000:
        return f"{sign}${av / 1_000_000_000_000:.2f}T"
    if av >= 1_000_000_000:
        return f"{sign}${av / 1_000_000_000:.2f}B"
    if av >= 1_000_000:
        return f"{sign}${av / 1_000_000:.2f}M"
    if av >= 10_000:
        return f"{sign}${av / 1_000:.1f}K"
    return f"{sign}${av:,.2f}"


def _fmt_pct(val: Optional[float], decimals: int = 2) -> str:
    if val is None or (isinstance(val, float) and pd.isna(val)):
        return "—"
    return f"{float(val):.{decimals}f}%"


def _fmt_num(val: Optional[float], decimals: int = 2) -> str:
    if val is None or (isinstance(val, float) and pd.isna(val)):
        return "—"
    return f"{float(val):,.{decimals}f}"


def _clean_pdf_text(text: Any) -> str:
    """Sanitize string to WinAnsi / Latin-1 printable characters for PDF Type1 fonts."""
    if text is None:
        return "-"
    s = str(text)
    replacements = {
        "—": "-",
        "–": "-",
        "’": "'",
        "‘": "'",
        "“": '"',
        "”": '"',
        "…": "...",
        "•": "*",
        "✓": "Y",
        "✗": "N",
        "⚠": "!",
    }
    for k, v in replacements.items():
        s = s.replace(k, v)
    return s.encode("latin-1", errors="replace").decode("latin-1")


# ---------------------------------------------------------------------------
# Report Data Assembly
# ---------------------------------------------------------------------------

def build_export_payload(
    summary: Dict[str, Any],
    df_raw: pd.DataFrame,
    df_clean: pd.DataFrame,
    risk: Optional[Dict[str, Any]] = None,
    tax: Optional[Dict[str, Any]] = None,
    efficiency: Optional[Dict[str, Any]] = None,
    market: Optional[Dict[str, Any]] = None,
    valuation: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """
    Assemble a unified analytical dictionary from Munger's pipeline outputs.
    Respects the Cost Basis rule: never treats null/NaN cost_basis as 0.
    """
    timestamp_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    total_value = float(summary.get("total_value") or 0.0)
    positions = summary.get("positions") or []
    allocation = summary.get("allocation") or {}
    institutions = summary.get("institutions") or []
    risk_threshold = float(summary.get("risk_threshold") or 10.0)

    # 1. Per-ticker Cost Basis aggregation from df_raw (only where cost_basis is not null)
    basis_by_ticker: Dict[str, Dict[str, float]] = {}
    raw_holdings_rows: List[Dict[str, Any]] = []

    if df_raw is not None and not df_raw.empty:
        has_cb = "cost_basis" in df_raw.columns
        for _, row in df_raw.iterrows():
            val = float(pd.to_numeric(row.get("value", 0), errors="coerce") or 0.0)
            if val < 0.01:
                continue
            qty = float(pd.to_numeric(row.get("quantity", 0), errors="coerce") or 0.0)
            raw_t = str(row.get("ticker") or "").strip()
            if not raw_t or raw_t.lower() == "nan":
                raw_t = f"UNKNOWN_{row.get('security_id', '')}"
            t = normalize_ticker(raw_t, aggregate_classes=False)

            type_display = str(row.get("type_display") or "")
            if t in CASH_TICKERS:
                type_display = "Cash"
            elif t in FIXED_INCOME_TICKERS:
                type_display = "Fixed Income"
            elif t in MUTUAL_FUND_TICKERS:
                type_display = "Mutual Fund"

            cb_val: Optional[float] = None
            if has_cb:
                parsed_cb = pd.to_numeric(row.get("cost_basis"), errors="coerce")
                if not pd.isna(parsed_cb):
                    cb_val = float(parsed_cb)

            if t not in basis_by_ticker:
                basis_by_ticker[t] = {
                    "value_with_basis": 0.0,
                    "cost_basis": 0.0,
                    "has_basis": False,
                }
            if cb_val is not None:
                basis_by_ticker[t]["value_with_basis"] += val
                basis_by_ticker[t]["cost_basis"] += cb_val
                basis_by_ticker[t]["has_basis"] = True

            acct_name = str(row.get("account_name") or "Unknown Account")
            inst_name = str(row.get("institution_name") or "Unknown Institution")
            bucket = _classify_account(acct_name)
            gain_dollar = (val - cb_val) if cb_val is not None else None
            gain_pct = ((gain_dollar / cb_val) * 100.0) if (cb_val is not None and cb_val > 0) else None

            raw_holdings_rows.append({
                "account_name": acct_name,
                "institution_name": inst_name,
                "tax_bucket": bucket,
                "ticker": t,
                "security_name": str(row.get("security_name") or t),
                "type_display": type_display,
                "quantity": round(qty, 6),
                "value": round(val, 2),
                "cost_basis": round(cb_val, 2) if cb_val is not None else None,
                "unrealized_gain": round(gain_dollar, 2) if gain_dollar is not None else None,
                "unrealized_gain_pct": round(gain_pct, 2) if gain_pct is not None else None,
                "dividend_treatment": classify_dividend_treatment(t, type_display),
            })

    raw_holdings_rows.sort(key=lambda r: (r["tax_bucket"], -r["value"]))

    # 2. Index market data (from passed market dict, in-memory cache, or SQLite cache)
    market_by_ticker: Dict[str, Dict[str, Any]] = {}
    if market and isinstance(market.get("positions"), list):
        for mp in market["positions"]:
            if mp.get("ticker"):
                market_by_ticker[mp["ticker"]] = mp

    for p in positions:
        t = p.get("ticker")
        if t and t not in market_by_ticker:
            if t in _market_cache:
                market_by_ticker[t] = _market_cache[t]
            else:
                db_m = _yf_db_get(t, "market", allow_stale=True)
                if db_m:
                    market_by_ticker[t] = db_m

    # 3. Index valuation data (from passed valuation list or SQLite cache)
    val_by_ticker: Dict[str, Dict[str, Any]] = {}
    if valuation:
        for v in valuation:
            if v.get("ticker"):
                val_by_ticker[v["ticker"]] = v

    # 4. Index True Exposure & Risk Contributions
    true_exp_by_ticker: Dict[str, Dict[str, Any]] = {}
    risk_contrib_by_ticker: Dict[str, Dict[str, Any]] = {}
    diversification = (risk or {}).get("diversification") or {}
    if risk:
        for te in risk.get("true_exposure") or []:
            true_exp_by_ticker[te["ticker"]] = te
        for rc in diversification.get("risk_contributions") or []:
            risk_contrib_by_ticker[rc["ticker"]] = rc

    # 5. Enrich deduplicated positions
    enriched_positions: List[Dict[str, Any]] = []
    total_annual_dividends = 0.0

    for p in positions:
        t = p.get("ticker") or ""
        t_agg = normalize_ticker(t, aggregate_classes=True)
        val = float(p.get("value") or 0.0)
        qty = float(p.get("quantity") or 0.0)
        weight_pct = float(p.get("weight_pct") or 0.0)
        type_display = p.get("type_display") or "Unknown"

        # Cost Basis & Gain/Loss
        b_info = basis_by_ticker.get(t)
        cb_total = b_info["cost_basis"] if (b_info and b_info["has_basis"]) else None
        val_with_cb = b_info["value_with_basis"] if (b_info and b_info["has_basis"]) else None
        gain_dollar = (val_with_cb - cb_total) if (cb_total is not None and val_with_cb is not None) else None
        gain_pct = ((gain_dollar / cb_total) * 100.0) if (gain_dollar is not None and cb_total and cb_total > 0) else None

        # True exposure
        te = true_exp_by_ticker.get(t) or true_exp_by_ticker.get(t_agg) or {}
        true_weight_pct = te.get("weight_pct", weight_pct)
        flagged = bool(te.get("flagged", weight_pct > risk_threshold))

        # Market metrics
        m = market_by_ticker.get(t) or {}
        sector = m.get("sector")
        if not sector:
            if type_display == "Fixed Income":
                sector = "Fixed Income"
            elif type_display == "Cash":
                sector = "Cash"
            else:
                sector = "Other/Unknown"

        div_yield = m.get("dividend_yield")
        div_rate = m.get("dividend_rate")
        trailing_pe = m.get("trailing_pe")
        forward_pe = m.get("forward_pe")
        trailing_eps = m.get("trailing_eps")
        market_cap = m.get("market_cap")

        # Estimate annual dividend income
        ann_div_income: Optional[float] = None
        if div_yield is not None and not pd.isna(div_yield):
            ann_div_income = val * (float(div_yield) / 100.0)
            total_annual_dividends += ann_div_income
        elif div_rate is not None and not pd.isna(div_rate) and qty > 0:
            ann_div_income = qty * float(div_rate)
            total_annual_dividends += ann_div_income

        # Expense ratio & annual fee
        fund_details = get_fund_details(t) if t else {"expense_ratio": None}
        exp_ratio = fund_details.get("expense_ratio")
        exp_ratio_pct = (float(exp_ratio) * 100.0) if exp_ratio else 0.0
        annual_fee = (val * float(exp_ratio)) if exp_ratio else 0.0

        # Valuation metrics
        v_item = val_by_ticker.get(t) or {}
        intrinsic_price = v_item.get("intrinsic_price")
        current_price = v_item.get("current_price")
        mos_pct = (float(v_item["mos"]) * 100.0) if v_item.get("mos") is not None else None
        quality_score = v_item.get("quality_score")
        if quality_score == -1:
            quality_score = None

        # Risk contribution
        rc = risk_contrib_by_ticker.get(t) or risk_contrib_by_ticker.get(t.replace(".", "-")) or {}

        enriched_positions.append({
            "ticker": t,
            "security_name": p.get("security_name") or t,
            "type_display": type_display,
            "sector": sector,
            "quantity": round(qty, 6),
            "value": round(val, 2),
            "weight_pct": round(weight_pct, 2),
            "true_weight_pct": round(float(true_weight_pct), 2) if true_weight_pct is not None else round(weight_pct, 2),
            "flagged": flagged,
            "cost_basis": round(cb_total, 2) if cb_total is not None else None,
            "unrealized_gain": round(gain_dollar, 2) if gain_dollar is not None else None,
            "unrealized_gain_pct": round(gain_pct, 2) if gain_pct is not None else None,
            "dividend_yield_pct": round(float(div_yield), 2) if div_yield is not None and not pd.isna(div_yield) else None,
            "annual_dividend_income": round(ann_div_income, 2) if ann_div_income is not None else None,
            "trailing_pe": round(float(trailing_pe), 2) if trailing_pe is not None and not pd.isna(trailing_pe) else None,
            "forward_pe": round(float(forward_pe), 2) if forward_pe is not None and not pd.isna(forward_pe) else None,
            "trailing_eps": round(float(trailing_eps), 2) if trailing_eps is not None and not pd.isna(trailing_eps) else None,
            "market_cap": float(market_cap) if market_cap is not None and not pd.isna(market_cap) else None,
            "expense_ratio_pct": round(exp_ratio_pct, 4),
            "annual_fee": round(annual_fee, 2),
            "current_price": round(float(current_price), 2) if current_price else None,
            "intrinsic_price": round(float(intrinsic_price), 2) if intrinsic_price else None,
            "mos_pct": round(mos_pct, 1) if mos_pct is not None else None,
            "quality_score": quality_score,
            "risk_contrib_pct": rc.get("risk_contrib_pct"),
            "volatility_pct": rc.get("volatility_pct"),
            "risk_role": rc.get("role"),
        })

    return {
        "generated_at": timestamp_str,
        "active_portfolio": summary.get("active_portfolio") or "Default",
        "total_value": total_value,
        "position_count": len(enriched_positions),
        "raw_holding_count": len(raw_holdings_rows),
        "risk_threshold": risk_threshold,
        "total_annual_dividends": round(total_annual_dividends, 2),
        "portfolio_dividend_yield_pct": round((total_annual_dividends / total_value * 100.0), 2) if total_value > 0 else 0.0,
        "allocation": allocation,
        "institutions": institutions,
        "risk": risk or {},
        "diversification": diversification,
        "tax": tax or {},
        "efficiency": efficiency or {},
        "valuation": valuation or [],
        "positions": enriched_positions,
        "raw_holdings": raw_holdings_rows,
    }


# ---------------------------------------------------------------------------
# CSV Generator
# ---------------------------------------------------------------------------

def generate_csv_report(payload: Dict[str, Any]) -> str:
    """
    Generate a comprehensive multi-section CSV portfolio analysis report.
    """
    buf = io.StringIO()
    writer = csv.writer(buf)

    div = payload.get("diversification") or {}
    eff = payload.get("efficiency") or {}
    tax = payload.get("tax") or {}
    gains_summary = tax.get("gains_summary") or {}
    total_gains = gains_summary.get("total_informational") or gains_summary.get("total") or {}
    taxable_gains = gains_summary.get("taxable") or {}

    projections = eff.get("projections") or []
    proj_30 = next((p for p in projections if p.get("years") == 30), None)

    # Section 1: Executive Summary
    writer.writerow(["# MUNGER PORTFOLIO ANALYSIS REPORT"])
    writer.writerow(["Generated At", payload.get("generated_at")])
    writer.writerow(["Data Source", payload.get("active_portfolio")])
    writer.writerow([])

    writer.writerow(["[1. EXECUTIVE SUMMARY]"])
    writer.writerow(["Metric", "Value", "Notes"])
    writer.writerow(["Total Portfolio Value", f"{payload['total_value']:.2f}", _fmt_dollar(payload["total_value"])])
    writer.writerow(["Deduplicated Positions", payload["position_count"], f"{payload['raw_holding_count']} account-level holdings"])
    writer.writerow([
        "Total Tracked Cost Basis",
        f"{total_gains.get('cost_basis', 0.0):.2f}" if total_gains.get("cost_basis") else "",
        f"Covers {_fmt_dollar(total_gains.get('value_with_basis'))} of portfolio",
    ])
    writer.writerow([
        "Total Net Unrealized Gain/Loss",
        f"{total_gains.get('net_gain', 0.0):.2f}" if total_gains.get("value_with_basis") else "",
        _fmt_pct(total_gains.get("gain_pct")) if total_gains.get("value_with_basis") else "No cost basis",
    ])
    writer.writerow([
        "Taxable Net Unrealized Gain/Loss",
        f"{taxable_gains.get('net_gain', 0.0):.2f}" if taxable_gains.get("value_with_basis") else "",
        f" Gains: {_fmt_dollar(taxable_gains.get('unrealized_gain'))} / Losses: {_fmt_dollar(taxable_gains.get('unrealized_loss'))}",
    ])
    writer.writerow([
        "Estimated Annual Dividend Income",
        f"{payload.get('total_annual_dividends', 0.0):.2f}",
        f"Portfolio Yield: {_fmt_pct(payload.get('portfolio_dividend_yield_pct'))}",
    ])
    wer_pct = float(eff.get("weighted_expense_ratio") or 0.0) * 100.0
    writer.writerow([
        "Weighted Expense Ratio (WER)",
        f"{wer_pct:.4f}%",
        f"Annual Fee Bill: {_fmt_dollar(eff.get('total_annual_cost'))}",
    ])
    if proj_30:
        writer.writerow([
            "30-Year Fee Wealth Gap",
            f"{proj_30.get('wealth_gap', 0.0):.2f}",
            f"Projected Optimized: {_fmt_dollar(proj_30.get('optimized_val'))} vs Current: {_fmt_dollar(proj_30.get('current_val'))}",
        ])
    writer.writerow(["Effective Holdings (1/HHI)", div.get("effective_holdings", ""), f"HHI: {div.get('hhi', '')}"])
    writer.writerow(["Top 5 Concentration (%)", div.get("top5_concentration", ""), f"Top 10: {div.get('top10_concentration', '')}%"])
    if div.get("portfolio_volatility") is not None:
        writer.writerow(["Annualized Portfolio Volatility (%)", div.get("portfolio_volatility"), f"Diversification Ratio: {div.get('diversification_ratio')}x"])
        writer.writerow(["Sharpe Ratio (1Y)", div.get("sharpe_ratio"), f"1Y Annualized Return: {div.get('annualized_return')}%"])
        writer.writerow(["Avg Pairwise Correlation", div.get("avg_pairwise_correlation"), f"Volatility Reduction: {div.get('volatility_reduction_pct')}%"])
    writer.writerow([])

    # Section 2: Asset Allocation
    writer.writerow(["[2. ASSET CLASS ALLOCATION]"])
    writer.writerow(["Asset Class", "Value ($)", "Weight (%)"])
    total_val = payload["total_value"]
    for asset_class, weight in sorted(payload.get("allocation", {}).items(), key=lambda x: x[1], reverse=True):
        ac_val = (total_val * float(weight) / 100.0) if total_val else 0.0
        writer.writerow([asset_class, f"{ac_val:.2f}", f"{float(weight):.2f}"])
    writer.writerow([])

    # Section 3: Tax Bucket Breakdown
    writer.writerow(["[3. TAX BUCKET BREAKDOWN]"])
    writer.writerow(["Tax Bucket", "Value ($)", "Weight (%)", "Account Count"])
    for b_label, b_data in (tax.get("buckets") or {}).items():
        writer.writerow([
            b_label,
            f"{float(b_data.get('value') or 0.0):.2f}",
            f"{float(b_data.get('weight_pct') or 0.0):.2f}",
            len(b_data.get("accounts") or []),
        ])
    writer.writerow([])

    # Section 4: Institution Summary
    writer.writerow(["[4. BY INSTITUTION]"])
    writer.writerow(["Institution", "Value ($)", "Weight (%)"])
    for inst in payload.get("institutions") or []:
        writer.writerow([
            inst.get("institution_name"),
            f"{float(inst.get('value') or 0.0):.2f}",
            f"{float(inst.get('weight_pct') or 0.0):.2f}",
        ])
    writer.writerow([])

    # Section 5: Concentration & True Look-Through Exposure
    true_exp = (payload.get("risk") or {}).get("true_exposure") or []
    if true_exp:
        writer.writerow([f"[5. TRUE LOOK-THROUGH EXPOSURE & CONCENTRATION RISK (Threshold > {payload['risk_threshold']}%)]"])
        writer.writerow(["Ticker", "Security Name", "Total Exposure ($)", "Direct ($)", "Indirect via ETFs ($)", "True Weight (%)", "Concentration Flag"])
        for te in true_exp[:25]:
            writer.writerow([
                te.get("ticker"),
                te.get("security_name"),
                f"{float(te.get('value') or 0.0):.2f}",
                f"{float(te.get('direct') or 0.0):.2f}",
                f"{float(te.get('indirect') or 0.0):.2f}",
                f"{float(te.get('weight_pct') or 0.0):.2f}",
                "FLAGGED" if te.get("flagged") else "OK",
            ])
        writer.writerow([])

    # Section 6: Fee Efficiency & Wealth Gap Projections
    if projections:
        writer.writerow(["[6. FEE EFFICIENCY & WEALTH GAP PROJECTIONS]"])
        writer.writerow(["Horizon (Years)", "Current Trajectory ($)", "Zero-Fee Benchmark ($)", "Cumulative Wealth Gap ($)"])
        for proj in projections:
            writer.writerow([
                proj.get("years"),
                f"{float(proj.get('current_val') or 0.0):.2f}",
                f"{float(proj.get('optimized_val') or 0.0):.2f}",
                f"{float(proj.get('wealth_gap') or 0.0):.2f}",
            ])
        writer.writerow([])

    # Section 7: Deduplicated Positions with Full Analytical Enrichment
    writer.writerow(["[7. DEDUPLICATED POSITIONS WITH ANALYSIS]"])
    writer.writerow([
        "Ticker",
        "Security Name",
        "Asset Class",
        "Sector",
        "Quantity",
        "Market Value ($)",
        "Portfolio Weight (%)",
        "True Look-Through Weight (%)",
        "Concentration Flag",
        "Tracked Cost Basis ($)",
        "Unrealized Gain/Loss ($)",
        "Unrealized Gain/Loss (%)",
        "Dividend Yield (%)",
        "Est. Annual Dividend ($)",
        "Trailing P/E",
        "Forward P/E",
        "Trailing EPS ($)",
        "Expense Ratio (%)",
        "Annual Fee ($)",
        "Current Price ($)",
        "DCF Intrinsic Value ($)",
        "Margin of Safety (%)",
        "Quality Score",
        "Risk Contribution (%)",
        "Annualized Volatility (%)",
        "Risk Role",
    ])
    for p in payload.get("positions") or []:
        writer.writerow([
            p["ticker"],
            p["security_name"],
            p["type_display"],
            p["sector"],
            f"{p['quantity']:.6f}",
            f"{p['value']:.2f}",
            f"{p['weight_pct']:.2f}",
            f"{p['true_weight_pct']:.2f}",
            "FLAGGED" if p["flagged"] else "",
            f"{p['cost_basis']:.2f}" if p["cost_basis"] is not None else "",
            f"{p['unrealized_gain']:.2f}" if p["unrealized_gain"] is not None else "",
            f"{p['unrealized_gain_pct']:.2f}" if p["unrealized_gain_pct"] is not None else "",
            f"{p['dividend_yield_pct']:.2f}" if p["dividend_yield_pct"] is not None else "",
            f"{p['annual_dividend_income']:.2f}" if p["annual_dividend_income"] is not None else "",
            f"{p['trailing_pe']:.2f}" if p["trailing_pe"] is not None else "",
            f"{p['forward_pe']:.2f}" if p["forward_pe"] is not None else "",
            f"{p['trailing_eps']:.2f}" if p["trailing_eps"] is not None else "",
            f"{p['expense_ratio_pct']:.4f}" if p["expense_ratio_pct"] else "0.0000",
            f"{p['annual_fee']:.2f}" if p["annual_fee"] else "0.00",
            f"{p['current_price']:.2f}" if p["current_price"] is not None else "",
            f"{p['intrinsic_price']:.2f}" if p["intrinsic_price"] is not None else "",
            f"{p['mos_pct']:.1f}" if p["mos_pct"] is not None else "",
            p["quality_score"] if p["quality_score"] is not None else "",
            f"{p['risk_contrib_pct']:.2f}" if p["risk_contrib_pct"] is not None else "",
            f"{p['volatility_pct']:.1f}" if p["volatility_pct"] is not None else "",
            p["risk_role"] or "",
        ])
    writer.writerow([])

    # Section 8: Raw Account-Level Holdings
    writer.writerow(["[8. ACCOUNT-LEVEL HOLDINGS (PRE-DEDUPLICATION)]"])
    writer.writerow([
        "Account Name",
        "Institution",
        "Tax Bucket",
        "Ticker",
        "Security Name",
        "Asset Class",
        "Quantity",
        "Market Value ($)",
        "Cost Basis ($)",
        "Unrealized Gain/Loss ($)",
        "Unrealized Gain/Loss (%)",
        "Dividend Tax Treatment",
    ])
    for r in payload.get("raw_holdings") or []:
        writer.writerow([
            r["account_name"],
            r["institution_name"],
            r["tax_bucket"],
            r["ticker"],
            r["security_name"],
            r["type_display"],
            f"{r['quantity']:.6f}",
            f"{r['value']:.2f}",
            f"{r['cost_basis']:.2f}" if r["cost_basis"] is not None else "",
            f"{r['unrealized_gain']:.2f}" if r["unrealized_gain"] is not None else "",
            f"{r['unrealized_gain_pct']:.2f}" if r["unrealized_gain_pct"] is not None else "",
            r["dividend_treatment"],
        ])

    return buf.getvalue()


# ---------------------------------------------------------------------------
# Pure-Python PDF Report Engine (PDF 1.4 Type1 Helvetica)
# ---------------------------------------------------------------------------

class _PDFReportBuilder:
    """
    Lightweight, zero-dependency PDF 1.4 document builder using built-in Type1
    Helvetica fonts (F1 = Helvetica, F2 = Helvetica-Bold) with automatic
    pagination, vector tables, metric cards, and headers/footers.
    """

    PAGE_W = 612.0   # Letter width (8.5 in * 72 pt)
    PAGE_H = 792.0   # Letter height (11 in * 72 pt)
    MARGIN_L = 36.0
    MARGIN_R = 36.0
    MARGIN_T = 46.0
    MARGIN_B = 44.0

    def __init__(self, title: str, subtitle: str):
        self.title = _clean_pdf_text(title)
        self.subtitle = _clean_pdf_text(subtitle)
        self.pages: List[List[str]] = []
        self.y = 0.0
        self._new_page()

    @property
    def content_w(self) -> float:
        return self.PAGE_W - self.MARGIN_L - self.MARGIN_R

    @staticmethod
    def _esc(text: Any) -> str:
        s = _clean_pdf_text(text)
        return s.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")

    def _cmd(self, cmd_str: str) -> None:
        self.pages[-1].append(cmd_str)

    def _new_page(self) -> None:
        self.pages.append([])
        # Draw top banner on page
        is_first = len(self.pages) == 1
        if is_first:
            # Dark Navy Header Banner
            self._fill_rect(self.MARGIN_L, self.PAGE_H - 82, self.content_w, 50, (0.08, 0.10, 0.16))
            self._fill_rect(self.MARGIN_L, self.PAGE_H - 82, 4, 50, (0.42, 0.39, 1.0))  # Purple accent strip
            self._text(self.MARGIN_L + 14, self.PAGE_H - 54, "MUNGER PORTFOLIO INTELLIGENCE", size=14, bold=True, color=(1, 1, 1))
            self._text(self.MARGIN_L + 14, self.PAGE_H - 70, self.subtitle, size=8.5, bold=False, color=(0.72, 0.76, 0.85))
            self.y = self.PAGE_H - 98
        else:
            # Running header on subsequent pages
            self._text(self.MARGIN_L, self.PAGE_H - 32, "MUNGER PORTFOLIO ANALYSIS REPORT", size=8, bold=True, color=(0.40, 0.44, 0.54))
            self._text(self.PAGE_W - self.MARGIN_R, self.PAGE_H - 32, self.subtitle, size=8, bold=False, color=(0.50, 0.54, 0.62), align="right")
            self._line(self.MARGIN_L, self.PAGE_H - 37, self.PAGE_W - self.MARGIN_R, self.PAGE_H - 37, color=(0.82, 0.85, 0.90), width=0.6)
            self.y = self.PAGE_H - 52

    def ensure_space(self, needed_h: float) -> None:
        if self.y - needed_h < self.MARGIN_B:
            self._new_page()

    def _fill_rect(self, x: float, y: float, w: float, h: float, rgb: tuple) -> None:
        r, g, b = rgb
        self._cmd(f"q {r:.3f} {g:.3f} {b:.3f} rg {x:.2f} {y:.2f} {w:.2f} {h:.2f} re f Q")

    def _stroke_rect(self, x: float, y: float, w: float, h: float, rgb: tuple, line_w: float = 0.5) -> None:
        r, g, b = rgb
        self._cmd(f"q {line_w:.2f} w {r:.3f} {g:.3f} {b:.3f} RG {x:.2f} {y:.2f} {w:.2f} {h:.2f} re S Q")

    def _line(self, x1: float, y1: float, x2: float, y2: float, color: tuple = (0.85, 0.87, 0.91), width: float = 0.5) -> None:
        r, g, b = color
        self._cmd(f"q {width:.2f} w {r:.3f} {g:.3f} {b:.3f} RG {x1:.2f} {y1:.2f} m {x2:.2f} {y2:.2f} l S Q")

    def _text(
        self,
        x: float,
        y: float,
        text: Any,
        size: float = 9.0,
        bold: bool = False,
        color: tuple = (0.12, 0.14, 0.19),
        align: str = "left",
        max_chars: Optional[int] = None,
    ) -> None:
        s = _clean_pdf_text(text)
        if max_chars and len(s) > max_chars:
            s = s[: max_chars - 2] + ".."
        font = "F2" if bold else "F1"
        est_w = len(s) * size * (0.53 if bold else 0.50)
        draw_x = x
        if align == "right":
            draw_x = x - est_w
        elif align == "center":
            draw_x = x - (est_w / 2.0)
        r, g, b = color
        escaped = self._esc(s)
        self._cmd(f"BT /{font} {size:.2f} Tf {r:.3f} {g:.3f} {b:.3f} rg 1 0 0 1 {draw_x:.2f} {y:.2f} Tm ({escaped}) Tj ET")

    def add_section_header(self, title: str, subtitle: Optional[str] = None) -> None:
        needed = 34.0 if subtitle else 24.0
        self.ensure_space(needed + 20.0)
        self.y -= 6.0
        self._fill_rect(self.MARGIN_L, self.y - 16, self.content_w, 18, (0.94, 0.95, 0.98))
        self._fill_rect(self.MARGIN_L, self.y - 16, 3.0, 18, (0.42, 0.39, 1.0))
        self._text(self.MARGIN_L + 9, self.y - 11, title.upper(), size=9.0, bold=True, color=(0.15, 0.18, 0.28))
        self.y -= 20.0
        if subtitle:
            self._text(self.MARGIN_L + 2, self.y - 8, subtitle, size=7.8, bold=False, color=(0.42, 0.46, 0.55))
            self.y -= 13.0

    def add_kpi_cards(self, cards: List[Dict[str, str]]) -> None:
        """Draw a row of up to 4 KPI summary cards."""
        if not cards:
            return
        card_h = 44.0
        self.ensure_space(card_h + 10.0)
        n = len(cards)
        gap = 8.0
        card_w = (self.content_w - gap * (n - 1)) / n
        top_y = self.y
        for idx, c in enumerate(cards):
            cx = self.MARGIN_L + idx * (card_w + gap)
            cy = top_y - card_h
            self._fill_rect(cx, cy, card_w, card_h, (0.97, 0.98, 1.0))
            self._stroke_rect(cx, cy, card_w, card_h, (0.85, 0.88, 0.93), line_w=0.6)
            self._text(cx + 8, top_y - 12, c.get("label", "").upper(), size=6.8, bold=True, color=(0.42, 0.46, 0.56))
            self._text(cx + 8, top_y - 27, c.get("value", "—"), size=12.0, bold=True, color=(0.08, 0.11, 0.18))
            if c.get("sub"):
                self._text(cx + 8, top_y - 38, c.get("sub", ""), size=7.0, bold=False, color=(0.38, 0.43, 0.52), max_chars=34)
        self.y -= (card_h + 10.0)

    def add_table(
        self,
        columns: List[Dict[str, Any]],
        rows: List[List[Any]],
        row_h: float = 14.5,
        font_size: float = 7.6,
    ) -> None:
        """
        Render a multi-page table with repeating column headers.
        Each item in `columns`: {"header": str, "width": float, "align": "left"|"right"|"center", "max_chars": int}
        """
        if not rows:
            self.ensure_space(20)
            self._text(self.MARGIN_L + 4, self.y - 12, "No records available for this section.", size=8.0, color=(0.45, 0.48, 0.55))
            self.y -= 18
            return

        header_h = 16.0

        def _draw_header():
            self._fill_rect(self.MARGIN_L, self.y - header_h, self.content_w, header_h, (0.18, 0.21, 0.30))
            cx = self.MARGIN_L
            for col in columns:
                w = float(col["width"])
                align = col.get("align", "left")
                tx = cx + 4 if align == "left" else (cx + w - 4 if align == "right" else cx + w / 2.0)
                self._text(tx, self.y - 11.2, col["header"], size=font_size, bold=True, color=(1, 1, 1), align=align)
                cx += w
            self.y -= header_h

        self.ensure_space(header_h + row_h + 6.0)
        _draw_header()

        for r_idx, row in enumerate(rows):
            if self.y - row_h < self.MARGIN_B:
                self._new_page()
                _draw_header()

            bg = (0.975, 0.98, 0.992) if (r_idx % 2 == 1) else (1.0, 1.0, 1.0)
            self._fill_rect(self.MARGIN_L, self.y - row_h, self.content_w, row_h, bg)
            self._line(self.MARGIN_L, self.y - row_h, self.MARGIN_L + self.content_w, self.y - row_h, color=(0.90, 0.91, 0.94), width=0.4)

            cx = self.MARGIN_L
            for c_idx, col in enumerate(columns):
                w = float(col["width"])
                align = col.get("align", "left")
                val = row[c_idx] if c_idx < len(row) else ""
                bold_cell = bool(col.get("bold", False))
                color_cell = col.get("color", (0.12, 0.15, 0.20))

                # Allow tuple (text, color_tuple, bold_bool) for custom cell styling
                if isinstance(val, tuple):
                    cell_text = val[0]
                    if len(val) > 1 and val[1]:
                        color_cell = val[1]
                    if len(val) > 2:
                        bold_cell = bool(val[2])
                else:
                    cell_text = val

                tx = cx + 4 if align == "left" else (cx + w - 4 if align == "right" else cx + w / 2.0)
                self._text(
                    tx,
                    self.y - (row_h * 0.72),
                    cell_text,
                    size=font_size,
                    bold=bold_cell,
                    color=color_cell,
                    align=align,
                    max_chars=col.get("max_chars"),
                )
                cx += w

            self.y -= row_h

        self.y -= 8.0

    def build_bytes(self) -> bytes:
        """Serialize all pages and objects into a valid PDF 1.4 binary stream."""
        total_pages = len(self.pages)
        # Add footers to all pages
        for idx, page_cmds in enumerate(self.pages):
            pno = idx + 1
            footer_y = 22.0
            r, g, b = (0.50, 0.54, 0.62)
            left_txt = self._esc("Munger Local-First Portfolio Analysis — Confidential (100% On-Device Generated)")
            right_txt = self._esc(f"Page {pno} of {total_pages}")
            page_cmds.append(f"q 0.50 w 0.85 0.87 0.91 RG {self.MARGIN_L:.2f} 33.00 m {self.PAGE_W - self.MARGIN_R:.2f} 33.00 l S Q")
            page_cmds.append(f"BT /F1 7.50 Tf {r:.3f} {g:.3f} {b:.3f} rg 1 0 0 1 {self.MARGIN_L:.2f} {footer_y:.2f} Tm ({left_txt}) Tj ET")
            est_w = len(right_txt) * 7.5 * 0.50
            rx = self.PAGE_W - self.MARGIN_R - est_w
            page_cmds.append(f"BT /F1 7.50 Tf {r:.3f} {g:.3f} {b:.3f} rg 1 0 0 1 {rx:.2f} {footer_y:.2f} Tm ({right_txt}) Tj ET")

        # Object layout:
        # 1: Catalog
        # 2: Pages
        # 3: Font F1 (Helvetica)
        # 4: Font F2 (Helvetica-Bold)
        # 5 .. 5 + 2*N - 1: Page object + Content stream object pairs
        objects: List[bytes] = []

        def _add_obj(data: bytes) -> int:
            objects.append(data)
            return len(objects)

        _add_obj(b"<< /Type /Catalog /Pages 2 0 R >>")
        _add_obj(b"")  # Placeholder for Pages (obj 2)
        _add_obj(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>")
        _add_obj(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold /Encoding /WinAnsiEncoding >>")

        page_obj_ids: List[int] = []
        for page_cmds in self.pages:
            stream_bytes = ("\n".join(page_cmds)).encode("latin-1", errors="replace")
            content_obj = (
                f"<< /Length {len(stream_bytes)} >>\nstream\n".encode("ascii")
                + stream_bytes
                + b"\nendstream"
            )
            content_id = _add_obj(content_obj)
            page_obj = (
                f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {self.PAGE_W:.0f} {self.PAGE_H:.0f}] "
                f"/Resources << /Font << /F1 3 0 R /F2 4 0 R >> >> /Contents {content_id} 0 R >>"
            ).encode("ascii")
            page_id = _add_obj(page_obj)
            page_obj_ids.append(page_id)

        kids_str = " ".join(f"{pid} 0 R" for pid in page_obj_ids)
        objects[1] = f"<< /Type /Pages /Kids [{kids_str}] /Count {len(page_obj_ids)} >>".encode("ascii")

        out = io.BytesIO()
        out.write(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
        offsets: List[int] = []
        for idx, obj_bytes in enumerate(objects):
            obj_num = idx + 1
            offsets.append(out.tell())
            out.write(f"{obj_num} 0 obj\n".encode("ascii"))
            out.write(obj_bytes)
            out.write(b"\nendobj\n")

        xref_pos = out.tell()
        out.write(f"xref\n0 {len(objects) + 1}\n".encode("ascii"))
        out.write(b"0000000000 65535 f \n")
        for off in offsets:
            out.write(f"{off:010d} 00000 n \n".encode("ascii"))

        out.write(
            f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref_pos}\n%%EOF\n".encode("ascii")
        )
        return out.getvalue()


def generate_pdf_report(payload: Dict[str, Any]) -> bytes:
    """
    Generate a multi-page PDF portfolio analysis report from the assembled payload.
    """
    subtitle = f"Generated {payload.get('generated_at')}  |  Source: {payload.get('active_portfolio')}"
    pdf = _PDFReportBuilder("Munger Portfolio Intelligence", subtitle)

    total_val = float(payload.get("total_value") or 0.0)
    div = payload.get("diversification") or {}
    eff = payload.get("efficiency") or {}
    tax = payload.get("tax") or {}
    gains_summary = tax.get("gains_summary") or {}
    total_gains = gains_summary.get("total_informational") or gains_summary.get("total") or {}
    taxable_gains = gains_summary.get("taxable") or {}
    projections = eff.get("projections") or []
    proj_30 = next((p for p in projections if p.get("years") == 30), None)

    # ── 1. Executive Summary KPI Cards ─────────────────────────────
    wer_pct = float(eff.get("weighted_expense_ratio") or 0.0) * 100.0
    pdf.add_section_header("1. Executive Portfolio Summary")

    net_gain_val = total_gains.get("net_gain")
    has_basis = bool(total_gains.get("value_with_basis"))
    pdf.add_kpi_cards([
        {
            "label": "Total Net Worth",
            "value": _fmt_dollar(total_val),
            "sub": f"{payload['position_count']} positions / {payload['raw_holding_count']} holdings",
        },
        {
            "label": "Tracked Unrealized Gain",
            "value": _fmt_dollar(net_gain_val) if has_basis else "N/A",
            "sub": f"{_fmt_pct(total_gains.get('gain_pct'))} on {_fmt_dollar_compact(total_gains.get('cost_basis'))} basis" if has_basis else "No cost basis tracked",
        },
        {
            "label": "Est. Annual Dividend Income",
            "value": _fmt_dollar(payload.get("total_annual_dividends")),
            "sub": f"Portfolio Yield: {_fmt_pct(payload.get('portfolio_dividend_yield_pct'))}",
        },
        {
            "label": "Annual Fund Fee Bill",
            "value": _fmt_dollar(eff.get("total_annual_cost")),
            "sub": f"WER: {wer_pct:.3f}% | 30Y Gap: {_fmt_dollar_compact(proj_30.get('wealth_gap') if proj_30 else 0)}",
        },
    ])

    pdf.add_kpi_cards([
        {
            "label": "Effective Holdings (1/HHI)",
            "value": f"{div.get('effective_holdings', 0):.1f}",
            "sub": f"HHI: {div.get('hhi', 0):.0f} | Top 5: {_fmt_pct(div.get('top5_concentration'))}",
        },
        {
            "label": "Portfolio Volatility (1Y)",
            "value": _fmt_pct(div.get("portfolio_volatility")) if div.get("portfolio_volatility") is not None else "N/A",
            "sub": f"Div Ratio: {div.get('diversification_ratio')}x" if div.get("diversification_ratio") else "Historical series offline",
        },
        {
            "label": "Sharpe Ratio (Rf=4.5%)",
            "value": f"{div.get('sharpe_ratio'):.2f}" if div.get("sharpe_ratio") is not None else "N/A",
            "sub": f"1Y Ann. Return: {_fmt_pct(div.get('annualized_return'))}" if div.get("annualized_return") is not None else "—",
        },
        {
            "label": "Taxable Net Gain",
            "value": _fmt_dollar(taxable_gains.get("net_gain")) if taxable_gains.get("value_with_basis") else "$0.00",
            "sub": f"Gains {_fmt_dollar_compact(taxable_gains.get('unrealized_gain'))} / Loss {_fmt_dollar_compact(taxable_gains.get('unrealized_loss'))}",
        },
    ])

    # ── 2. Asset Class, Tax Bucket & Institution Tables ────────────
    pdf.add_section_header("2. Asset Allocation, Tax Buckets & Custodians")

    alloc_rows = []
    for ac, weight in sorted((payload.get("allocation") or {}).items(), key=lambda x: x[1], reverse=True):
        ac_val = (total_val * float(weight) / 100.0) if total_val else 0.0
        alloc_rows.append([ac, _fmt_dollar(ac_val), _fmt_pct(weight)])

    pdf.add_table(
        columns=[
            {"header": "Asset Class", "width": 260, "align": "left", "bold": True},
            {"header": "Market Value", "width": 160, "align": "right"},
            {"header": "Portfolio Weight", "width": 120, "align": "right"},
        ],
        rows=alloc_rows,
    )

    tax_rows = []
    for b_label, b_data in (tax.get("buckets") or {}).items():
        tax_rows.append([
            b_label,
            str(len(b_data.get("accounts") or [])),
            _fmt_dollar(b_data.get("value")),
            _fmt_pct(b_data.get("weight_pct")),
        ])
    if tax_rows:
        pdf.add_table(
            columns=[
                {"header": "Tax Bucket Classification", "width": 200, "align": "left", "bold": True},
                {"header": "Accounts", "width": 80, "align": "right"},
                {"header": "Market Value", "width": 140, "align": "right"},
                {"header": "Share of Portfolio", "width": 120, "align": "right"},
            ],
            rows=tax_rows,
        )

    inst_rows = [
        [inst.get("institution_name"), _fmt_dollar(inst.get("value")), _fmt_pct(inst.get("weight_pct"))]
        for inst in (payload.get("institutions") or [])
    ]
    if inst_rows:
        pdf.add_table(
            columns=[
                {"header": "Custodian / Institution", "width": 260, "align": "left", "bold": True},
                {"header": "Total Held Value", "width": 160, "align": "right"},
                {"header": "Portfolio Weight", "width": 120, "align": "right"},
            ],
            rows=inst_rows,
        )

    # ── 3. True Exposure & Concentration Analysis ──────────────────
    true_exp = (payload.get("risk") or {}).get("true_exposure") or []
    if true_exp:
        pdf.add_section_header(
            "3. True Look-Through Exposure & Concentration Risk",
            f"Aggregates direct holdings with underlying ETF/fund look-through weights (Threshold: {payload['risk_threshold']}%)",
        )
        te_rows = []
        for te in true_exp[:15]:
            flagged = te.get("flagged")
            status_cell = ("FLAGGED (>10%)", (0.78, 0.18, 0.18), True) if flagged else ("Within Limit", (0.12, 0.55, 0.38), False)
            te_rows.append([
                te.get("ticker"),
                te.get("security_name"),
                _fmt_dollar(te.get("direct")),
                _fmt_dollar(te.get("indirect")),
                _fmt_dollar(te.get("value")),
                _fmt_pct(te.get("weight_pct")),
                status_cell,
            ])
        pdf.add_table(
            columns=[
                {"header": "Ticker", "width": 55, "align": "left", "bold": True},
                {"header": "Security Name", "width": 145, "align": "left", "max_chars": 28},
                {"header": "Direct ($)", "width": 75, "align": "right"},
                {"header": "Indirect ($)", "width": 75, "align": "right"},
                {"header": "Total ($)", "width": 80, "align": "right", "bold": True},
                {"header": "True Wt%", "width": 50, "align": "right", "bold": True},
                {"header": "Status", "width": 60, "align": "right"},
            ],
            rows=te_rows,
        )

    # ── 4. Fee Efficiency & Wealth Gap Projections ─────────────────
    if projections:
        pdf.add_section_header(
            "4. Fee Efficiency & Compounding Wealth Gap",
            f"Asset-by-asset compounding at 7.0% nominal growth vs. 0.00% benchmark (Annual Fee Bill: {_fmt_dollar(eff.get('total_annual_cost'))})",
        )
        proj_rows = [
            [
                f"{p.get('years')} Years",
                _fmt_dollar(p.get("current_val")),
                _fmt_dollar(p.get("optimized_val")),
                (_fmt_dollar(p.get("wealth_gap")), (0.75, 0.22, 0.22), True),
            ]
            for p in projections
        ]
        pdf.add_table(
            columns=[
                {"header": "Projection Horizon", "width": 120, "align": "left", "bold": True},
                {"header": "Current Trajectory", "width": 140, "align": "right"},
                {"header": "Zero-Fee Benchmark", "width": 140, "align": "right"},
                {"header": "Cumulative Fee Drag", "width": 140, "align": "right"},
            ],
            rows=proj_rows,
        )

    # ── 5. Intrinsic Valuation (2-Stage FCF DCF) ───────────────────
    val_items = payload.get("valuation") or []
    if val_items:
        pdf.add_section_header(
            "5. Intrinsic Valuation Analysis (2-Stage FCF-WACC DCF)",
            "5-year Free Cash Flow projection discounted at WACC with equity adjustments (Cash - Debt)",
        )
        val_rows = []
        for v in val_items:
            mos = float(v.get("mos") or 0.0) * 100.0
            mos_color = (0.08, 0.55, 0.40) if mos > 0 else (0.75, 0.20, 0.20)
            q_score = v.get("quality_score")
            val_rows.append([
                v.get("ticker"),
                v.get("security_name"),
                _fmt_dollar(v.get("current_price")),
                _fmt_dollar(v.get("intrinsic_price")),
                (_fmt_pct(mos, 1), mos_color, True),
                str(q_score) if (q_score is not None and q_score != -1) else "—",
                _fmt_pct(float(v.get("wacc") or 0.0) * 100.0) if v.get("wacc") else "—",
                _fmt_pct(float(v.get("g") or 0.0) * 100.0, 1) if v.get("g") else "—",
            ])
        pdf.add_table(
            columns=[
                {"header": "Ticker", "width": 52, "align": "left", "bold": True},
                {"header": "Company Name", "width": 158, "align": "left", "max_chars": 30},
                {"header": "Price", "width": 60, "align": "right"},
                {"header": "Intrinsic", "width": 65, "align": "right", "bold": True},
                {"header": "Margin Safety", "width": 65, "align": "right"},
                {"header": "Quality", "width": 45, "align": "right"},
                {"header": "WACC", "width": 48, "align": "right"},
                {"header": "Growth", "width": 47, "align": "right"},
            ],
            rows=val_rows,
        )

    # ── 6. Deduplicated Positions Master Table ─────────────────────
    pdf.add_section_header(
        "6. Deduplicated Portfolio Positions (With Market & Gain Analysis)",
        "Merged across accounts by security; Gain/Loss computed strictly where cost basis is reported",
    )
    pos_rows = []
    for p in payload.get("positions") or []:
        ug = p.get("unrealized_gain")
        ug_pct = p.get("unrealized_gain_pct")
        if ug is not None:
            ug_color = (0.08, 0.55, 0.40) if ug >= 0 else (0.75, 0.20, 0.20)
            ug_str = f"{_fmt_dollar_compact(ug)} ({_fmt_pct(ug_pct, 1)})"
            ug_cell: Any = (ug_str, ug_color, False)
        else:
            ug_cell = "—"

        pos_rows.append([
            p["ticker"],
            p["security_name"],
            p["type_display"],
            _fmt_dollar(p["value"]),
            _fmt_pct(p["weight_pct"]),
            _fmt_dollar(p["cost_basis"]) if p["cost_basis"] is not None else "—",
            ug_cell,
            _fmt_pct(p["dividend_yield_pct"]) if p["dividend_yield_pct"] is not None else "—",
            f"{p['trailing_pe']:.1f}x" if p["trailing_pe"] is not None else "—",
        ])

    pdf.add_table(
        columns=[
            {"header": "Ticker", "width": 48, "align": "left", "bold": True},
            {"header": "Security Name", "width": 122, "align": "left", "max_chars": 24},
            {"header": "Class", "width": 56, "align": "left", "max_chars": 12},
            {"header": "Market Value", "width": 72, "align": "right", "bold": True},
            {"header": "Wt %", "width": 40, "align": "right"},
            {"header": "Cost Basis", "width": 68, "align": "right"},
            {"header": "Gain / Loss", "width": 74, "align": "right"},
            {"header": "Yield", "width": 32, "align": "right"},
            {"header": "P/E", "width": 28, "align": "right"},
        ],
        rows=pos_rows,
        font_size=7.2,
    )

    # ── 7. Account-Level Holdings Table ────────────────────────────
    raw_rows_data = payload.get("raw_holdings") or []
    if raw_rows_data:
        pdf.add_section_header(
            "7. Account-Level Holdings Breakdown",
            "Individual positions grouped by tax bucket and account prior to cross-account deduplication",
        )
        acct_table_rows = []
        for r in raw_rows_data:
            ug = r.get("unrealized_gain")
            ug_cell = (
                (_fmt_dollar(ug), (0.08, 0.55, 0.40) if ug >= 0 else (0.75, 0.20, 0.20), False)
                if ug is not None
                else "—"
            )
            acct_table_rows.append([
                r["account_name"],
                r["tax_bucket"].replace("Tax-Exempt (Roth)", "Roth").replace("Tax-Deferred", "Deferred"),
                r["ticker"],
                r["security_name"],
                _fmt_dollar(r["value"]),
                _fmt_dollar(r["cost_basis"]) if r["cost_basis"] is not None else "—",
                ug_cell,
            ])

        pdf.add_table(
            columns=[
                {"header": "Account", "width": 115, "align": "left", "max_chars": 22},
                {"header": "Bucket", "width": 48, "align": "left"},
                {"header": "Ticker", "width": 46, "align": "left", "bold": True},
                {"header": "Security Name", "width": 125, "align": "left", "max_chars": 24},
                {"header": "Value ($)", "width": 68, "align": "right", "bold": True},
                {"header": "Cost Basis", "width": 68, "align": "right"},
                {"header": "Gain / Loss", "width": 70, "align": "right"},
            ],
            rows=acct_table_rows,
            font_size=7.1,
        )

    return pdf.build_bytes()
