import numpy as np
import pandas as pd
import pytest

from capex import finance as fin
from capex.analysis import appraise_all
from capex.cashflows import Overrides, build_cashflows
from capex.db import Inputs
from capex.generator import generate
from capex.montecarlo import simulate
from capex.optimizer import Problem, solve_greedy, solve_milp

A = {"energy_purchase_cost": 28.0, "avg_tariff": 45.0, "collection_rate": 0.85, "voll": 120.0,
     "ens_societal": 1, "benefit_growth": 0.0, "discount_rate": 0.12}


def project(**kw):
    base = dict(project_id="X", capex_mn=100.0, build_years=1, life_years=10, om_pct=0.0,
                tech_loss_mwh=1000.0, theft_mwh=0.0, ens_mwh=0.0, opex_saving_mn=0.0,
                benefit_ramp_years=1)
    base.update(kw)
    return pd.Series(base)


@pytest.fixture(scope="module")
def inputs():
    return Inputs.from_frames(generate(42))


# ---------------------------------------------------------------- cash flows
def test_unit_conversion_mwh_to_pkr_mn():
    cf = build_cashflows(project(), np.array([1.0]), A)
    # 1,000 MWh x 28 PKR/kWh = PKR 28 mn per year
    assert cf.loc[1, "benefit_tech_loss"] == pytest.approx(28.0)
    assert cf.loc[0, "benefit_tech_loss"] == 0  # nothing during construction


def test_horizon_and_capex_phasing():
    cf = build_cashflows(project(build_years=2), np.array([0.6, 0.4]), A)
    assert len(cf) == 2 + 10
    assert cf["capex"].tolist()[:3] == pytest.approx([60, 40, 0])


def test_ramp_up():
    cf = build_cashflows(project(benefit_ramp_years=2), np.array([1.0]), A)
    assert cf.loc[1, "benefit_tech_loss"] == pytest.approx(14.0)
    assert cf.loc[2, "benefit_tech_loss"] == pytest.approx(28.0)


def test_delay_pushes_benefits_out_and_lowers_npv():
    p, s = project(), np.array([1.0])
    on_time = build_cashflows(p, s, A)
    late = build_cashflows(p, s, A, Overrides(delay_years=1))
    assert late.loc[1, "benefits"] == 0
    assert fin.npv(0.12, late["net"]) < fin.npv(0.12, on_time["net"])


def test_ens_perspective_switch():
    p, s = project(tech_loss_mwh=0, ens_mwh=100), np.array([1.0])
    societal = build_cashflows(p, s, A).loc[1, "benefit_ens"]
    utility = build_cashflows(p, s, {**A, "ens_societal": 0}).loc[1, "benefit_ens"]
    assert societal == pytest.approx(100 * 120 / 1000)
    assert utility == pytest.approx(100 * (45 - 28) / 1000)


# --------------------------------------------------------------- Monte Carlo
def test_monte_carlo_collapses_to_deterministic_without_uncertainty(inputs):
    flat = inputs.risk_profiles.copy()
    flat[:] = 1.0
    flat[["p_delay_1", "p_delay_2"]] = 0.0
    no_risk = Inputs(inputs.projects, inputs.phasing,
                     {**inputs.assumptions, "energy_price_volatility": 0.0}, flat,
                     inputs.scenarios, inputs.budget, inputs.categories)
    metrics, _ = appraise_all(no_risk)
    sims = simulate(no_risk, n_sims=50, seed=1)
    np.testing.assert_allclose(sims.npv.mean(axis=0), metrics["npv_mn"].to_numpy(), atol=0.01)


def test_monte_carlo_is_reproducible(inputs):
    a = simulate(inputs, n_sims=200, seed=7).npv
    b = simulate(inputs, n_sims=200, seed=7).npv
    np.testing.assert_array_equal(a, b)


# ----------------------------------------------------------------- optimiser
def toy_problem(budget):
    projects = pd.DataFrame([
        # id, build, capex, mandatory, group, depends, earliest, latest
        ("A", 1, 100, False, None, None, 0, None),
        ("B", 1, 100, False, "G", None, 0, None),
        ("C", 1, 60, False, "G", None, 0, None),
        ("D", 1, 50, False, None, "E", 0, None),
        ("E", 1, 50, False, None, None, 0, None),
        ("M", 1, 30, True, None, None, 0, 0),
    ], columns=["project_id", "build_years", "capex_mn", "mandatory", "exclusive_group",
                "depends_on", "earliest_start", "latest_start"])
    phasing = pd.DataFrame({"project_id": projects["project_id"], "build_year_idx": 0,
                            "share": 1.0})
    value = pd.Series({"A": 80, "B": 70, "C": 50, "D": 90, "E": -20, "M": -10})
    return Problem(projects, phasing, value, np.asarray(budget, dtype=float), rate=0.0)


def test_milp_respects_all_constraints():
    pf = solve_milp(toy_problem([200, 150]))
    sel = pf.selection
    assert pf.status == "Optimal"
    assert sel["M"] == 0                                   # mandatory, with deadline
    assert not ("B" in sel and "C" in sel)                 # exclusive alternatives
    if "D" in sel:                                         # prerequisite starts first
        assert "E" in sel and sel["E"] <= sel["D"]
    assert np.all(pf.annual_capex <= pf.budget + 1e-6)     # budget every year


def test_milp_funds_negative_enabler_when_it_unlocks_value():
    # E is NPV-negative alone, but D (+90) needs it: net +70 is worth it
    pf = solve_milp(toy_problem([500, 500]))
    assert {"D", "E"} <= set(pf.selection)


def test_milp_never_worse_than_greedy(inputs):
    metrics, _ = appraise_all(inputs)
    value = metrics.set_index("project_id")["npv_mn"]
    prob = Problem(inputs.projects, inputs.phasing, value,
                   inputs.budget["capex_budget_mn"].to_numpy(), 0.12)
    milp = solve_milp(prob)
    for rank in ("npv", "pi"):
        greedy = solve_greedy(prob, rank)
        assert milp.total_value >= greedy.total_value - 1e-6
        assert np.all(greedy.annual_capex <= greedy.budget + 1e-6)
