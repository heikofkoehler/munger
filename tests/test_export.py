"""
tests/test_export.py — Unit and HTTP tests for CSV and PDF portfolio export.
"""

import csv
import io
import os
import tempfile
from pathlib import Path
from unittest.mock import patch

# --- hermetic setup before importing main ---
_WS = tempfile.mkdtemp(prefix="munger-test-export-ws-")
os.environ["MUNGER_WORKSPACE"] = _WS
for _var in ("MONARCH_JSON_PATH", "CSV_PATH", "SHEET_ID"):
    os.environ.pop(_var, None)

import pandas as pd
import pytest
from fastapi.testclient import TestClient

import main
from core.export import build_export_payload, generate_csv_report, generate_pdf_report
from data.normalization import deduplicate, normalize_asset_class
from metrics.portfolio import calculate_metrics, calculate_institutions
from metrics.risk import calculate_efficiency_metrics
from metrics.tax import calculate_tax_buckets

REPO = Path(__file__).resolve().parent.parent
DEMO_CSV = REPO / "demo_portfolio.csv"
NO_FUND_DETAILS = {"expense_ratio": 0.0003, "holdings": []}


@pytest.fixture(autouse=True)
def _mock_fund_details():
    with (
        patch("metrics.risk.get_fund_details", return_value=dict(NO_FUND_DETAILS)),
        patch("core.export.get_fund_details", return_value=dict(NO_FUND_DETAILS)),
    ):
        yield


@pytest.fixture()
def sample_df_raw():
    return pd.DataFrame([
        {
            "security_id": "sec_aapl",
            "ticker": "AAPL",
            "security_name": "Apple Inc.",
            "type_display": "Stock",
            "quantity": 100.0,
            "value": 25000.0,
            "cost_basis": 15000.0,
            "account_name": "Individual Brokerage",
            "institution_name": "Fidelity",
        },
        {
            "security_id": "sec_voo",
            "ticker": "VOO",
            "security_name": "Vanguard S&P 500 ETF",
            "type_display": "ETF",
            "quantity": 50.0,
            "value": 27500.0,
            "cost_basis": None,  # Missing cost basis must never be treated as 0
            "account_name": "Roth IRA",
            "institution_name": "Vanguard",
        },
        {
            "security_id": "sec_cash",
            "ticker": "SPAXX",
            "security_name": "Fidelity Government Money Market",
            "type_display": "Stock",  # Should normalize to Cash
            "quantity": 5000.0,
            "value": 5000.0,
            "cost_basis": 5000.0,
            "account_name": "Traditional IRA",
            "institution_name": "Fidelity",
        },
    ])


def test_build_export_payload_and_null_basis_handling(sample_df_raw):
    df_clean = normalize_asset_class(deduplicate(sample_df_raw))
    metrics = calculate_metrics(df_clean)
    summary = {
        **metrics,
        "institutions": calculate_institutions(sample_df_raw),
        "risk_threshold": 10.0,
        "active_portfolio": "UnitTest",
    }
    tax = calculate_tax_buckets(normalize_asset_class(sample_df_raw))
    eff = calculate_efficiency_metrics(df_clean)

    payload = build_export_payload(
        summary=summary,
        df_raw=sample_df_raw,
        df_clean=df_clean,
        risk={
            "true_exposure": [
                {"ticker": "AAPL", "security_name": "Apple Inc.", "value": 25000.0, "direct": 25000.0, "indirect": 0.0, "weight_pct": 43.48, "flagged": True},
                {"ticker": "VOO", "security_name": "Vanguard S&P 500 ETF", "value": 27500.0, "direct": 27500.0, "indirect": 0.0, "weight_pct": 47.83, "flagged": True},
            ],
            "diversification": {
                "effective_holdings": 2.4,
                "hhi": 4200.0,
                "top5_concentration": 100.0,
                "top10_concentration": 100.0,
                "portfolio_volatility": 14.5,
                "diversification_ratio": 1.25,
                "sharpe_ratio": 1.12,
                "annualized_return": 18.4,
            },
        },
        tax=tax,
        efficiency=eff,
        valuation=[
            {
                "ticker": "AAPL",
                "security_name": "Apple Inc.",
                "current_price": 250.0,
                "intrinsic_price": 210.0,
                "mos": -0.19,
                "quality_score": 100,
                "wacc": 0.085,
                "g": 0.09,
            }
        ],
    )

    assert payload["total_value"] == 57500.0
    assert payload["position_count"] == 3
    assert payload["raw_holding_count"] == 3

    pos_by_t = {p["ticker"]: p for p in payload["positions"]}
    # AAPL has cost_basis = 15000 -> gain = 10000 (+66.67%)
    assert pos_by_t["AAPL"]["cost_basis"] == 15000.0
    assert pos_by_t["AAPL"]["unrealized_gain"] == 10000.0
    assert pos_by_t["AAPL"]["unrealized_gain_pct"] == 66.67

    # VOO has null cost_basis -> must remain None, never 0 or +100% gain
    assert pos_by_t["VOO"]["cost_basis"] is None
    assert pos_by_t["VOO"]["unrealized_gain"] is None
    assert pos_by_t["VOO"]["unrealized_gain_pct"] is None

    # SPAXX overridden to Cash
    assert pos_by_t["SPAXX"]["type_display"] == "Cash"


