"""PostgreSQL access layer."""
from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache

import pandas as pd
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

from . import config

SCHEMA = "capex"


@lru_cache(maxsize=1)
def get_engine(url: str | None = None) -> Engine:
    return create_engine(url or config.DATABASE_URL, pool_pre_ping=True, future=True)


def apply_sql_files(engine: Engine | None = None) -> None:
    """(Re)create the schema and reporting views. Drops existing capex data."""
    engine = engine or get_engine()
    with engine.begin() as conn:
        for path in (config.SQL_DIR / "01_schema.sql", config.SQL_DIR / "02_views.sql"):
            conn.exec_driver_sql(path.read_text(encoding="utf-8"))


def write_df(df: pd.DataFrame, table: str, engine: Engine | None = None) -> None:
    """Append a DataFrame to a table in the capex schema."""
    engine = engine or get_engine()
    df.to_sql(table, engine, schema=SCHEMA, if_exists="append", index=False, method="multi",
              chunksize=1000)


def read_sql(query: str, engine: Engine | None = None, **params) -> pd.DataFrame:
    engine = engine or get_engine()
    with engine.connect() as conn:
        return pd.read_sql(text(query), conn, params=params)


@dataclass
class Inputs:
    """Everything the analytics layer needs, loaded from Postgres."""
    projects: pd.DataFrame
    phasing: pd.DataFrame
    assumptions: dict[str, float]
    risk_profiles: pd.DataFrame
    scenarios: pd.DataFrame
    budget: pd.DataFrame
    categories: pd.DataFrame

    @classmethod
    def from_frames(cls, d: dict[str, pd.DataFrame]) -> "Inputs":
        """Build Inputs straight from generator output (no database) - used by tests."""
        a = dict(zip(d["assumption"]["key"], d["assumption"]["value"].astype(float)))
        return cls(d["project"].copy(), d["project_capex_phasing"].copy(), a,
                   d["risk_profile"].set_index("risk_class").astype(float),
                   d["scenario"].set_index("scenario_code"), d["budget"].copy(),
                   d["category"].copy())


def load_inputs(engine: Engine | None = None) -> Inputs:
    engine = engine or get_engine()
    projects = read_sql("SELECT * FROM capex.project ORDER BY project_id", engine)
    numeric_cols = ["capex_mn", "om_pct", "tech_loss_mwh", "theft_mwh", "ens_mwh", "opex_saving_mn"]
    projects[numeric_cols] = projects[numeric_cols].astype(float)

    phasing = read_sql("SELECT * FROM capex.project_capex_phasing ORDER BY project_id, build_year_idx",
                       engine)
    phasing["share"] = phasing["share"].astype(float)

    a = read_sql("SELECT key, value FROM capex.assumption", engine)
    assumptions = {k: float(v) for k, v in zip(a["key"], a["value"])}

    risk = read_sql("SELECT * FROM capex.risk_profile", engine).set_index("risk_class").astype(float)
    scen = read_sql("SELECT * FROM capex.scenario", engine).set_index("scenario_code")
    scen = scen.assign(**{c: scen[c].astype(float) for c in
                          ["capex_mult", "benefit_mult", "energy_price_mult", "discount_rate_delta"]})
    budget = read_sql("SELECT * FROM capex.budget ORDER BY plan_year", engine)
    budget["capex_budget_mn"] = budget["capex_budget_mn"].astype(float)
    categories = read_sql("SELECT * FROM capex.category", engine)
    return Inputs(projects, phasing, assumptions, risk, scen, budget, categories)


def new_run(scenario_code: str, n_sims: int, seed: int, notes: str = "",
            engine: Engine | None = None) -> int:
    engine = engine or get_engine()
    with engine.begin() as conn:
        return conn.execute(
            text("INSERT INTO capex.analysis_run (scenario_code, n_sims, seed, notes) "
                 "VALUES (:s, :n, :seed, :notes) RETURNING run_id"),
            {"s": scenario_code, "n": n_sims, "seed": seed, "notes": notes},
        ).scalar_one()


def new_portfolio(run_id: int, method: str, objective: str, budget_scale: float,
                  total_npv: float, total_capex: float, status: str,
                  engine: Engine | None = None) -> int:
    engine = engine or get_engine()
    with engine.begin() as conn:
        return conn.execute(
            text("INSERT INTO capex.portfolio_run "
                 "(run_id, method, objective, budget_scale, total_npv_mn, total_capex_mn, solver_status) "
                 "VALUES (:r, :m, :o, :b, :npv, :capex, :st) RETURNING portfolio_id"),
            {"r": run_id, "m": method, "o": objective, "b": budget_scale,
             "npv": round(total_npv, 2), "capex": round(total_capex, 2), "st": status},
        ).scalar_one()
