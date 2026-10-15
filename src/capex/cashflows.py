"""Turn a project's physical benefit drivers into a yearly cash-flow table.

Benefit valuation (all PKR million, real terms):

* Technical losses avoided   -> energy we no longer need to buy
                                 MWh x energy purchase cost
* Theft (non-technical) recovered -> energy that is now billed and collected
                                 MWh x average tariff x collection rate
* Energy not served avoided  -> utility view: lost margin (tariff - purchase cost)
                                 societal view: value of lost load (VoLL)
* Opex saving                -> maintenance / field work avoided (PKR mn)
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

BENEFIT_COLS = ["benefit_tech_loss", "benefit_theft", "benefit_ens", "benefit_opex"]
ENERGY_PRICE_LINKED = ["benefit_tech_loss", "benefit_theft"]


@dataclass(frozen=True)
class Overrides:
    """Multipliers used by scenarios, sensitivity analysis and Monte Carlo."""
    capex_mult: float = 1.0
    benefit_mult: float = 1.0
    energy_price_mult: float = 1.0
    delay_years: int = 0


def ens_value_per_kwh(a: dict[str, float]) -> float:
    if a.get("ens_societal", 1) >= 0.5:
        return a["voll"]
    return a["avg_tariff"] - a["energy_purchase_cost"]


def build_cashflows(project: pd.Series, shares: np.ndarray, a: dict[str, float],
                    ov: Overrides = Overrides()) -> pd.DataFrame:
    """Yearly cash flows from project start (year 0) to end of asset life."""
    build = int(project["build_years"])
    life = int(project["life_years"])
    ramp = max(int(project["benefit_ramp_years"]), 1)
    commission = build + ov.delay_years
    horizon = commission + life
    t = np.arange(horizon)

    capex_total = project["capex_mn"] * ov.capex_mult
    capex = np.zeros(horizon)
    capex[: len(shares)] = capex_total * np.asarray(shares, dtype=float)

    operating = t >= commission
    k = np.where(operating, t - commission, 0)
    ramp_factor = np.where(operating, np.minimum(1.0, (k + 1) / ramp), 0.0)
    growth = (1 + a["benefit_growth"]) ** t
    scale = ramp_factor * ov.benefit_mult

    to_mn = 1 / 1000.0  # MWh x PKR/kWh -> PKR million
    price = ov.energy_price_mult
    tech = project["tech_loss_mwh"] * a["energy_purchase_cost"] * to_mn * price * scale * growth
    theft = (project["theft_mwh"] * a["avg_tariff"] * a["collection_rate"] * to_mn * price
             * scale * growth)
    ens = project["ens_mwh"] * ens_value_per_kwh(a) * to_mn * scale * growth
    opex_saving = project["opex_saving_mn"] * scale
    opex = np.where(operating, project["om_pct"] * capex_total, 0.0)

    df = pd.DataFrame({
        "year_idx": t,
        "capex": capex,
        "opex": opex,
        "benefit_tech_loss": tech,
        "benefit_theft": theft,
        "benefit_ens": ens,
        "benefit_opex": opex_saving,
    })
    df["benefits"] = df[BENEFIT_COLS].sum(axis=1)
    df["costs"] = df["capex"] + df["opex"]
    df["net"] = df["benefits"] - df["costs"]
    return df


def phasing_lookup(phasing: pd.DataFrame) -> dict[str, np.ndarray]:
    return {pid: g.sort_values("build_year_idx")["share"].to_numpy(dtype=float)
            for pid, g in phasing.groupby("project_id")}
