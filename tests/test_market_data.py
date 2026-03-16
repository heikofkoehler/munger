import pytest
from unittest.mock import patch, MagicMock
from data.market_data import enrich_with_market_data, get_fund_details

@patch("yfinance.Ticker")
def test_get_fund_details(mock_yf_ticker):
    mock_ticker_instance = MagicMock()
    mock_ticker_instance.info = {"netExpenseRatio": 0.03} # yfinance returns 0.03 for 0.03%
    
    import pandas as pd
    mock_holdings = pd.DataFrame([
        {"Holding Percent": 0.05}
    ], index=["AAPL"])
    
    mock_funds_data = MagicMock()
    mock_funds_data.top_holdings = mock_holdings
    mock_ticker_instance.funds_data = mock_funds_data
    
    mock_yf_ticker.return_value = mock_ticker_instance
    
    result = get_fund_details("MOCK_ETF")
    
    assert result["expense_ratio"] == 0.0003
    assert len(result["holdings"]) == 1
    assert result["holdings"][0]["ticker"] == "AAPL"
    assert result["holdings"][0]["weight"] == 0.05

@patch("data.market_data._yf_db_get")
@patch("data.market_data._yf_db_set")
@patch("yfinance.Ticker")
def test_enrich_with_market_data(mock_yf_ticker, mock_db_set, mock_db_get):
    # Mock DB get to return None (cache miss)
    mock_db_get.return_value = None
    
    # Mock yf Ticker
    mock_ticker_instance = MagicMock()
    mock_ticker_instance.info = {
        "dividendYield": 0.015,
        "trailingPE": 25.0,
        "sector": "Technology"
    }
    mock_yf_ticker.return_value = mock_ticker_instance
    
    positions = [
        {"ticker": "AAPL", "value": 1000},
        {"ticker": "FCASH", "value": 100} # Should be skipped
    ]
    
    result = enrich_with_market_data(positions)
    
    assert len(result) == 2
    
    aapl = next(p for p in result if p["ticker"] == "AAPL")
    assert aapl["dividend_yield"] == 0.015
    assert aapl["trailing_pe"] == 25.0
    assert aapl["sector"] == "Technology"
    
    fcash = next(p for p in result if p["ticker"] == "FCASH")
    assert fcash["dividend_yield"] is None
