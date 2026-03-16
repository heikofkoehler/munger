import pytest
import pandas as pd
from metrics.tax import calculate_tax_buckets, _classify_account

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
