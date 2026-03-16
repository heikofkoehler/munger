import pytest
import pandas as pd
from data.normalization import normalize_ticker, deduplicate, normalize_asset_class

def test_normalize_ticker():
    # Standard format adjustments
    assert normalize_ticker("BRK.B") == "BRK-B"
    assert normalize_ticker("BRK/B") == "BRK-B"
    assert normalize_ticker(" brk-b ") == "BRK-B"
    assert normalize_ticker("BRKB") == "BRK-B"
    
    # Overrides and fallbacks
    assert normalize_ticker("UNKNOWN_189993188208175994") == "VFFSX"
    assert normalize_ticker("Inst Tot Bd Mkt Ix Tr") == "VBTIX"
    
    # Aggregation
    assert normalize_ticker("GOOG", aggregate_classes=False) == "GOOG"
    assert normalize_ticker("GOOG", aggregate_classes=True) == "GOOGL"
    assert normalize_ticker("BRK-A", aggregate_classes=True) == "BRK-B"
    assert normalize_ticker("BRK-A", aggregate_classes=False) == "BRK-A"

def test_deduplicate():
    # Setup mock data with duplicates across accounts
    data = [
        {"account_id": "1", "ticker": "GOOG", "security_id": "s1", "security_name": "Google", "type_display": "Stock", "quantity": 10.0, "value": 1500.0, "cost_basis": 1000.0},
        {"account_id": "2", "ticker": "GOOG", "security_id": "s1", "security_name": "Google", "type_display": "Stock", "quantity": 5.0, "value": 750.0, "cost_basis": 500.0},
        {"account_id": "1", "ticker": "AAPL", "security_id": "s2", "security_name": "Apple", "type_display": "Stock", "quantity": 100.0, "value": 15000.0, "cost_basis": 10000.0},
        {"account_id": "3", "ticker": "UNKNOWN_189993188208175994", "security_id": "UNKNOWN_189993188208175994", "security_name": "Vanguard 500", "type_display": "ETF", "quantity": 1.0, "value": 100.0, "cost_basis": 50.0},
        {"account_id": "4", "ticker": "VFFSX", "security_id": "s4", "security_name": "Vanguard 500", "type_display": "Mutual Fund", "quantity": 2.0, "value": 200.0, "cost_basis": 100.0},
    ]
    df = pd.DataFrame(data)
    
    result = deduplicate(df)
    
    assert len(result) == 3 # GOOG, AAPL, VFFSX
    
    # Check aggregation of GOOG
    goog_row = result[result["ticker"] == "GOOG"].iloc[0]
    assert goog_row["quantity"] == 15.0
    assert goog_row["value"] == 2250.0
    assert goog_row["cost_basis"] == 1500.0
    
    # Check that UNKNOWN got overridden to VFFSX and then aggregated with the other VFFSX
    vffsx_row = result[result["ticker"] == "VFFSX"].iloc[0]
    assert vffsx_row["quantity"] == 3.0
    assert vffsx_row["value"] == 300.0

def test_normalize_asset_class():
    data = [
        {"ticker": "VOO", "type_display": "Stock"},
        {"ticker": "SPAXX", "type_display": "Other"},
        {"ticker": "VBTIX", "type_display": "Other"},
        {"ticker": "VFFSX", "type_display": "Stock"}
    ]
    df = pd.DataFrame(data)
    
    result = normalize_asset_class(df)
    
    assert result[result["ticker"] == "VOO"]["type_display"].iloc[0] == "Stock" # Unchanged
    assert result[result["ticker"] == "SPAXX"]["type_display"].iloc[0] == "Cash"
    assert result[result["ticker"] == "VBTIX"]["type_display"].iloc[0] == "Fixed Income"
    assert result[result["ticker"] == "VFFSX"]["type_display"].iloc[0] == "Mutual Fund"
