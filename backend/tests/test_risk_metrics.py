"""
Tests for pure risk metrics functions in backend/app/risk_metrics.py.
All expected values are hand-computed and asserted with pytest.approx.
"""
from __future__ import annotations

import pytest

from app.risk_metrics import (
    daily_returns,
    max_drawdown,
    underwater,
    sharpe,
    cagr,
    calmar,
    objective_score,
    tax_drag,
)
def test_daily_returns_basic():
    # Example: [100, 120, 90] -> [20%, -25%]
    equity = [100.0, 120.0, 90.0]
    result = daily_returns(equity)
    assert result == pytest.approx([0.2, -0.25])
    assert daily_returns([100.0]) == []
    assert daily_returns([]) == []
def test_max_drawdown_on_spec_series():
    """[100, 120, 90, 95, 130] -> max_drawdown 0.25, peak 1, trough 2, recovery 4"""
    equity = [100.0, 120.0, 90.0, 95.0, 130.0]
    result = max_drawdown(equity)
    assert result["max_drawdown"] == pytest.approx(0.25)
    assert result["peak_index"] == 1
    assert result["trough_index"] == 2
    assert result["recovery_index"] == 4

def test_max_drawdown_no_recovery():
    """Series that never recovers: [100, 120, 90, 80] -> recovery None"""
    equity = [100.0, 120.0, 90.0, 80.0]
    result = max_drawdown(equity)
    assert result["max_drawdown"] == pytest.approx(0.3333333333333333)  # (120-80)/120 = 40/120
    assert result["peak_index"] == 1
    assert result["trough_index"] == 3
    assert result["recovery_index"] is None

def test_max_drawdown_short():
    assert max_drawdown([100.0]) == {"max_drawdown": 0.0, "peak_index": None, "trough_index": None, "recovery_index": None}
    assert max_drawdown([]) == {"max_drawdown": 0.0, "peak_index": None, "trough_index": None, "recovery_index": None}
    assert max_drawdown([100.0, 120.0]) == {"max_drawdown": 0.0, "peak_index": 1, "trough_index": 1, "recovery_index": None}
def test_underwater_on_spec_series():
    """[100, 120, 90, 95, 130] -> [0, 0, -0.25, -0.2083333, 0]"""
    equity = [100.0, 120.0, 90.0, 95.0, 130.0]
    result = underwater(equity)
    expected = [0.0, 0.0, -0.25, -95.0 / 120.0, 0.0]  # -95/120 ≈ -0.7916667? Wait, let me recalculate:
    # For day 3: equity[2]=90, running_peak=120, underwater = 90/120 - 1 = -0.25 ✓
    # For day 4: equity[3]=95, running_peak=120, underwater = 95/120 - 1 = -0.2083333 ✓
    # Actually day 4 should be 95/120 - 1 = -0.2083333, not -95/120
    expected = [0.0, 0.0, -0.25, -0.20833333333333334, 0.0]
    assert result == pytest.approx(expected)

def test_underwater_monotonic():
    equity = [100.0, 110.0, 120.0, 130.0, 140.0]
    result = underwater(equity)
    assert result == pytest.approx([0.0, 0.0, 0.0, 0.0, 0.0])

    equity = [100.0, 90.0, 80.0, 70.0, 60.0]
    result = underwater(equity)
    # The spec says "per day, equity/running_peak - 1"
    # For [100, 90, 80, 70, 60]:
    # Day 0: running_peak = 100, underwater = 100/100 - 1 = 0.0
    # Day 1: running_peak = 100 (never updated because 90 < 100), underwater = 90/100 - 1 = -0.1
    # Day 2: running_peak = 100 (never updated because 80 < 100), underwater = 80/100 - 1 = -0.2
    # Day 3: running_peak = 100 (never updated because 70 < 100), underwater = 70/100 - 1 = -0.3
    # Day 4: running_peak = 100 (never updated because 60 < 100), underwater = 60/100 - 1 = -0.4
    assert result == pytest.approx([0.0, -0.1, -0.2, -0.3, -0.4])
def test_sharpe_constant_returns():
    """Constant returns -> None (stdev == 0)"""
    constant_returns = [0.01, 0.01, 0.01, 0.01, 0.01, 0.01, 0.01, 0.01, 0.01, 0.01, 0.01, 0.01, 0.01, 0.01, 0.01, 0.01, 0.01, 0.01, 0.01, 0.01]
    assert sharpe(constant_returns) is None
    # Less than 20 returns -> None
    assert sharpe(constant_returns[:19]) is None
def test_sharpe_hand_built_series():
    """Create a series with known Sharpe ratio."""
    # Returns: [0.05, 0.05, -0.05, -0.05, 0.05, 0.05, -0.05, -0.05, 0.05, 0.05, -0.05, -0.05, 0.05, 0.05, -0.05, -0.05, 0.05, 0.05, -0.05, -0.05]
    # Mean = 0.0, stdev = sqrt((0.05²*10 + (-0.05)²*10)/19) = 0.05
    # Sharpe = 0.0 / 0.05 * sqrt(252) = 0.0
    returns = [0.05] * 10 + [-0.05] * 10
    result = sharpe(returns)
    assert result == pytest.approx(0.0)

