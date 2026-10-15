"""End-to-end pipeline and command-line entry point.

    python -m capex.pipeline all            # rebuild DB, seed, analyse, optimise, report
    python -m capex.pipeline init-db        # (re)create schema and views
    python -m capex.pipeline seed           # load the synthetic project register
    python -m capex.pipeline analyze --scenario pessimistic
    python -m capex.pipeline optimize --objective mc_p10
    python -m capex.pipeline report
"""
from __future__ import annotations

import argparse
import time

import numpy as np
import pandas as pd

from . import config, db
from .analysis import appraise_all, scenario_overrides
from .generator import seed_database
from .montecarlo import simulate
from .optimizer import Problem, solve_greedy, solve_milp

OBJECTIVE_COLUMN = {"npv": "npv_mn", "mc_mean": "mc_mean_npv", "mc_p10": "mc_p10_npv"}
CASHFLOW_COLS = ["run_id", "project_id", "year_idx", "capex", "opex", "benefit_tech_loss",
                 "benefit_theft", "benefit_ens", "benefit_opex", "net", "discount_factor"]


def analyze(scenario: str = "base", n_sims: int = config.DEFAULT_SIMS,
            seed: int = config.DEFAULT_SEED) -> int:
    inputs = db.load_inputs()
    metrics, flows = appraise_all(inputs, scenario)
    sims = simulate(inputs, scenario, n_sims, seed)
    metrics = metrics.merge(sims.summary(), on="project_id")
    run_id = db.new_run(scenario, n_sims, seed, notes="pipeline")
    db.write_df(metrics.assign(run_id=run_id), "project_metrics")
    db.write_df(flows.assign(run_id=run_id)[CASHFLOW_COLS].round(3), "project_cashflow")
    return run_id


def latest_metrics(run_id: int | None = None) -> tuple[int, pd.DataFrame]:
    if run_id is None:
        run_id = int(db.read_sql("SELECT max(run_id) AS r FROM capex.analysis_run")["r"][0])
    m = db.read_sql("SELECT * FROM capex.project_metrics WHERE run_id = :r", r=run_id)
    num = [c for c in m.columns if c not in ("run_id", "project_id")]
    m[num] = m[num].astype(float)
    return run_id, m


def build_problem(inputs: db.Inputs, metrics: pd.DataFrame, objective: str = "npv",
                  budget_scale: float = 1.0, scenario: str = "base",
                  budget: np.ndarray | None = None) -> Problem:
    value = metrics.set_index("project_id")[OBJECTIVE_COLUMN[objective]]
    _, rate = scenario_overrides(inputs, scenario)
    b = inputs.budget["capex_budget_mn"].to_numpy()[: config.PLAN_YEARS] if budget is None \
        else np.asarray(budget, dtype=float)
    return Problem(inputs.projects, inputs.phasing, value, b * budget_scale, rate)


def optimize(objective: str = "npv", budget_scale: float = 1.0, run_id: int | None = None):
    inputs = db.load_inputs()
    run_id, metrics = latest_metrics(run_id)
    scenario = db.read_sql("SELECT scenario_code FROM capex.analysis_run WHERE run_id=:r",
                           r=run_id)["scenario_code"][0]
    prob = build_problem(inputs, metrics, objective, budget_scale, scenario)
    results = [solve_milp(prob, objective, budget_scale),
               solve_greedy(prob, "npv", objective, budget_scale),
               solve_greedy(prob, "pi", objective, budget_scale)]
    for pf in results:
        pid = db.new_portfolio(run_id, pf.method, objective, budget_scale, pf.total_value,
                               pf.total_capex, pf.status)
        db.write_df(pf.to_frame().assign(portfolio_id=pid), "portfolio_selection")
    return results


def init_db() -> None:
    db.apply_sql_files()


def run_all(scenario: str = "base", objective: str = "npv") -> None:
    t0 = time.time()
    init_db()
    data = seed_database(config.DEFAULT_SEED)
    print(f"Seeded {len(data['project'])} candidate projects "
          f"(PKR {data['project']['capex_mn'].sum():,.0f} mn total capex)")
    run_id = analyze(scenario)
    print(f"Analysis run {run_id} complete ({config.DEFAULT_SIMS:,} Monte Carlo simulations)")
    for pf in optimize(objective):
        print(f"  {pf.method:<11} NPV PKR {pf.total_value:>8,.1f} mn | capex "
              f"{pf.total_capex:>8,.1f} mn | {len(pf.selection):>2} projects | {pf.status}")
    from .report import build_report
    path = build_report()
    print(f"Business case written to {path}  ({time.time() - t0:.1f}s)")


def main() -> None:
    ap = argparse.ArgumentParser(description="Capital investment portfolio optimizer")
    ap.add_argument("command", choices=["all", "init-db", "seed", "analyze", "optimize", "report"])
    ap.add_argument("--scenario", default="base", choices=["base", "optimistic", "pessimistic"])
    ap.add_argument("--objective", default="npv", choices=list(OBJECTIVE_COLUMN))
    ap.add_argument("--budget-scale", type=float, default=1.0)
    ap.add_argument("--sims", type=int, default=config.DEFAULT_SIMS)
    args = ap.parse_args()

    if args.command == "all":
        run_all(args.scenario, args.objective)
    elif args.command == "init-db":
        init_db()
    elif args.command == "seed":
        seed_database(config.DEFAULT_SEED)
    elif args.command == "analyze":
        print(f"run_id = {analyze(args.scenario, args.sims)}")
    elif args.command == "optimize":
        for pf in optimize(args.objective, args.budget_scale):
            print(pf.method, round(pf.total_value, 1), pf.status)
    elif args.command == "report":
        from .report import build_report
        print(build_report())


if __name__ == "__main__":
    main()
