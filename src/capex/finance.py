"""Core investment-appraisal formulas.

Convention: cash flow index t = 0, 1, 2 ... is years from the start of the
project; a flow in year t is discounted by (1 + r) ** -t. All values are real
(inflation-removed), so ``rate`` must be a real discount rate.
"""
from __future__ import annotations

import numpy as np
from scipy.optimize import brentq


def discount_factors(rate: float, n: int) -> np.ndarray:
    return (1.0 + rate) ** -np.arange(n)


def npv(rate: float, cashflows) -> float:
    cf = np.asarray(cashflows, dtype=float)
    return float(np.dot(cf, discount_factors(rate, len(cf))))


def irr(cashflows, lo: float = -0.95, hi: float = 10.0) -> float | None:
    """Internal rate of return.

    Scans for the first sign change of NPV(r) between ``lo`` and ``hi`` and
    refines it with Brent's method. Returns None when no IRR exists (e.g. the
    project never pays back), instead of a misleading number.
    """
    cf = np.asarray(cashflows, dtype=float)
    if not (np.any(cf > 0) and np.any(cf < 0)):
        return None
    grid = np.concatenate([np.linspace(lo, 1.0, 200), np.linspace(1.0, hi, 50)[1:]])
    values = np.array([npv(r, cf) for r in grid])
    sign_change = np.where(np.sign(values[:-1]) != np.sign(values[1:]))[0]
    if len(sign_change) == 0:
        return None
    i = sign_change[0]
    return float(brentq(lambda r: npv(r, cf), grid[i], grid[i + 1], xtol=1e-10))


def _crossing_year(cumulative: np.ndarray) -> float | None:
    """First (fractional) year where a cumulative series turns non-negative."""
    if cumulative[-1] < 0:
        return None
    for t in range(len(cumulative)):
        if cumulative[t] >= 0:
            if t == 0:
                return 0.0
            prev, cur = cumulative[t - 1], cumulative[t]
            return float(t - 1 + (-prev) / (cur - prev))
    return None


def payback_period(cashflows) -> float | None:
    """Simple (undiscounted) payback in years, linearly interpolated."""
    return _crossing_year(np.cumsum(np.asarray(cashflows, dtype=float)))


def discounted_payback_period(rate: float, cashflows) -> float | None:
    cf = np.asarray(cashflows, dtype=float)
    return _crossing_year(np.cumsum(cf * discount_factors(rate, len(cf))))


def benefit_cost_ratio(rate: float, benefits, costs) -> float | None:
    """PV(benefits) / PV(costs). ``costs`` are positive numbers (capex + opex)."""
    pv_c = npv(rate, costs)
    return None if pv_c == 0 else npv(rate, benefits) / pv_c


def profitability_index(rate: float, cashflows, capex) -> float | None:
    """NPV per unit of present-value capital — the right ranking metric when
    capital, not opportunity, is the binding constraint."""
    pv_k = npv(rate, capex)
    return None if pv_k == 0 else npv(rate, cashflows) / pv_k


def annuity_factor(rate: float, n: int) -> float:
    """PV of 1 per year for years 0..n-1 (same timing convention as npv)."""
    return float(discount_factors(rate, n).sum())


def equivalent_annual_value(rate: float, cashflows) -> float:
    """NPV spread as a level annual amount over the project horizon — lets
    projects with different lives be compared fairly."""
    cf = np.asarray(cashflows, dtype=float)
    return npv(rate, cf) / annuity_factor(rate, len(cf))
