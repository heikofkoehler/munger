import pytest
from unittest.mock import patch, MagicMock
from metrics.valuation import _calculate_intrinsic_value_detailed

def test_calculate_intrinsic_value_detailed():
    # Setup mock inputs for an ideal company
    inputs = {
        "beta": 1.1,
        "d": 5000.0,
        "e": 95000.0,
        "interest_expense": 250.0,
        "tax_rate": 0.20,
        "fcf0": 10000.0,
        "g": 0.10, # 10% growth
        "cash": 2000.0,
        "shares": 1000,
        "current_price": 100.0,
    }
    
    rf_rate = 0.04
    erp = 0.0438
    
    result = _calculate_intrinsic_value_detailed(inputs, rf_rate, erp)
    
    assert result is not None
    assert "intrinsic_price" in result
    assert "mos" in result
    assert "wacc" in result
    
    # Basic math sanity checks
    assert result["wacc"] > 0
    assert result["intrinsic_price"] > 0
    assert result["fcf0"] == 10000.0

def test_calculate_intrinsic_value_no_shares():
    inputs = {
        "shares": 0
    }
    assert _calculate_intrinsic_value_detailed(inputs, 0.04) is None
    
def test_calculate_intrinsic_value_negative_current_price():
    inputs = {
        "beta": 1.0, "d": 0, "e": 100, "interest_expense": 0, "tax_rate": 0,
        "fcf0": 10, "g": 0.05, "cash": 0, "shares": 10, "current_price": 0
    }
    result = _calculate_intrinsic_value_detailed(inputs, 0.04)
    # MOS should be 1.0 (100%) when current price is 0 and intrinsic value is positive
    assert result["mos"] == 1.0
