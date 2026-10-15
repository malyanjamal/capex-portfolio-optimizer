-- =====================================================================
-- Example analytical queries (run in psql / pgAdmin / VS Code after
-- `python -m capex.pipeline all`). Not executed by the pipeline.
-- =====================================================================

-- 1. Which projects create the most value per rupee, and how much budget
--    would a simple "fund the best ratio first" rule consume? (running total)
SELECT pi_rank_overall,
       project_id,
       name,
       capex_mn,
       pi,
       SUM(capex_mn) OVER (ORDER BY pi_rank_overall) AS cumulative_capex_mn
FROM capex.v_project_economics
ORDER BY pi_rank_overall;

-- 2. Optimiser vs heuristics: same budget, how much NPV does each method find?
SELECT pr.portfolio_id,
       pr.method,
       pr.objective,
       pr.total_npv_mn,
       pr.total_capex_mn,
       COUNT(s.project_id) AS n_projects,
       ROUND(pr.total_npv_mn / FIRST_VALUE(pr.total_npv_mn) OVER (
           PARTITION BY pr.run_id, pr.objective ORDER BY pr.total_npv_mn) - 1, 4) AS uplift_vs_worst
FROM capex.portfolio_run pr
LEFT JOIN capex.portfolio_selection s USING (portfolio_id)
GROUP BY pr.portfolio_id
ORDER BY pr.run_id, pr.objective, pr.total_npv_mn DESC;

-- 3. Projects the optimiser funds but a "rank by NPV" approach would not (and vice versa)
WITH latest AS (
    SELECT method, MAX(portfolio_id) AS portfolio_id
    FROM capex.portfolio_run WHERE objective = 'npv' GROUP BY method
),
milp   AS (SELECT project_id FROM capex.portfolio_selection
           WHERE portfolio_id = (SELECT portfolio_id FROM latest WHERE method = 'milp')),
greedy AS (SELECT project_id FROM capex.portfolio_selection
           WHERE portfolio_id = (SELECT portfolio_id FROM latest WHERE method = 'greedy_npv'))
SELECT COALESCE(m.project_id, g.project_id) AS project_id,
       p.name,
       CASE WHEN g.project_id IS NULL THEN 'only optimiser' ELSE 'only rank-by-NPV' END AS funded_by
FROM milp m
FULL OUTER JOIN greedy g USING (project_id)
JOIN capex.project p ON p.project_id = COALESCE(m.project_id, g.project_id)
WHERE m.project_id IS NULL OR g.project_id IS NULL;

-- 4. High deterministic NPV but fragile under uncertainty: the gap between
--    the "single-number" answer and the risk-adjusted one
SELECT project_id, name, risk_class, npv_mn, mc_p10_npv, mc_p50_npv, prob_npv_pos,
       npv_mn - mc_p50_npv AS optimism_gap_mn
FROM capex.v_project_economics
WHERE npv_mn > 0
ORDER BY prob_npv_pos ASC
LIMIT 10;

-- 5. Where does the value come from? Benefit mix by category (PV, latest run)
SELECT c.name AS category,
       ROUND(SUM(cf.benefit_tech_loss * cf.discount_factor), 1) AS pv_losses_avoided,
       ROUND(SUM(cf.benefit_theft     * cf.discount_factor), 1) AS pv_theft_recovered,
       ROUND(SUM(cf.benefit_ens       * cf.discount_factor), 1) AS pv_outages_avoided,
       ROUND(SUM(cf.benefit_opex      * cf.discount_factor), 1) AS pv_opex_saved
FROM capex.project_cashflow cf
JOIN capex.project p  USING (project_id)
JOIN capex.category c USING (category_code)
WHERE cf.run_id = (SELECT run_id FROM capex.v_latest_run)
GROUP BY c.name
ORDER BY SUM((cf.benefit_tech_loss + cf.benefit_theft + cf.benefit_ens + cf.benefit_opex)
                  * cf.discount_factor) DESC;

-- 6. Budget head-room per year for the latest optimised portfolio
SELECT plan_year, capex_mn, budget_mn, budget_mn - capex_mn AS headroom_mn, utilisation
FROM capex.v_portfolio_annual_capex
WHERE portfolio_id = (SELECT MAX(portfolio_id) FROM capex.portfolio_run WHERE method = 'milp')
ORDER BY plan_year;
