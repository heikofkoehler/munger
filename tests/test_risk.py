import pytest
import pandas as pd
from unittest.mock import patch, MagicMock
import numpy as np
from metrics.risk import (
    check_concentration,
    calculate_risk_metrics,
    calculate_efficiency_metrics,
    calculate_diversification_metrics,
)

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


def test_calculate_diversification_breadth():
    # 4 equal-weighted positions of $2,500 each:
    # N_eff = 1 / (4 * 0.25^2) = 1 / (4 * 0.0625) = 1 / 0.25 = 4.0
    # HHI = 4 * 25^2 = 2500.0
    data = [
        {"ticker": "VOO", "security_name": "S&P 500", "value": 2500, "type_display": "ETF"},
        {"ticker": "GOOG", "security_name": "Google", "value": 2500, "type_display": "Stock"},
        {"ticker": "AAPL", "security_name": "Apple", "value": 2500, "type_display": "Stock"},
        {"ticker": "MSFT", "security_name": "Microsoft", "value": 2500, "type_display": "Stock"},
    ]
    df = pd.DataFrame(data)

    # Disable yfinance network call to test breadth calculation
    with patch("yfinance.download", side_effect=Exception("Offline test")):
        res = calculate_diversification_metrics(df)

    assert res["effective_holdings"] == 4.0
    assert res["hhi"] == 2500.0
    assert res["top5_concentration"] == 100.0


def test_calculate_diversification_empty():
    res = calculate_diversification_metrics(pd.DataFrame([]))
    assert res["effective_holdings"] == 0.0
    assert res["diversification_ratio"] is None
    assert res["risk_contributions"] == []


def test_calculate_diversification_math():
    # Synthetic test: 2 uncorrelated assets with simulated daily price series
    # When assets are uncorrelated with equal vol, DR should be ~sqrt(2) = 1.41
    np.random.seed(42)
    days = 100
    dates = pd.date_range("2024-01-01", periods=days, freq="B")
    
    # Generate 2 independent random walks
    r1 = np.random.normal(0.0005, 0.01, days)
    r2 = np.random.normal(0.0005, 0.01, days)
    p1 = 100.0 * np.exp(np.cumsum(r1))
    p2 = 100.0 * np.exp(np.cumsum(r2))
    
    mock_prices = pd.DataFrame({"STOCK_A": p1, "STOCK_B": p2}, index=dates)

    data = [
        {"ticker": "STOCK_A", "security_name": "Stock A", "value": 5000, "type_display": "Stock"},
        {"ticker": "STOCK_B", "security_name": "Stock B", "value": 5000, "type_display": "Stock"},
    ]
    df = pd.DataFrame(data)

    mock_cols = pd.MultiIndex.from_tuples([("Close", "STOCK_A"), ("Close", "STOCK_B")])
    mock_df = pd.DataFrame(mock_prices.values, index=dates, columns=mock_cols)

    mock_yf = MagicMock()
    mock_yf.download.return_value = mock_df

    with patch.dict("sys.modules", {"yfinance": mock_yf}):
        with patch("metrics.risk._yf_db_get", return_value=None):
            with patch("metrics.risk._yf_db_set", return_value=None):
                res = calculate_diversification_metrics(df)

    # Diversification Ratio should be substantially greater than 1.0 (uncorrelated)
    assert res["diversification_ratio"] is not None
    assert res["diversification_ratio"] > 1.25
    assert res["volatility_reduction_pct"] > 20.0
    assert len(res["risk_contributions"]) == 2
    # Total risk contribution should sum to ~100%
    total_rc = sum(rc["risk_contrib_pct"] for rc in res["risk_contributions"])
    assert 98.0 <= total_rc <= 102.0
