import math

import pytest

from capex import finance as fin


def test_npv_matches_hand_calculation():
    # -100 today, +60 in years 1 and 2 at 10%
    expected = -100 + 60 / 1.1 + 60 / 1.1**2
    assert fin.npv(0.10, [-100, 60, 60]) == pytest.approx(expected)


def test_npv_zero_rate_is_simple_sum():
    assert fin.npv(0.0, [-50, 20, 20, 20]) == pytest.approx(10)


def test_irr_single_period():
    assert fin.irr([-100, 110]) == pytest.approx(0.10, abs=1e-8)


def test_irr_annuity_round_trip():
    cf = [-1000] + [200] * 10
    r = fin.irr(cf)
    assert fin.npv(r, cf) == pytest.approx(0, abs=1e-6)


def test_irr_none_when_never_positive():
    assert fin.irr([-100, -10, -10]) is None
    assert fin.irr([100, 10]) is None


def test_payback_interpolates():
    # cumulative: -100, -40, +20  -> crosses zero 2/3 of the way through year 2
    assert fin.payback_period([-100, 60, 60]) == pytest.approx(1 + 40 / 60)


def test_payback_none_if_never_recovered():
    assert fin.payback_period([-100, 10, 10]) is None


def test_discounted_payback_is_later_than_simple():
    cf = [-100, 40, 40, 40, 40]
    assert fin.discounted_payback_period(0.10, cf) > fin.payback_period(cf)


def test_bcr_and_pi():
    benefits = [0, 60, 60]
    costs = [100, 0, 0]
    net = [b - c for b, c in zip(benefits, costs)]
    pv_b = 60 / 1.1 + 60 / 1.1**2
    assert fin.benefit_cost_ratio(0.10, benefits, costs) == pytest.approx(pv_b / 100)
    assert fin.profitability_index(0.10, net, costs) == pytest.approx((pv_b - 100) / 100)


def test_equivalent_annual_value_recovers_level_annuity():
    rate, n, pmt = 0.08, 10, 25.0
    cf = [pmt] * n  # years 0..9
    # annuity-due PV divided by annuity factor of matching length
    eav = fin.equivalent_annual_value(rate, cf)
    assert fin.npv(rate, [eav] * n) == pytest.approx(fin.npv(rate, cf))
    assert math.isfinite(eav)
