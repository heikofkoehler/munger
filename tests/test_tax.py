import pytest
import pandas as pd
from metrics.tax import calculate_tax_buckets, _classify_account, classify_dividend_treatment

def test_classify_dividend_treatment():
    # Common stocks and equity funds -> Qualified
    assert classify_dividend_treatment("AAPL", "Stock", "Technology") == "Qualified"
    assert classify_dividend_treatment("GOOG", "Stock", "Communication Services") == "Qualified"
    assert classify_dividend_treatment("VOO", "ETF", "Large Cap") == "Qualified"
    assert classify_dividend_treatment("SCHF", "ETF", "International") == "Qualified"

    # Fixed income -> Non-Qualified (regular income)
    assert classify_dividend_treatment("VGSH", "ETF", "") == "Non-Qualified"
    assert classify_dividend_treatment("VCSH", "Fixed Income", "") == "Non-Qualified"
    assert classify_dividend_treatment("BND", "ETF", "") == "Non-Qualified"
    assert classify_dividend_treatment("VBTIX", "Mutual Fund", "") == "Non-Qualified"
    assert classify_dividend_treatment("XYZ", "Fixed Income", "") == "Non-Qualified"

    # Cash / money market sweeps -> Non-Qualified
    assert classify_dividend_treatment("SPAXX", "Cash", "") == "Non-Qualified"
    assert classify_dividend_treatment("FCASH", "Cash", "") == "Non-Qualified"
    assert classify_dividend_treatment("CUR:USD", "Cash", "") == "Non-Qualified"

    # REITs / Real Estate -> Non-Qualified
    assert classify_dividend_treatment("O", "Stock", "Real Estate") == "Non-Qualified"
    assert classify_dividend_treatment("PLD", "Stock", "Real Estate") == "Non-Qualified"
    assert classify_dividend_treatment("VNQ", "ETF", "Real Estate") == "Non-Qualified"
    assert classify_dividend_treatment("UNKNOWN_REIT", "Stock", "Real Estate") == "Non-Qualified"

def test_classify_account():
    assert _classify_account("Mock Roth IRA") == "Tax-Exempt (Roth)"
    assert _classify_account("Mock Traditional IRA") == "Tax-Deferred"
    assert _classify_account("Company 401k Plan") == "Tax-Deferred"
    assert _classify_account("Joint Brokerage Account") == "Taxable"
    assert _classify_account("Checking Account") == "Taxable"

def test_calculate_tax_buckets():
    data = [
        # Taxable
        {"account_name": "Brokerage", "institution_name": "Mock Bank A", "ticker": "VOO", "security_name": "S&P 500", "security_id": "s1", "quantity": 10, "value": 5000, "cost_basis": 4000, "type_display": "ETF"},
        # Tax-Exempt
        {"account_name": "Roth IRA", "institution_name": "Mock Bank B", "ticker": "GOOG", "security_name": "Google", "security_id": "s2", "quantity": 20, "value": 3000, "cost_basis": 2000, "type_display": "Stock"},
        # Tax-Deferred
        {"account_name": "401k", "institution_name": "Mock Bank C", "ticker": "VBTIX", "security_name": "Bonds", "security_id": "s3", "quantity": 100, "value": 2000, "cost_basis": 2000, "type_display": "Mutual Fund"},
        {"account_name": "Rollover IRA", "institution_name": "Mock Bank B", "ticker": "AAPL", "security_name": "Apple", "security_id": "s4", "quantity": 5, "value": 1000, "cost_basis": 800, "type_display": "Stock"},
        # Ignore dust (<$0.01)
        {"account_name": "Brokerage", "institution_name": "Mock Bank A", "ticker": "CASH", "security_name": "Cash", "security_id": "s5", "quantity": 0, "value": 0.005, "cost_basis": 0, "type_display": "Cash"},
    ]
    df = pd.DataFrame(data)
    
    result = calculate_tax_buckets(df)
    
    assert result["total_value"] == 11000.0 # 5000 + 3000 + 2000 + 1000
    
    buckets = result["buckets"]
    assert "Taxable" in buckets
    assert "Tax-Exempt (Roth)" in buckets
    assert "Tax-Deferred" in buckets
    
    assert buckets["Taxable"]["value"] == 5000.01
    assert buckets["Tax-Exempt (Roth)"]["value"] == 3000.0
    assert buckets["Tax-Deferred"]["value"] == 3000.0 # 2000 + 1000
    
    # Check that dust was ignored
    taxable_holdings = buckets["Taxable"]["accounts"][0]["holdings"]
    assert len(taxable_holdings) == 1
    assert taxable_holdings[0]["ticker"] == "VOO"
    assert taxable_holdings[0]["dividend_treatment"] == "Qualified"

    # Check VBTIX in Tax-Deferred gets Non-Qualified treatment (bond fund)
    deferred_holdings = [
        h for acct in buckets["Tax-Deferred"]["accounts"] for h in acct["holdings"]
    ]
    vbtix = next(h for h in deferred_holdings if h["ticker"] == "VBTIX")
    assert vbtix["dividend_treatment"] == "Non-Qualified"
    assert vbtix["type_display"] == "Fixed Income"


