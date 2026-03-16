import pytest
import pandas as pd
from unittest.mock import patch
from metrics.risk import check_concentration, calculate_risk_metrics, calculate_efficiency_metrics

def test_check_concentration():
    data = [
        {"ticker": "VOO", "security_name": "S&P 500", "value": 8000},
        {"ticker": "AAPL", "security_name": "Apple", "value": 1500},
        {"ticker": "CASH", "security_name": "Cash", "value": 500},
    ]
    df = pd.DataFrame(data)
    
    with patch("metrics.risk.CONC_THRESHOLD", 10.0):
        result = check_concentration(df)
        
        # Only VOO and AAPL > 10%
        assert len(result) == 2
        assert result[0]["ticker"] == "VOO"
        assert result[0]["weight_pct"] == 80.0
        assert result[1]["ticker"] == "AAPL"
        assert result[1]["weight_pct"] == 15.0

@patch("metrics.risk.get_fund_details")
def test_calculate_risk_metrics(mock_get_fund_details):
    # Mock VOO holdings and expense ratio
    mock_get_fund_details.side_effect = lambda ticker: {
        "VOO": {"expense_ratio": 0.0003, "holdings": [{"ticker": "AAPL", "weight": 0.05}, {"ticker": "MSFT", "weight": 0.05}]},
        "AAPL": {"expense_ratio": None, "holdings": []},
        "CASH": {"expense_ratio": None, "holdings": []}
    }.get(ticker, {"expense_ratio": None, "holdings": []})
    
    data = [
        {"ticker": "VOO", "security_id": "s1", "security_name": "S&P 500", "value": 10000, "type_display": "ETF"},
        {"ticker": "AAPL", "security_id": "s2", "security_name": "Apple", "value": 5000, "type_display": "Stock"},
    ]
    df = pd.DataFrame(data)
    
    with patch("metrics.risk.CONC_THRESHOLD", 10.0):
        result = calculate_risk_metrics(df)
    
    # Total Value = 15000
    # True exposure of AAPL = Direct(5000) + Indirect from VOO(10000 * 0.05 = 500) = 5500
    assert round(result["wer"], 6) == round((10000 * 0.0003) / 15000, 6)
    assert result["total_annual_cost"] == 3.0
    
    exposures = {e["ticker"]: e for e in result["true_exposure"]}
    assert "AAPL" in exposures
    assert exposures["AAPL"]["direct"] == 5000.0
    assert exposures["AAPL"]["indirect"] == 500.0
    assert exposures["AAPL"]["value"] == 5500.0
    assert "MSFT" in exposures
    assert exposures["MSFT"]["indirect"] == 500.0

@patch("metrics.risk.get_fund_details")
def test_calculate_efficiency_metrics(mock_get_fund_details):
    mock_get_fund_details.side_effect = lambda ticker: {
        "VOO": {"expense_ratio": 0.0003, "holdings": []},
        "HIGH_FEE_FUND": {"expense_ratio": 0.015, "holdings": []}, # 1.5%
        "AAPL": {"expense_ratio": None, "holdings": []},
    }.get(ticker, {"expense_ratio": None, "holdings": []})
    
    data = [
        {"ticker": "VOO", "security_name": "S&P 500", "value": 10000},
        {"ticker": "HIGH_FEE_FUND", "security_name": "Expensive", "value": 10000},
        {"ticker": "AAPL", "security_name": "Apple", "value": 10000},
    ]
    df = pd.DataFrame(data)
    
    result = calculate_efficiency_metrics(df, growth_rate=0.07, benchmark_fee=0.0005)
    
    # VOO cost = 3.0, HIGH_FEE_FUND cost = 150.0. Total = 153.0
    assert result["total_annual_cost"] == 153.0
    assert result["weighted_expense_ratio"] == 153.0 / 30000.0
    
    # High cost assets check
    assert len(result["high_cost_assets"]) == 2 # VOO and HIGH_FEE_FUND are both funds
    expensive = next(x for x in result["high_cost_assets"] if x["ticker"] == "HIGH_FEE_FUND")
    assert expensive["status"] == "Red"
    assert expensive["annual_cost"] == 150.0
    
    voo = next(x for x in result["high_cost_assets"] if x["ticker"] == "VOO")
    assert voo["status"] == "Green"
    assert voo["annual_cost"] == 3.0
