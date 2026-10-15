-- =====================================================================
-- Capital Investment Portfolio Optimizer — database schema
-- All money values are in PKR million, real terms (base-year prices).
-- =====================================================================

DROP SCHEMA IF EXISTS capex CASCADE;
CREATE SCHEMA capex;

-- ---------------------------------------------------------------------
-- Reference data
-- ---------------------------------------------------------------------
CREATE TABLE capex.category (
    category_code     TEXT PRIMARY KEY,
    name              TEXT NOT NULL,
    primary_benefit   TEXT NOT NULL,
    description       TEXT
);

CREATE TABLE capex.risk_profile (
    risk_class        TEXT PRIMARY KEY CHECK (risk_class IN ('low','medium','high')),
    -- Triangular distribution multipliers on base capex (cost overrun risk)
    capex_low         NUMERIC(5,3) NOT NULL,
    capex_mode        NUMERIC(5,3) NOT NULL,
    capex_high        NUMERIC(5,3) NOT NULL,
    -- Triangular distribution multipliers on base benefits (realisation risk)
    benefit_low       NUMERIC(5,3) NOT NULL,
    benefit_mode      NUMERIC(5,3) NOT NULL,
    benefit_high      NUMERIC(5,3) NOT NULL,
    -- Probability of commissioning slipping by 1 or 2 years
    p_delay_1         NUMERIC(4,3) NOT NULL,
    p_delay_2         NUMERIC(4,3) NOT NULL,
    CHECK (capex_low <= capex_mode AND capex_mode <= capex_high),
    CHECK (benefit_low <= benefit_mode AND benefit_mode <= benefit_high),
    CHECK (p_delay_1 + p_delay_2 <= 1)
);

CREATE TABLE capex.assumption (
    key               TEXT PRIMARY KEY,
    value             NUMERIC NOT NULL,
    unit              TEXT,
    description       TEXT NOT NULL,
    source_note       TEXT
);

CREATE TABLE capex.scenario (
    scenario_code       TEXT PRIMARY KEY,
    description         TEXT,
    capex_mult          NUMERIC(5,3) NOT NULL DEFAULT 1,
    benefit_mult        NUMERIC(5,3) NOT NULL DEFAULT 1,
    energy_price_mult   NUMERIC(5,3) NOT NULL DEFAULT 1,
    discount_rate_delta NUMERIC(5,4) NOT NULL DEFAULT 0
);

CREATE TABLE capex.budget (
    plan_year         INT PRIMARY KEY,
    capex_budget_mn   NUMERIC(12,2) NOT NULL CHECK (capex_budget_mn >= 0)
);

-- ---------------------------------------------------------------------
-- Project register (the candidate investments)
-- ---------------------------------------------------------------------
CREATE TABLE capex.project (
    project_id          TEXT PRIMARY KEY,
    name                TEXT NOT NULL,
    category_code       TEXT NOT NULL REFERENCES capex.category,
    region              TEXT NOT NULL,
    capex_mn            NUMERIC(12,2) NOT NULL CHECK (capex_mn > 0),
    build_years         INT NOT NULL CHECK (build_years BETWEEN 1 AND 4),
    life_years          INT NOT NULL CHECK (life_years BETWEEN 5 AND 40),
    om_pct              NUMERIC(5,4) NOT NULL,          -- annual O&M as share of capex
    -- Annual physical benefit drivers at full realisation
    tech_loss_mwh       NUMERIC(12,1) NOT NULL DEFAULT 0, -- technical losses avoided
    theft_mwh           NUMERIC(12,1) NOT NULL DEFAULT 0, -- non-technical losses recovered
    ens_mwh             NUMERIC(12,1) NOT NULL DEFAULT 0, -- energy not served avoided
    opex_saving_mn      NUMERIC(12,2) NOT NULL DEFAULT 0, -- maintenance / field cost avoided
    benefit_ramp_years  INT NOT NULL DEFAULT 1,           -- years to reach full benefit
    risk_class          TEXT NOT NULL REFERENCES capex.risk_profile,
    mandatory           BOOLEAN NOT NULL DEFAULT FALSE,   -- regulatory / safety must-do
    exclusive_group     TEXT,                             -- alternatives: pick at most one
    depends_on          TEXT REFERENCES capex.project,    -- prerequisite project
    earliest_start      INT NOT NULL DEFAULT 0,           -- plan-year index
    latest_start        INT,                              -- deadline (e.g. regulatory), plan-year index
    notes               TEXT
);