def test_generate_csv_report_contains_all_sections(sample_df_raw):
    df_clean = normalize_asset_class(deduplicate(sample_df_raw))
    summary = {
        **calculate_metrics(df_clean),
        "institutions": calculate_institutions(sample_df_raw),
        "risk_threshold": 10.0,
        "active_portfolio": "UnitTest",
    }
    payload = build_export_payload(
        summary=summary,
        df_raw=sample_df_raw,
        df_clean=df_clean,
        tax=calculate_tax_buckets(normalize_asset_class(sample_df_raw)),
        efficiency=calculate_efficiency_metrics(df_clean),
    )

    csv_str = generate_csv_report(payload)
    assert "[1. EXECUTIVE SUMMARY]" in csv_str
    assert "[2. ASSET CLASS ALLOCATION]" in csv_str
    assert "[3. TAX BUCKET BREAKDOWN]" in csv_str
    assert "[4. BY INSTITUTION]" in csv_str
    assert "[6. FEE EFFICIENCY & WEALTH GAP PROJECTIONS]" in csv_str
    assert "[7. DEDUPLICATED POSITIONS WITH ANALYSIS]" in csv_str
    assert "[8. ACCOUNT-LEVEL HOLDINGS (PRE-DEDUPLICATION)]" in csv_str

    rows = list(csv.reader(io.StringIO(csv_str)))
    assert any("Apple Inc." in r for r in rows)
    assert any("Vanguard S&P 500 ETF" in r for r in rows)


def test_generate_pdf_report_valid_pdf_binary(sample_df_raw):
    df_clean = normalize_asset_class(deduplicate(sample_df_raw))
    summary = {
        **calculate_metrics(df_clean),
        "institutions": calculate_institutions(sample_df_raw),
        "risk_threshold": 10.0,
        "active_portfolio": "UnitTest",
    }
    payload = build_export_payload(
        summary=summary,
        df_raw=sample_df_raw,
        df_clean=df_clean,
        tax=calculate_tax_buckets(normalize_asset_class(sample_df_raw)),
        efficiency=calculate_efficiency_metrics(df_clean),
    )

    pdf_bytes = generate_pdf_report(payload)
    assert isinstance(pdf_bytes, bytes)
    assert pdf_bytes.startswith(b"%PDF-1.4")
    assert b"%%EOF" in pdf_bytes
    assert b"MUNGER PORTFOLIO INTELLIGENCE" in pdf_bytes
    assert b"Apple Inc." in pdf_bytes


def test_http_export_endpoints():
    main._build_cache(source_path=str(DEMO_CSV))
    client = TestClient(main.app, raise_server_exceptions=False)

    # CSV Endpoint
    r_csv = client.get("/api/export/csv")
    assert r_csv.status_code == 200
    assert "text/csv" in r_csv.headers["content-type"]
    assert "attachment; filename=\"munger_portfolio_analysis_" in r_csv.headers["content-disposition"]
    assert "[1. EXECUTIVE SUMMARY]" in r_csv.text
    assert "AAPL" in r_csv.text

    # PDF Endpoint
    r_pdf = client.get("/api/export/pdf")
    assert r_pdf.status_code == 200
    assert r_pdf.headers["content-type"] == "application/pdf"
    assert "attachment; filename=\"munger_portfolio_report_" in r_pdf.headers["content-disposition"]
    assert r_pdf.content.startswith(b"%PDF-1.4")
    assert b"%%EOF" in r_pdf.content
