"""Budget-constrained portfolio selection and phasing.

Mixed-integer linear programme (solved with CBC via PuLP):

    maximise   sum_p sum_s  V_p * (1+r)^-s * x[p,s]
    subject to
      sum_s x[p,s] <= 1                         each project at most once
      sum_s x[p,s]  = 1    for mandatory p      regulatory / safety must-dos
      x[p,s] only exists for earliest_start <= s <= latest_start (deadlines)
      sum_p sum_s capex[p, y-s] * x[p,s] <= B_y  annual budget, every plan year
      sum_{p in G} sum_s x[p,s] <= 1            mutually exclusive alternatives
      x[c,s] <= sum_{s'<=s} x[parent,s']        prerequisite starts first
      x[p,s] in {0,1}

x[p,s] = 1 means project p starts in plan year s. V_p is the project's value
at its own start (deterministic NPV, Monte Carlo mean, or P10 for a
risk-averse plan). All capex must fall inside the planning window, so a
project cannot dodge the budget by starting in the last year.

Two greedy heuristics (rank by NPV, rank by profitability index) are included
as baselines - they mimic how capex is often prioritised in practice, and
show what the optimiser adds.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd
import pulp

from .cashflows import phasing_lookup


@dataclass
class Portfolio:
    method: str
    objective: str
    budget_scale: float
    status: str
    selection: dict[str, int] = field(default_factory=dict)  # project_id -> start year idx
    value_at_start: dict[str, float] = field(default_factory=dict)
    annual_capex: np.ndarray | None = None
    budget: np.ndarray | None = None

    @property
    def total_value(self) -> float:
        return float(sum(self.value_at_start.values()))

    @property
    def total_capex(self) -> float:
        return float(self.annual_capex.sum()) if self.annual_capex is not None else 0.0

    def to_frame(self) -> pd.DataFrame:
        return pd.DataFrame({
            "project_id": list(self.selection),
            "start_year_idx": list(self.selection.values()),
            "npv_at_start_mn": [round(self.value_at_start[p], 2) for p in self.selection],
        })


@dataclass
class Problem:
    projects: pd.DataFrame          # project register
    phasing: pd.DataFrame
    value: pd.Series                # project_id -> value at project start (PKR mn)
    budget: np.ndarray              # PKR mn per plan year
    rate: float

    @property
    def n_years(self) -> int:
        return len(self.budget)

    def feasible_starts(self, p: pd.Series) -> list[int]:
        last = self.n_years - int(p["build_years"])
        if pd.notna(p.get("latest_start")):
            last = min(last, int(p["latest_start"]))
        return list(range(int(p["earliest_start"]), last + 1))

    def capex_matrix(self) -> dict[tuple[str, int], np.ndarray]:
        """(project, start) -> capex per plan year."""
        shares = phasing_lookup(self.phasing)
        out = {}
        for _, p in self.projects.iterrows():
            pid = p["project_id"]
            for s in self.feasible_starts(p):
                row = np.zeros(self.n_years)
                for j, sh in enumerate(shares[pid]):
                    row[s + j] = p["capex_mn"] * sh
                out[(pid, s)] = row
        return out


def _finish(prob: Problem, method: str, objective: str, scale: float, status: str,
            selection: dict[str, int], capex: dict) -> Portfolio:
    annual = np.zeros(prob.n_years)
    values = {}
    for pid, s in selection.items():
        annual += capex[(pid, s)]
        values[pid] = float(prob.value[pid]) * (1 + prob.rate) ** -s
    return Portfolio(method, objective, scale, status, dict(sorted(selection.items())),
                     values, annual, prob.budget)


def solve_milp(prob: Problem, objective: str = "npv", scale: float = 1.0,
               time_limit: int = 60) -> Portfolio:
    capex = prob.capex_matrix()
    m = pulp.LpProblem("capex_portfolio", pulp.LpMaximize)
    x = {k: pulp.LpVariable(f"x_{k[0]}_{k[1]}", cat="Binary") for k in capex}
    by_project: dict[str, list] = {}
    for (pid, s), var in x.items():
        by_project.setdefault(pid, []).append((s, var))

    m += pulp.lpSum(prob.value[pid] * (1 + prob.rate) ** -s * var for (pid, s), var in x.items())

    proj = prob.projects.set_index("project_id")
    for pid, opts in by_project.items():
        total = pulp.lpSum(v for _, v in opts)
        if proj.loc[pid, "mandatory"]:
            m += total == 1, f"mandatory_{pid}"
        else:
            m += total <= 1, f"once_{pid}"

    for y in range(prob.n_years):
        m += (pulp.lpSum(capex[k][y] * var for k, var in x.items() if capex[k][y] > 0)
              <= prob.budget[y]), f"budget_y{y}"

    for grp, members in proj[proj["exclusive_group"].notna()].groupby("exclusive_group"):
        m += pulp.lpSum(v for pid in members.index for _, v in by_project.get(pid, [])) <= 1, \
            f"exclusive_{grp}"

    for child, parent in proj["depends_on"].dropna().items():
        for s, var in by_project.get(child, []):
            m += var <= pulp.lpSum(v for s2, v in by_project.get(parent, []) if s2 <= s), \
                f"dep_{child}_{s}"

    m.solve(pulp.PULP_CBC_CMD(msg=False, timeLimit=time_limit))
    status = pulp.LpStatus[m.status]
    selection = {pid: s for (pid, s), var in x.items() if var.value() and var.value() > 0.5}
    return _finish(prob, "milp", objective, scale, status, selection, capex)


def solve_greedy(prob: Problem, rank_by: str, objective: str = "npv",
                 scale: float = 1.0) -> Portfolio:
    """Rank projects (mandatory first), then fund each at its earliest start
    that still fits the remaining budget and the logical constraints."""
    capex = prob.capex_matrix()
    proj = prob.projects.set_index("project_id").copy()
    # npv: biggest NPV first. pi: most NPV per rupee of capital first.
    proj["score"] = [prob.value[p] if rank_by == "npv" else prob.value[p] / proj.loc[p, "capex_mn"]
                     for p in proj.index]
    order = proj.sort_values(["mandatory", "score"], ascending=[False, False])

    remaining = prob.budget.astype(float).copy()
    selection: dict[str, int] = {}
    used_groups: set[str] = set()
    for pid, p in order.iterrows():
        if not p["mandatory"] and prob.value[pid] <= 0:
            continue
        if pd.notna(p["exclusive_group"]) and p["exclusive_group"] in used_groups:
            continue
        parent = p["depends_on"]
        for s in prob.feasible_starts(p):
            if pd.notna(parent) and (parent not in selection or selection[parent] > s):
                continue
            if np.all(capex[(pid, s)] <= remaining + 1e-9):
                selection[pid] = s
                remaining -= capex[(pid, s)]
                if pd.notna(p["exclusive_group"]):
                    used_groups.add(p["exclusive_group"])
                break
    missing = [p for p in proj.index[proj["mandatory"]] if p not in selection]
    status = "Optimal-heuristic" if not missing else f"Mandatory unfunded: {missing}"
    return _finish(prob, f"greedy_{rank_by}", objective, scale, status, selection, capex)


def efficient_frontier(prob: Problem, scales=np.arange(0.4, 1.81, 0.2),
                       objective: str = "npv") -> pd.DataFrame:
    """Portfolio value achievable at different budget levels."""
    rows = []
    for sc in scales:
        sub = Problem(prob.projects, prob.phasing, prob.value, prob.budget * sc, prob.rate)
        pf = solve_milp(sub, objective, float(sc))
        rows.append({"budget_scale": round(float(sc), 2),
                     "budget_mn": round(float(sub.budget.sum()), 1),
                     "portfolio_npv_mn": round(pf.total_value, 1),
                     "capex_mn": round(pf.total_capex, 1),
                     "n_projects": len(pf.selection),
                     "status": pf.status})
    df = pd.DataFrame(rows)
    df["marginal_npv_per_mn"] = (df["portfolio_npv_mn"].diff() / df["budget_mn"].diff()).round(3)
    return df