-- How capex is spread across the build period (shares sum to 1 per project)
CREATE TABLE capex.project_capex_phasing (
    project_id        TEXT REFERENCES capex.project ON DELETE CASCADE,
    build_year_idx    INT NOT NULL CHECK (build_year_idx >= 0),
    share             NUMERIC(6,4) NOT NULL CHECK (share > 0 AND share <= 1),
    PRIMARY KEY (project_id, build_year_idx)
);

-- ---------------------------------------------------------------------
-- Analysis outputs
-- ---------------------------------------------------------------------
CREATE TABLE capex.analysis_run (
    run_id            SERIAL PRIMARY KEY,
    run_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
    scenario_code     TEXT NOT NULL REFERENCES capex.scenario,
    n_sims            INT NOT NULL,
    seed              INT NOT NULL,
    notes             TEXT
);

CREATE TABLE capex.project_cashflow (
    run_id            INT REFERENCES capex.analysis_run ON DELETE CASCADE,
    project_id        TEXT REFERENCES capex.project ON DELETE CASCADE,
    year_idx          INT NOT NULL,          -- years from project start
    capex             NUMERIC(14,3) NOT NULL,
    opex              NUMERIC(14,3) NOT NULL,
    benefit_tech_loss NUMERIC(14,3) NOT NULL,
    benefit_theft     NUMERIC(14,3) NOT NULL,
    benefit_ens       NUMERIC(14,3) NOT NULL,
    benefit_opex      NUMERIC(14,3) NOT NULL,
    net               NUMERIC(14,3) NOT NULL,
    discount_factor   NUMERIC(10,6) NOT NULL,
    PRIMARY KEY (run_id, project_id, year_idx)
);

CREATE TABLE capex.project_metrics (
    run_id            INT REFERENCES capex.analysis_run ON DELETE CASCADE,
    project_id        TEXT REFERENCES capex.project ON DELETE CASCADE,
    npv_mn            NUMERIC(14,2),
    irr               NUMERIC(8,4),
    payback_yrs       NUMERIC(6,2),
    disc_payback_yrs  NUMERIC(6,2),
    bcr               NUMERIC(8,3),
    pi                NUMERIC(8,3),      -- profitability index = NPV / PV(capex)
    eac_mn            NUMERIC(14,2),     -- equivalent annual NPV
    pv_benefits_mn    NUMERIC(14,2),
    pv_costs_mn       NUMERIC(14,2),
    mc_mean_npv       NUMERIC(14,2),
    mc_p10_npv        NUMERIC(14,2),
    mc_p50_npv        NUMERIC(14,2),
    mc_p90_npv        NUMERIC(14,2),
    prob_npv_pos      NUMERIC(5,4),
    PRIMARY KEY (run_id, project_id)
);

CREATE TABLE capex.portfolio_run (
    portfolio_id      SERIAL PRIMARY KEY,
    run_id            INT NOT NULL REFERENCES capex.analysis_run ON DELETE CASCADE,
    method            TEXT NOT NULL,     -- milp / greedy_npv / greedy_pi
    objective         TEXT NOT NULL,     -- npv / mc_mean / mc_p10
    budget_scale      NUMERIC(5,2) NOT NULL DEFAULT 1,
    total_npv_mn      NUMERIC(14,2),
    total_capex_mn    NUMERIC(14,2),
    solver_status     TEXT,
    solved_at         TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE capex.portfolio_selection (
    portfolio_id      INT REFERENCES capex.portfolio_run ON DELETE CASCADE,
    project_id        TEXT REFERENCES capex.project ON DELETE CASCADE,
    start_year_idx    INT NOT NULL,
    npv_at_start_mn   NUMERIC(14,2),     -- NPV discounted to plan year 0
    PRIMARY KEY (portfolio_id, project_id)
);

CREATE INDEX ix_metrics_project  ON capex.project_metrics (project_id);
CREATE INDEX ix_selection_project ON capex.portfolio_selection (project_id);
CREATE INDEX ix_project_category ON capex.project (category_code);
