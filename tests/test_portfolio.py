import pytest
import pandas as pd
from metrics.portfolio import calculate_metrics, calculate_institutions, calculate_sector_allocation

def test_calculate_metrics():
    data = [
        {"ticker": "VOO", "security_id": "s1", "security_name": "S&P 500", "value": 6000.0, "quantity": 15.0, "type_display": "ETF"},
        {"ticker": "GOOG", "security_id": "s2", "security_name": "Google", "value": 4000.0, "quantity": 30.0, "type_display": "Stock"},
    ]
    df = pd.DataFrame(data)
    
    result = calculate_metrics(df)
    
    assert result["total_value"] == 10000.0
    
    positions = result["positions"]
    assert len(positions) == 2
    
    # Sorted by value desc
    assert positions[0]["ticker"] == "VOO"
    assert positions[0]["weight_pct"] == 60.0
    
    assert positions[1]["ticker"] == "GOOG"
    assert positions[1]["weight_pct"] == 40.0
    
    alloc = result["allocation"]
    assert alloc["ETF"] == 60.0
    assert alloc["Stock"] == 40.0

def test_calculate_institutions():
    data = [
        {"institution_name": "Institution A", "value": 5000},
        {"institution_name": "Institution A", "value": 2000},
        {"institution_name": "Institution B", "value": 3000},
    ]
    df = pd.DataFrame(data)
    
    result = calculate_institutions(df)
    
    assert len(result) == 2
    # Sorted by value desc
    assert result[0]["institution_name"] == "Institution A"
    assert result[0]["value"] == 7000
    assert result[0]["weight_pct"] == 70.0
    
    assert result[1]["institution_name"] == "Institution B"
    assert result[1]["value"] == 3000
    assert result[1]["weight_pct"] == 30.0

def test_calculate_sector_allocation():
    # Use patch to mock _market_cache so we don't rely on it being populated globally
    from unittest.mock import patch
    
    mock_cache = {
        "GOOG": {"sector": "Technology"},
        "AAPL": {"sector": "Technology"},
        "JPM": {"sector": "Financial Services"}
    }
    
    positions = [
        {"ticker": "GOOG", "value": 4000, "type_display": "Stock"},
        {"ticker": "AAPL", "value": 2000, "type_display": "Stock"},
        {"ticker": "JPM", "value": 2000, "type_display": "Stock"},
        {"ticker": "VOO", "value": 1000, "type_display": "ETF"}, # Not in cache
        {"ticker": "VBTIX", "value": 500, "type_display": "Fixed Income"},
        {"ticker": "FCASH", "value": 500, "type_display": "Cash"},
    ]
    
    with patch("metrics.portfolio._market_cache", mock_cache):
        result = calculate_sector_allocation(positions)
    
    assert result["Technology"] == 60.0 # (4000+2000)/10000 * 100
    assert result["Financial Services"] == 20.0
    assert result["Fixed Income"] == 5.0
    assert result["Cash"] == 5.0
    assert result["Other/Unknown"] == 10.0 # VOO
