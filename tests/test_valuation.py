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

def test_pseudo_fcf0_consistency():
    # Test logic for ETF back-calculation from metrics/valuation.py
    def back_calc_fcf0(target_price, wacc, g, tg):
        if target_price <= 0: return 0
        pv_factor = 0
        fcf_step = 1.0
        for t in range(1, 6):
            fcf_step *= (1 + g)
            pv_factor += fcf_step / ((1 + wacc) ** t)
        
        cur_tg = tg
        if cur_tg >= wacc: cur_tg = wacc - 0.005
        tv_factor = (fcf_step * (1 + cur_tg)) / (wacc - cur_tg)
        pv_tv_factor = tv_factor / ((1 + wacc) ** 5)
        
        total_factor = pv_factor + pv_tv_factor
        return target_price / total_factor if total_factor > 0 else 0

    # Mock inputs
    target_price = 450.0
    wacc = 0.08
    g = 0.06
    tg = 0.03
    
    fcf0 = back_calc_fcf0(target_price, wacc, g, tg)
    
    # Now verify that _calculate_intrinsic_value_detailed with these inputs reproduces the target price
    inputs = {
        "beta": (wacc - 0.04) / 0.0438, # solving wacc = rf + beta*erp
        "d": 0, "e": 1000000, "interest_expense": 0, "tax_rate": 0,
        "fcf0": fcf0, "g": g, "cash": 0, "shares": 1, "current_price": 400
    }
    
    result = _calculate_intrinsic_value_detailed(inputs, 0.04, 0.0438)
    # Allow some floating point error
    assert abs(result["intrinsic_price"] - target_price) < 0.1