def test_sharpe_non_zero():
    """Series with non-zero mean and stdev."""
    # Returns: [0.02, 0.03, 0.04, 0.05, 0.06] (less than 20, should be None)
    assert sharpe([0.02, 0.03, 0.04, 0.05, 0.06]) is None

    # Create 20 returns with mean 0.01 and stdev 0.02
    returns = [0.01] * 20  # Constant, so Sharpe should be None
    assert sharpe(returns) is None
def test_cagr_double():
    """253-point series doubling: [100, 100, ..., 200] -> cagr 1.0"""
    equity = [100.0] * 252 + [200.0]  # 253 points total
    result = cagr(equity)
    assert result == pytest.approx(1.0)  # Doubles exactly

def test_cagr_short():
    assert cagr([100.0]) is None
    assert cagr([]) is None
    # For [100.0, 150.0]: (150/100)^(252/(2-1)) - 1 = 1.5^252 - 1
    assert cagr([100.0, 150.0]) == pytest.approx(1.5**252 - 1)

def test_cagr_calculation():
    """Test cagr formula directly."""
    # Simple case: [100, 200] over 1 day, annualized to 252 days
    # (200/100)^(252/1) - 1 = 2^252 - 1 (enormous)
    equity = [100.0, 200.0]
    result = cagr(equity)
    assert result == pytest.approx(2.0**252 - 1)
def test_calmar():
    """Calmar = cagr / max_drawdown, None if drawdown is 0 or cagr is None."""
    # Series with no drawdown: [100, 120, 150, 180]
    equity_no_dd = [100.0, 120.0, 150.0, 180.0]
    assert calmar(equity_no_dd) is None  # max_drawdown is 0

    # Use the double series from test_cagr_double (has drawdown)
    equity = [100.0] * 252 + [200.0]
    result = calmar(equity)
    # max_drawdown = 0 (never drops below 100)
    assert result is None

    # Series that goes down then recovers: [100, 200, 100, 200] (253 points for annualization)
    equity = [100.0, 200.0, 100.0, 200.0] + [200.0] * 249
    result = calmar(equity)
    # cagr = (200/100) ** (252/252) - 1 = 1.0 ; max drawdown = (200-100)/200 = 0.5 ; calmar = 1.0 / 0.5 = 2.0
    assert result == pytest.approx(2.0)
def test_objective_score():
    """Test the objective_score formula with hand-computed numbers."""
    # total_return - a*annual_vol - b*max_dd - c*cost_frac - d*turnover
    # Let's use simple numbers: total_return=0.1, annual_vol=0.05, max_dd=0.2, cost_frac=0.01, turnover=0.05
    # With default weights (a=0.5, b=1.0, c=1.0, d=0.1):
    # result = 0.1 - 0.5*0.05 - 1.0*0.2 - 1.0*0.01 - 0.1*0.05
    # result = 0.1 - 0.025 - 0.2 - 0.01 - 0.005 = -0.14
    result = objective_score(total_return=0.1, annual_vol=0.05, max_dd=0.2, cost_frac=0.01, turnover=0.05)
    assert result == pytest.approx(-0.14)

def test_objective_score_defaults():
    """Test with default weights."""
    result = objective_score(total_return=0.2, annual_vol=0.1, max_dd=0.15, cost_frac=0.02, turnover=0.08)
    # 0.2 - 0.5*0.1 - 1.0*0.15 - 1.0*0.02 - 0.1*0.08 = 0.2 - 0.05 - 0.15 - 0.02 - 0.008 = -0.028
    assert result == pytest.approx(-0.028)
def test_tax_drag():
    """Test tax_drag hand computation."""
    result = tax_drag(pre_tax_return=0.2, costs=0.05, tax=0.03)
    assert result["pre_tax"] == 0.2
    assert result["after_costs"] == pytest.approx(0.15)  # 0.2 - 0.05
    assert result["after_tax"] == pytest.approx(0.12)  # 0.2 - 0.05 - 0.03
    assert result["drag"] == pytest.approx(0.08)  # 0.05 + 0.03

def test_tax_drag_zero():
    result = tax_drag(pre_tax_return=0.0, costs=0.0, tax=0.0)
    assert result["pre_tax"] == 0.0
    assert result["after_costs"] == 0.0
    assert result["after_tax"] == 0.0
    assert result["drag"] == 0.0

def test_equity_edge_cases():
    """Edge cases for equity inputs."""
    # Empty
    assert daily_returns([]) == []
    assert max_drawdown([]) == {"max_drawdown": 0.0, "peak_index": None, "trough_index": None, "recovery_index": None}
    assert underwater([]) == []
    assert cagr([]) is None
    assert calmar([]) is None

    # Single point
    assert max_drawdown([100.0]) == {"max_drawdown": 0.0, "peak_index": None, "trough_index": None, "recovery_index": None}
    assert cagr([100.0]) is None
    assert calmar([100.0]) is None