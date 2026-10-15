"""Deterministic project appraisal for a given scenario."""
from __future__ import annotations

import numpy as np
import pandas as pd

from . import finance as fin
from .cashflows import Overrides, build_cashflows, phasing_lookup
from .db import Inputs


def scenario_overrides(inputs: Inputs, scenario: str) -> tuple[Overrides, float]:
    s = inputs.scenarios.loc[scenario]
    ov = Overrides(capex_mult=s["capex_mult"], benefit_mult=s["benefit_mult"],
                   energy_price_mult=s["energy_price_mult"])
    rate = inputs.assumptions["discount_rate"] + s["discount_rate_delta"]
    return ov, rate


def appraise(cf: pd.DataFrame, rate: float) -> dict:
    """All appraisal metrics for one cash-flow table."""
    def r(x, nd=4):
        return None if x is None else round(float(x), nd)

    return {
        "npv_mn": round(fin.npv(rate, cf["net"]), 2),
        "irr": r(fin.irr(cf["net"])),
        "payback_yrs": r(fin.payback_period(cf["net"]), 2),
        "disc_payback_yrs": r(fin.discounted_payback_period(rate, cf["net"]), 2),
        "bcr": r(fin.benefit_cost_ratio(rate, cf["benefits"], cf["costs"]), 3),
        "pi": r(fin.profitability_index(rate, cf["net"], cf["capex"]), 3),
        "eac_mn": round(fin.equivalent_annual_value(rate, cf["net"]), 2),
        "pv_benefits_mn": round(fin.npv(rate, cf["benefits"]), 2),
        "pv_costs_mn": round(fin.npv(rate, cf["costs"]), 2),
    }


def appraise_all(inputs: Inputs, scenario: str = "base") -> tuple[pd.DataFrame, pd.DataFrame]:
    """Returns (metrics per project, long cash-flow table)."""
    ov, rate = scenario_overrides(inputs, scenario)
    shares = phasing_lookup(inputs.phasing)
    metrics, flows = [], []
    for _, p in inputs.projects.iterrows():
        cf = build_cashflows(p, shares[p["project_id"]], inputs.assumptions, ov)
        metrics.append({"project_id": p["project_id"], **appraise(cf, rate)})
        cf = cf.assign(project_id=p["project_id"],
                       discount_factor=fin.discount_factors(rate, len(cf)))
        flows.append(cf)
    return pd.DataFrame(metrics), pd.concat(flows, ignore_index=True)


def capex_by_plan_year(projects: pd.DataFrame, phasing: pd.DataFrame,
                       selection: dict[str, int], n_years: int,
                       capex_mult: float = 1.0) -> np.ndarray:
    """Capex needed in each plan year for {project_id: start_year_idx}."""
    shares = phasing_lookup(phasing)
    cap = projects.set_index("project_id")["capex_mn"]
    out = np.zeros(n_years)
    for pid, start in selection.items():
        for j, s in enumerate(shares[pid]):
            if start + j < n_years:
                out[start + j] += cap[pid] * s * capex_mult
    return out