def test_gains_summary():
    data = [
        # Taxable with gain
        {"account_name": "Brokerage", "institution_name": "Bank A", "ticker": "VOO", "security_name": "S&P 500", "security_id": "s1", "quantity": 10, "value": 5000, "cost_basis": 4000, "type_display": "ETF"},
        # Taxable with loss
        {"account_name": "Brokerage", "institution_name": "Bank A", "ticker": "PGR", "security_name": "Progressive", "security_id": "s2", "quantity": 10, "value": 1800, "cost_basis": 2000, "type_display": "Stock"},
        # Taxable with null/NaN cost basis (should be excluded from gains)
        {"account_name": "Equity Awards", "institution_name": "Bank A", "ticker": "GOOG", "security_name": "Google", "security_id": "s3", "quantity": 50, "value": 10000, "cost_basis": None, "type_display": "Stock"},
        # Retirement (Tax-Deferred) with gain
        {"account_name": "Traditional IRA", "institution_name": "Bank B", "ticker": "AAPL", "security_name": "Apple", "security_id": "s4", "quantity": 5, "value": 1000, "cost_basis": 800, "type_display": "Stock"},
        # Retirement (Tax-Exempt / Roth) with loss
        {"account_name": "Roth IRA", "institution_name": "Bank B", "ticker": "MSFT", "security_name": "Microsoft", "security_id": "s5", "quantity": 5, "value": 900, "cost_basis": 1000, "type_display": "Stock"},
    ]
    df = pd.DataFrame(data)
    result = calculate_tax_buckets(df)

    assert "gains_summary" in result
    gs = result["gains_summary"]

    # Taxable
    taxable = gs["taxable"]
    assert taxable["value_with_basis"] == 6800.0 # 5000 + 1800 (excludes 10000)
    assert taxable["cost_basis"] == 6000.0 # 4000 + 2000
    assert taxable["unrealized_gain"] == 1000.0 # VOO gain
    assert taxable["unrealized_loss"] == -200.0 # PGR loss
    assert taxable["net_gain"] == 800.0
    assert taxable["gain_pct"] == round(800.0 / 6000.0 * 100.0, 2)

    # Retirement
    retirement = gs["retirement"]
    assert retirement["value_with_basis"] == 1900.0 # 1000 + 900
    assert retirement["cost_basis"] == 1800.0 # 800 + 1000
    assert retirement["unrealized_gain"] == 200.0 # AAPL gain
    assert retirement["unrealized_loss"] == -100.0 # MSFT loss
    assert retirement["net_gain"] == 100.0
    assert retirement["gain_pct"] == round(100.0 / 1800.0 * 100.0, 2)

    # Total informational
    total_info = gs["total_informational"]
    assert total_info["value_with_basis"] == 8700.0
    assert total_info["cost_basis"] == 7800.0
    assert total_info["unrealized_gain"] == 1200.0
    assert total_info["unrealized_loss"] == -300.0
    assert total_info["net_gain"] == 900.0
    assert total_info["gain_pct"] == round(900.0 / 7800.0 * 100.0, 2)
