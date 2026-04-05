import pytest
import pandas as pd
from unittest.mock import patch
from metrics.risk import calculate_efficiency_metrics

@patch("metrics.risk.get_fund_details")
def test_opportunity_cost_calculation_precision(mock_get_fund_details):
    """
    Verify that the wealth gap (opportunity cost) is calculated correctly
    using a fixed dataset and the standard compound interest formula.
    """
    # 1. Setup a controlled portfolio
    # Total Value: $1,000,000
    # Weighted Expense Ratio: 0.01 (1.00%)
    # Annual Cost: $10,000
    mock_get_fund_details.side_effect = lambda ticker: {
        "FUND_A": {"expense_ratio": 0.01, "holdings": []},
    }.get(ticker, {"expense_ratio": None, "holdings": []})
    
    data = [
        {"ticker": "FUND_A", "security_name": "Test Fund", "value": 1_000_000.0},
    ]
    df = pd.DataFrame(data)
    
    # 2. Run calculation with zero-fee benchmark (as recently updated)
    growth_rate = 0.07
    benchmark_fee = 0.0
    result = calculate_efficiency_metrics(df, growth_rate=growth_rate, benchmark_fee=benchmark_fee)
    
    # 3. Verify core metrics
    assert result["total_annual_cost"] == 10_000.0
    assert result["weighted_expense_ratio"] == 0.01
    
    # 4. Verify 5, 10, 15, 20, 25, 30 year projections
    # Formula: Value * (1 + (r - f))^years
    total_val = 1_000_000.0
    
    for p in result["projections"]:
        years = p["years"]
        
        # Manual High-Precision Calculation
        expected_bench = total_val * ((1 + growth_rate - benchmark_fee) ** years)
        expected_curr = total_val * ((1 + growth_rate - 0.01) ** years)
        expected_gap = expected_bench - expected_curr
        
        # Allow for small rounding differences in final display values
        assert abs(p["optimized_val"] - expected_bench) < 1.0
        assert abs(p["current_val"] - expected_curr) < 1.0
        assert abs(p["wealth_gap"] - expected_gap) < 1.0

def test_zero_value_portfolio_efficiency():
    """Ensure the function handles empty/zero-value portfolios gracefully."""
    df = pd.DataFrame(columns=["ticker", "security_name", "value"])
    result = calculate_efficiency_metrics(df)
    assert result["weighted_expense_ratio"] == 0.0
    assert result["total_annual_cost"] == 0.0
    assert result["projections"] == []
