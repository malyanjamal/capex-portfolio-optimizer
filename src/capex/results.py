"""Assemble everything the report and dashboard need from the database."""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from . import config, db
from . import finance as fin
from .analysis import scenario_overrides
from .cashflows import build_cashflows, phasing_lookup
from .montecarlo import SimResult, simulate
from .optimizer import Problem, efficient_frontier, solve_greedy, solve_milp
from .pipeline import build_problem, latest_metrics
from .sensitivity import tornado


def portfolio_cashflow(inputs: db.Inputs, selection: dict[str, int], scenario: str = "base"
                       ) -> pd.DataFrame:
    """Combined cash flow of a portfolio by plan year (calendar-aligned)."""
    ov, _ = scenario_overrides(inputs, scenario)
    shares = phasing_lookup(inputs.phasing)
    proj = inputs.projects.set_index("project_id")
    frames = []
    for pid, start in selection.items():
        p = proj.loc[pid].copy()
        p["project_id"] = pid
        cf = build_cashflows(p, shares[pid], inputs.assumptions, ov)
        frames.append(cf.assign(plan_year_idx=cf["year_idx"] + start))
    if not frames:
        return pd.DataFrame(columns=["plan_year_idx", "capex", "opex", "benefits", "net"])
    return (pd.concat(frames).groupby("plan_year_idx")[["capex", "opex", "benefits", "net"]]
            .sum().reset_index())


def unfunded_reasons(inputs: db.Inputs, metrics: pd.DataFrame,
                     selection: dict[str, int]) -> pd.DataFrame:
    proj = inputs.projects.merge(metrics, on="project_id")
    out = proj[~proj["project_id"].isin(selection)].copy()
    chosen_groups = set(proj.loc[proj["project_id"].isin(selection), "exclusive_group"].dropna())

    def reason(r):
        if r["npv_mn"] <= 0:
            return "Negative NPV"
        if pd.notna(r["exclusive_group"]) and r["exclusive_group"] in chosen_groups:
            return "Alternative on same asset selected"
        if pd.notna(r["depends_on"]) and r["depends_on"] not in selection:
            return "Prerequisite not worth funding in window"
        return "Outranked within budget"
    out["reason"] = out.apply(reason, axis=1)
    return out.sort_values("npv_mn", ascending=False)


@dataclass
class Bundle:
    inputs: db.Inputs
    run: pd.Series
    metrics: pd.DataFrame
    milp: object
    greedy_npv: object
    greedy_pi: object
    sims: SimResult
    tornado: pd.DataFrame
    frontier: pd.DataFrame
    cashflow: pd.DataFrame
    problem: Problem

    @property
    def base_year(self) -> int:
        return int(self.inputs.assumptions["base_year"])

    def kpis(self) -> dict:
        pf = self.milp
        dist = self.sims.portfolio_npv(pf.selection)
        cf = self.cashflow
        rate = self.problem.rate
        pv_b = fin.npv(rate, cf["benefits"])
        pv_c = fin.npv(rate, cf["capex"] + cf["opex"])
        return {
            "npv": pf.total_value,
            "capex": pf.total_capex,
            "irr": fin.irr(cf["net"]),
            "bcr": pv_b / pv_c if pv_c else None,
            "n_funded": len(pf.selection),
            "n_candidates": len(self.inputs.projects),
            "budget": float(pf.budget.sum()),
            "p10": float(np.percentile(dist, 10)),
            "p50": float(np.percentile(dist, 50)),
            "p90": float(np.percentile(dist, 90)),
            "prob_pos": float((dist > 0).mean()),
            "uplift_vs_npv": pf.total_value / self.greedy_npv.total_value - 1,
            "uplift_vs_pi": pf.total_value / self.greedy_pi.total_value - 1,
            "payback": fin.payback_period(cf["net"]),
        }


def load_bundle(objective: str = "npv", with_frontier: bool = True) -> Bundle:
    inputs = db.load_inputs()
    run_id, metrics = latest_metrics()
    run = db.read_sql("SELECT * FROM capex.analysis_run WHERE run_id=:r", r=run_id).iloc[0]
    prob = build_problem(inputs, metrics, objective, 1.0, run["scenario_code"])
    milp = solve_milp(prob, objective)
    g_npv = solve_greedy(prob, "npv", objective)
    g_pi = solve_greedy(prob, "pi", objective)
    sims = simulate(inputs, run["scenario_code"], int(run["n_sims"]), int(run["seed"]))
    torn = tornado(inputs, milp.selection)
    frontier = efficient_frontier(prob) if with_frontier else pd.DataFrame()
    cf = portfolio_cashflow(inputs, milp.selection, run["scenario_code"])
    return Bundle(inputs, run, metrics, milp, g_npv, g_pi, sims, torn, frontier, cf, prob)


__all__ = ["Bundle", "load_bundle", "portfolio_cashflow", "unfunded_reasons", "config"]
