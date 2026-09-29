"""
Pure risk metrics functions for GlassBox.

Input `equity: list[float]` = daily account values in date order (length >= 2 unless stated).
All functions use standard library only; no numpy/pandas needed.
"""
from __future__ import annotations
from typing import List, Dict, Optional
from math import sqrt
from statistics import mean, stdev
def daily_returns(equity: List[float]) -> List[float]:
    """r_t = equity[t]/equity[t-1] - 1."""
    if len(equity) < 2:
        return []
    return [equity[i] / equity[i - 1] - 1 for i in range(1, len(equity))]
def max_drawdown(equity: List[float]) -> Dict[str, float | int | None]:
    """Calculate max drawdown and related indices.

    Returns: {"max_drawdown": d (positive fraction), "peak_index": i, "trough_index": j,
              "recovery_index": k or None}. Empty or single point -> max_drawdown 0.0 and indexes None.
    """
    if len(equity) < 2:
        return {"max_drawdown": 0.0, "peak_index": None, "trough_index": None, "recovery_index": None}

    # Track the peak and trough for max drawdown
    peak_index = 0
    trough_index = 0
    max_dd = 0.0
    potential_peak = 0  # Initialize

    running_max = equity[0]  # O(n): keep the running peak instead of re-scanning equity[:i] every day
    for i in range(1, len(equity)):
        # Check if equity[i] is a new peak (higher than all previous values)
        if equity[i] > running_max:
            running_max = equity[i]
            # This is a new peak - record it as potential peak for future drawdowns
            potential_peak = i
        else:
            # Calculate drawdown from the potential peak
            dd = (equity[potential_peak] - equity[i]) / equity[potential_peak]
            if dd > max_dd:
                max_dd = dd
                trough_index = i
                # Record this peak_index as the one that gave us max drawdown
                peak_index = potential_peak

    # If there's no drawdown (max_dd == 0), set peak_index and trough_index
    # to the last peak index (where the equity ends if it's rising)
    if max_dd == 0.0:
        # Find the last peak
        peak_index = len(equity) - 1
        trough_index = len(equity) - 1

    recovery_index = None
    if max_dd > 0:
        for i in range(trough_index + 1, len(equity)):
            if equity[i] >= equity[peak_index]:
                recovery_index = i
                break

    return {
        "max_drawdown": max_dd,
        "peak_index": peak_index,
        "trough_index": trough_index,
        "recovery_index": recovery_index,
    }
def underwater(equity: List[float]) -> List[float]:
    """Per day, equity/running_peak - 1 (0 or negative)."""
    if not equity:
        return []

    result = []
    running_peak = equity[0]
    for value in equity:
        if value > running_peak:
            running_peak = value
        result.append(value / running_peak - 1)
    return result
def sharpe(returns: List[float], periods_per_year: int = 252, risk_free_annual: float = 0.0) -> Optional[float]:
    """Sharpe ratio: mean excess daily / sample stdev (n-1) x sqrt(periods_per_year).
    Returns None if fewer than 20 returns or stdev == 0.
    """
    if len(returns) < 20:
        return None

    excess_returns = [r - risk_free_annual / periods_per_year for r in returns]

    if len(excess_returns) < 2:
        return None

    try:
        excess_mean = mean(excess_returns)
        excess_stdev = stdev(excess_returns)
    except:
        return None

    if excess_stdev == 0:
        return None

    return excess_mean / excess_stdev * sqrt(periods_per_year)
def cagr(equity: List[float], periods_per_year: int = 252) -> Optional[float]:
    """CAGR: (equity[-1]/equity[0]) ** (periods_per_year/(len(equity)-1)) - 1."""
    if len(equity) < 2:
        return None

    return (equity[-1] / equity[0]) ** (periods_per_year / (len(equity) - 1)) - 1
def calmar(equity: List[float], periods_per_year: int = 252) -> Optional[float]:
    """Calmar ratio: cagr / max_drawdown. None if drawdown is 0 or cagr is None."""
    cagr_val = cagr(equity, periods_per_year)
    if cagr_val is None:
        return None

    dd_result = max_drawdown(equity)
    max_dd = dd_result["max_drawdown"]

    if max_dd <= 0:
        return None

    return cagr_val / max_dd
def objective_score(
    total_return: float,
    annual_vol: float,
    max_dd: float,
    cost_frac: float,
    turnover: float,
    a: float = 0.5,
    b: float = 1.0,
    c: float = 1.0,
    d: float = 0.1,
) -> float:
    """Objective score with fixed weights.

    Formula: total_return - a*annual_vol - b*max_dd - c*cost_frac - d*turnover
    The weights a,b,c,d are never tuned on the backtest.
    """
    return total_return - a * annual_vol - b * max_dd - c * cost_frac - d * turnover
def tax_drag(pre_tax_return: float, costs: float, tax: float) -> Dict[str, float]:
    """Tax drag breakdown.

    Returns: {"pre_tax": x, "after_costs": x - costs, "after_tax": x - costs - tax,
              "drag": costs + tax} (all fractions of starting capital).
    """
    return {
        "pre_tax": pre_tax_return,
        "after_costs": pre_tax_return - costs,
        "after_tax": pre_tax_return - costs - tax,
        "drag": costs + tax,
    }