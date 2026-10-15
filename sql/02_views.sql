-- =====================================================================
-- Reporting views
-- =====================================================================

-- Latest analysis run
CREATE OR REPLACE VIEW capex.v_latest_run AS
SELECT *
FROM capex.analysis_run
ORDER BY run_id DESC
LIMIT 1;

-- Project register joined with the latest economics, ranked within category
CREATE OR REPLACE VIEW capex.v_project_economics AS
SELECT
    p.project_id,
    p.name,
    c.name                                   AS category,
    p.region,
    p.capex_mn,
    p.risk_class,
    p.mandatory,
    m.npv_mn,
    m.irr,
    m.payback_yrs,
    m.disc_payback_yrs,
    m.bcr,
    m.pi,
    m.mc_p10_npv,
    m.mc_p50_npv,
    m.mc_p90_npv,
    m.prob_npv_pos,
    RANK() OVER (PARTITION BY p.category_code ORDER BY m.npv_mn DESC) AS npv_rank_in_category,
    RANK() OVER (ORDER BY m.pi DESC)                                  AS pi_rank_overall
FROM capex.project p
JOIN capex.category c        USING (category_code)
JOIN capex.project_metrics m ON m.project_id = p.project_id
WHERE m.run_id = (SELECT run_id FROM capex.v_latest_run);

-- Category roll-up
CREATE OR REPLACE VIEW capex.v_category_summary AS
SELECT
    category,
    COUNT(*)                                        AS n_projects,
    SUM(capex_mn)                                   AS total_capex_mn,
    SUM(npv_mn)                                     AS total_npv_mn,
    ROUND(SUM(npv_mn) / NULLIF(SUM(capex_mn),0), 3) AS npv_per_capex,
    ROUND(AVG(irr), 4)                              AS avg_irr,
    COUNT(*) FILTER (WHERE npv_mn > 0)              AS n_npv_positive
FROM capex.v_project_economics
GROUP BY category;

-- Capex required per calendar year by each saved portfolio, vs budget
CREATE OR REPLACE VIEW capex.v_portfolio_annual_capex AS
WITH base AS (
    SELECT value::INT AS base_year FROM capex.assumption WHERE key = 'base_year'
),
spend AS (
    SELECT
        s.portfolio_id,
        s.start_year_idx + ph.build_year_idx      AS plan_year_idx,
        SUM(p.capex_mn * ph.share)                AS capex_mn
    FROM capex.portfolio_selection s
    JOIN capex.project p                USING (project_id)
    JOIN capex.project_capex_phasing ph USING (project_id)
    GROUP BY 1, 2
)
SELECT
    sp.portfolio_id,
    pr.method,
    b.base_year + sp.plan_year_idx                      AS plan_year,
    ROUND(sp.capex_mn, 2)                               AS capex_mn,
    ROUND(bu.capex_budget_mn * pr.budget_scale, 2)      AS budget_mn,
    ROUND(sp.capex_mn / NULLIF(bu.capex_budget_mn * pr.budget_scale, 0), 3) AS utilisation
FROM spend sp
CROSS JOIN base b
JOIN capex.portfolio_run pr USING (portfolio_id)
LEFT JOIN capex.budget bu ON bu.plan_year = b.base_year + sp.plan_year_idx;
