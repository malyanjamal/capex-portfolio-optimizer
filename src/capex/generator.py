"""Synthetic but realistic candidate-project register for a power distribution
utility's 5-year capex plan.

Nothing here is real company data. Cost and benefit ranges are illustrative
and are generated from physical drivers (MWh of losses avoided, MWh of theft
recovered, MWh of outages avoided) so every rupee of benefit can be traced to
an engineering quantity and a documented price assumption.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from . import db

REGIONS = ["North", "South", "East", "West", "Central"]

CATEGORIES = [
    ("FDR", "Feeder reconductoring", "Technical loss reduction",
     "Replace undersized/aged conductor on high-loss 11 kV feeders."),
    ("CAP", "Capacitor bank installation", "Technical loss reduction",
     "Reactive power compensation; cheaper, partial alternative to reconductoring."),
    ("DTR", "Distribution transformer replacement", "Reliability + losses",
     "Replace overloaded or failure-prone distribution transformers."),
    ("ABC", "Aerial bundled cable conversion", "Theft (non-technical loss) reduction",
     "Convert bare LT conductor to insulated bundled cable to stop hooking."),
    ("AMI", "Smart metering (AMI) roll-out", "Theft reduction + opex",
     "Advanced metering with remote reading, tamper alerts and disconnect."),
    ("SCADA", "Grid SCADA / RTU upgrade", "Enabler + reliability",
     "Remote monitoring and control at grid stations; prerequisite for automation."),
    ("DA", "Distribution automation", "Reliability (outage reduction)",
     "Auto-reclosers and fault passage indicators; requires regional SCADA."),
    ("GRD", "Grid station augmentation", "Capacity + reliability",
     "Additional power transformer to remove overload-driven load shedding."),
    ("SAF", "Safety & regulatory compliance", "Compliance",
     "Earthing and LT rehabilitation required by the regulator - must be funded."),
]

# capex range (PKR mn), build years, life, O&M % of capex, risk class, ramp years,
# and per-PKR-mn-of-capex ranges for each annual benefit driver.
TEMPLATES = {
    "FDR": dict(capex=(60, 180), build=1, life=30, om=0.015, risk="low", ramp=1,
                tech=(4, 12)),
    "CAP": dict(capex=(12, 35), build=1, life=15, om=0.02, risk="low", ramp=1,
                tech=(5, 13)),
    "DTR": dict(capex=(20, 80), build=1, life=25, om=0.01, risk="low", ramp=1,
                tech=(2, 5), ens=(0.4, 1.6), opex=(0.02, 0.05)),
    "ABC": dict(capex=(80, 250), build=(1, 2), life=20, om=0.02, risk="high", ramp=2,
                theft=(3, 9)),
    "AMI": dict(capex=(150, 400), build=2, life=12, om=0.04, risk="medium", ramp=2,
                theft=(3, 7), opex=(0.03, 0.06)),
    "SCADA": dict(capex=(100, 220), build=2, life=15, om=0.04, risk="medium", ramp=1,
                  ens=(0.3, 0.9), opex=(0.01, 0.03)),
    "DA": dict(capex=(40, 120), build=1, life=15, om=0.03, risk="medium", ramp=1,
               ens=(1.2, 3.5)),
    "GRD": dict(capex=(300, 700), build=3, life=35, om=0.01, risk="high", ramp=2,
                tech=(0.5, 1.5), ens=(0.6, 1.8)),
    "SAF": dict(capex=(30, 80), build=1, life=20, om=0.01, risk="low", ramp=1,
                ens=(0.1, 0.3), opex=(0.01, 0.03)),
}

PHASING = {1: [1.0], 2: [0.6, 0.4], 3: [0.3, 0.45, 0.25]}

ASSUMPTIONS = [
    ("base_year", 2027, "year", "First year of the capex plan (plan year index 0).", ""),
    ("discount_rate", 0.12, "real, p.a.",
     "Real pre-tax WACC used to discount all cash flows.", "Illustrative"),
    ("energy_purchase_cost", 28.0, "PKR/kWh",
     "Average cost of energy purchased; values technical losses avoided.", "Illustrative"),
    ("avg_tariff", 45.0, "PKR/kWh",
     "Average billed tariff; values theft recovered.", "Illustrative"),
    ("collection_rate", 0.85, "share",
     "Share of newly billed energy actually collected.", "Illustrative"),
    ("voll", 120.0, "PKR/kWh",
     "Value of lost load - societal cost of 1 kWh not served.", "Illustrative"),
    ("ens_societal", 1, "flag",
     "1 = value outages at VoLL (societal view); 0 = utility lost margin only.", ""),
    ("benefit_growth", 0.01, "real, p.a.",
     "Real growth of energy-linked benefits with load growth.", "Illustrative"),
    ("energy_price_volatility", 0.12, "sigma",
     "Lognormal volatility of the shared energy-price factor in Monte Carlo.", "Illustrative"),
]

RISK_PROFILES = [
    # class,  capex low/mode/high, benefit low/mode/high, p(delay 1), p(delay 2)
    ("low", 0.95, 1.00, 1.15, 0.85, 1.00, 1.10, 0.10, 0.02),
    ("medium", 0.95, 1.05, 1.35, 0.70, 0.95, 1.15, 0.20, 0.05),
    ("high", 0.95, 1.10, 1.60, 0.50, 0.90, 1.20, 0.30, 0.10),
]

SCENARIOS = [
    ("base", "Central assumptions", 1.00, 1.00, 1.00, 0.00),
    ("optimistic", "Lower costs, faster benefit capture", 0.92, 1.10, 1.10, -0.01),
    ("pessimistic", "Cost overruns, weaker benefits, higher cost of capital",
     1.20, 0.85, 0.90, 0.02),
]

# Share of total candidate capex that the 5-year budget can fund, and its shape
BUDGET_COVERAGE = 0.50
BUDGET_SHAPE = [0.17, 0.20, 0.22, 0.21, 0.20]


def _u(rng, lo_hi):
    return float(rng.uniform(*lo_hi))


def generate(seed: int = 42) -> dict[str, pd.DataFrame]:
    rng = np.random.default_rng(seed)
    rows, phasing = [], []
    counter = iter(range(1, 1000))

    def add(cat, region, name, **extra):
        t = TEMPLATES[cat]
        pid = f"P{next(counter):03d}"
        capex = round(_u(rng, t["capex"]), 1)
        build = t["build"] if isinstance(t["build"], int) else int(rng.integers(t["build"][0],
                                                                                t["build"][1] + 1))
        row = dict(
            project_id=pid, name=name, category_code=cat, region=region, capex_mn=capex,
            build_years=build, life_years=t["life"], om_pct=t["om"],
            tech_loss_mwh=round(capex * _u(rng, t["tech"]), 1) if "tech" in t else 0.0,
            theft_mwh=round(capex * _u(rng, t["theft"]), 1) if "theft" in t else 0.0,
            ens_mwh=round(capex * _u(rng, t["ens"]), 1) if "ens" in t else 0.0,
            opex_saving_mn=round(capex * _u(rng, t["opex"]), 2) if "opex" in t else 0.0,
            benefit_ramp_years=t["ramp"], risk_class=t["risk"], mandatory=False,
            exclusive_group=None, depends_on=None,
            earliest_start=int(rng.random() < 0.2), latest_start=None, notes=None,
        )
        row.update(extra)
        rows.append(row)
        for i, s in enumerate(PHASING[build]):
            phasing.append(dict(project_id=pid, build_year_idx=i, share=s))
        return pid

    feeders = rng.choice(np.arange(101, 999), size=10, replace=False)
    # Reconductoring; the first four feeders also have a capacitor-bank alternative
    for i, f in enumerate(feeders[:6]):
        region = REGIONS[i % 5]
        grp = f"ALT-F{f}" if i < 4 else None
        add("FDR", region, f"Reconductor Feeder {f} ({region})", exclusive_group=grp,
            notes="Alternative to capacitor bank on same feeder" if grp else None)
        if grp:
            add("CAP", region, f"Capacitor bank Feeder {f} ({region})", exclusive_group=grp,
                notes="Lower-cost alternative to reconductoring")
    for i in range(6):
        region = REGIONS[(i + 2) % 5]
        add("DTR", region, f"Transformer replacement batch {i + 1} ({region})")
    for i in range(6):
        region = REGIONS[(i + 1) % 5]
        add("ABC", region, f"ABC conversion high-loss area {i + 1} ({region})")
    for i, region in enumerate(["Central", "South", "West"]):
        add("AMI", region, f"Smart metering phase {i + 1} ({region})")
    scada = {}
    for region in ["North", "Central", "East"]:
        scada[region] = add("SCADA", region, f"Grid SCADA upgrade ({region})", earliest_start=0)
    for i, region in enumerate(["North", "North", "Central", "East", "East"]):
        add("DA", region, f"Feeder automation cluster {i + 1} ({region})",
            depends_on=scada[region], notes=f"Requires {scada[region]} (regional SCADA)")
    for region in ["East", "West", "North"]:
        add("GRD", region, f"Grid station augmentation ({region})")
    for i, region in enumerate(["South", "West"]):
        add("SAF", region, f"Earthing & LT rehabilitation ({region})", mandatory=True,
            earliest_start=0, latest_start=1,
            notes="Regulatory requirement - must start by plan year 2")

    projects = pd.DataFrame(rows)
    total = projects["capex_mn"].sum()
    budget = pd.DataFrame({
        "plan_year": [2027 + i for i in range(len(BUDGET_SHAPE))],
        "capex_budget_mn": [round(total * BUDGET_COVERAGE * w, -1) for w in BUDGET_SHAPE],
    })
    return {
        "category": pd.DataFrame(CATEGORIES, columns=["category_code", "name",
                                                       "primary_benefit", "description"]),
        "risk_profile": pd.DataFrame(RISK_PROFILES, columns=[
            "risk_class", "capex_low", "capex_mode", "capex_high", "benefit_low",
            "benefit_mode", "benefit_high", "p_delay_1", "p_delay_2"]),
        "assumption": pd.DataFrame(ASSUMPTIONS, columns=["key", "value", "unit",
                                                         "description", "source_note"]),
        "scenario": pd.DataFrame(SCENARIOS, columns=[
            "scenario_code", "description", "capex_mult", "benefit_mult",
            "energy_price_mult", "discount_rate_delta"]),
        "budget": budget,
        "project": projects,
        "project_capex_phasing": pd.DataFrame(phasing),
    }


LOAD_ORDER = ["category", "risk_profile", "assumption", "scenario", "budget", "project",
              "project_capex_phasing"]


def seed_database(seed: int = 42, engine=None) -> dict[str, pd.DataFrame]:
    data = generate(seed)
    for table in LOAD_ORDER:
        db.write_df(data[table], table, engine)
    return data
