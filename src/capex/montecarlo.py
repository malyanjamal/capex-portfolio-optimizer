"""Monte Carlo simulation of project and portfolio NPV.

Uncertainties modelled per simulation:

* Capex overrun        - triangular multiplier by project risk class
* Benefit realisation  - triangular multiplier by project risk class
* Commissioning delay  - 0 / 1 / 2 years with risk-class probabilities
* Energy price         - ONE lognormal factor shared by every project in a
                         simulation. This is systematic risk: it does not
                         diversify away in a portfolio, which is why the
                         portfolio distribution is wider than a naive
                         sum-of-independent-projects would suggest.

NPV is linear in each multiplier, so we pre-compute present values of each
cash-flow component per delay case and the simulation is pure vector maths
(tens of thousands of runs in well under a second).
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from . import finance as fin
from .analysis import scenario_overrides
from .cashflows import ENERGY_PRICE_LINKED, Overrides, build_cashflows, phasing_lookup
from .db import Inputs

DELAYS = (0, 1, 2)


@dataclass
class SimResult:
    project_ids: list[str]
    npv: np.ndarray            # shape (n_sims, n_projects), NPV at project start
    price_factor: np.ndarray   # shape (n_sims,)
    rate: float

    def summary(self) -> pd.DataFrame:
        q = np.percentile(self.npv, [10, 50, 90], axis=0)
        return pd.DataFrame({
            "project_id": self.project_ids,
            "mc_mean_npv": self.npv.mean(axis=0).round(2),
            "mc_p10_npv": q[0].round(2),
            "mc_p50_npv": q[1].round(2),
            "mc_p90_npv": q[2].round(2),
            "prob_npv_pos": (self.npv > 0).mean(axis=0).round(4),
        })

    def portfolio_npv(self, selection: dict[str, int]) -> np.ndarray:
        """Portfolio NPV per simulation, discounted to plan year 0."""
        idx = {pid: i for i, pid in enumerate(self.project_ids)}
        total = np.zeros(self.npv.shape[0])
        for pid, start in selection.items():
            total += self.npv[:, idx[pid]] * (1 + self.rate) ** -start
        return total


def _component_pvs(inputs: Inputs, ov: Overrides, rate: float) -> dict[str, np.ndarray]:
    """PV of each cash-flow component, per project and delay case."""
    shares = phasing_lookup(inputs.phasing)
    n, nd = len(inputs.projects), len(DELAYS)
    out = {k: np.zeros((nd, n)) for k in ("capex", "opex", "energy", "other")}
    for j, (_, p) in enumerate(inputs.projects.iterrows()):
        for i, d in enumerate(DELAYS):
            cf = build_cashflows(p, shares[p["project_id"]], inputs.assumptions,
                                 Overrides(ov.capex_mult, ov.benefit_mult, ov.energy_price_mult, d))
            df = fin.discount_factors(rate, len(cf))
            energy = cf[ENERGY_PRICE_LINKED].sum(axis=1).to_numpy()
            out["capex"][i, j] = cf["capex"].to_numpy() @ df
            out["opex"][i, j] = cf["opex"].to_numpy() @ df
            out["energy"][i, j] = energy @ df
            out["other"][i, j] = (cf["benefits"].to_numpy() - energy) @ df
    return out


def simulate(inputs: Inputs, scenario: str = "base", n_sims: int = 5000,
             seed: int = 42) -> SimResult:
    ov, rate = scenario_overrides(inputs, scenario)
    pv = _component_pvs(inputs, ov, rate)
    rng = np.random.default_rng(seed)
    projects = inputs.projects
    risk = inputs.risk_profiles.loc[projects["risk_class"]].reset_index(drop=True)
    n = len(projects)

    def tri(low, mode, high):
        """Triangular draws via inverse CDF (also handles low == high)."""
        a, c, b = low.to_numpy(), mode.to_numpy(), high.to_numpy()
        u = rng.random((n_sims, n))
        width = np.where(b > a, b - a, 1.0)
        fc = (c - a) / width
        left = a + np.sqrt(u * width * (c - a))
        right = b - np.sqrt((1 - u) * width * (b - c))
        return np.where(u < fc, left, right)

    capex_m = tri(risk["capex_low"], risk["capex_mode"], risk["capex_high"])
    benefit_m = tri(risk["benefit_low"], risk["benefit_mode"], risk["benefit_high"])

    # Delay: categorical draw per project per simulation
    u = rng.random((n_sims, n))
    p1, p2 = risk["p_delay_1"].to_numpy(), risk["p_delay_2"].to_numpy()
    delay_idx = np.where(u < p2, 2, np.where(u < p1 + p2, 1, 0))

    sigma = inputs.assumptions.get("energy_price_volatility", 0.12)
    price = np.exp(rng.normal(-0.5 * sigma**2, sigma, size=n_sims))  # mean 1

    cols = np.arange(n)
    pv_capex = pv["capex"][delay_idx, cols]
    pv_opex = pv["opex"][delay_idx, cols]
    pv_energy = pv["energy"][delay_idx, cols]
    pv_other = pv["other"][delay_idx, cols]

    npv = (pv_energy * benefit_m * price[:, None] + pv_other * benefit_m
           - (pv_capex + pv_opex) * capex_m)
    return SimResult(projects["project_id"].tolist(), npv, price, rate)
