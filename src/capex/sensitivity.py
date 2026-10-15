"""One-at-a-time sensitivity analysis (tornado chart data)."""
from __future__ import annotations

import pandas as pd

from . import finance as fin
from .cashflows import Overrides, build_cashflows, phasing_lookup
from .db import Inputs

# (label, kind, key, low, high)   kind: 'abs' = absolute value change,
# 'rel' = multiply assumption, 'ov' = Overrides field
DRIVERS = [
    ("Discount rate (±2 pp)", "abs", "discount_rate", -0.02, 0.02),
    ("Energy purchase cost (±20%)", "rel", "energy_purchase_cost", 0.8, 1.2),
    ("Average tariff (±20%)", "rel", "avg_tariff", 0.8, 1.2),
    ("Collection rate (±10 pp)", "abs", "collection_rate", -0.10, 0.10),
    ("Value of lost load (±30%)", "rel", "voll", 0.7, 1.3),
    ("Real benefit growth (0% to 2%)", "set", "benefit_growth", 0.0, 0.02),
    ("Capex (±20%)", "ov", "capex_mult", 1.2, 0.8),
    ("Benefit realisation (±20%)", "ov", "benefit_mult", 0.8, 1.2),
    ("Commissioning delay (+1 yr / none)", "ov", "delay_years", 1, 0),
]


def portfolio_npv(inputs: Inputs, selection: dict[str, int], a: dict[str, float],
                  ov: Overrides) -> float:
    shares = phasing_lookup(inputs.phasing)
    rate = a["discount_rate"]
    proj = inputs.projects.set_index("project_id")
    total = 0.0
    for pid, start in selection.items():
        p = proj.loc[pid].copy()
        p["project_id"] = pid
        cf = build_cashflows(p, shares[pid], a, ov)
        total += fin.npv(rate, cf["net"]) * (1 + rate) ** -start
    return total


def tornado(inputs: Inputs, selection: dict[str, int]) -> pd.DataFrame:
    base_a = dict(inputs.assumptions)
    base = portfolio_npv(inputs, selection, base_a, Overrides())
    rows = []
    for label, kind, key, lo, hi in DRIVERS:
        results = []
        for v in (lo, hi):
            a, ov = dict(base_a), Overrides()
            if kind == "abs":
                a[key] = base_a[key] + v
            elif kind == "rel":
                a[key] = base_a[key] * v
            elif kind == "set":
                a[key] = v
            else:
                ov = Overrides(**{key: v})
            results.append(portfolio_npv(inputs, selection, a, ov))
        rows.append({"driver": label, "npv_low_case": results[0], "npv_high_case": results[1]})
    df = pd.DataFrame(rows)
    df["base_npv"] = base
    df["swing"] = (df["npv_high_case"] - df["npv_low_case"]).abs()
    return df.sort_values("swing", ascending=False).reset_index(drop=True).round(1)
