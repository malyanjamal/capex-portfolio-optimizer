"""Capital Investment Portfolio Optimizer - interactive dashboard.

Run:  streamlit run app/streamlit_app.py
"""
from __future__ import annotations

import sys
from dataclasses import replace
from pathlib import Path

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from capex import db  # noqa: E402
from capex import finance as fin  # noqa: E402
from capex.analysis import appraise_all, scenario_overrides  # noqa: E402
from capex.montecarlo import simulate  # noqa: E402
from capex.optimizer import Problem, efficient_frontier, solve_greedy, solve_milp  # noqa: E402
from capex.pipeline import OBJECTIVE_COLUMN  # noqa: E402
from capex.results import Bundle, portfolio_cashflow, unfunded_reasons  # noqa: E402
from capex.sensitivity import tornado  # noqa: E402

BLUE, ORANGE, AQUA, GREY = "#2a78d6", "#eb6834", "#1baf7a", "#c9c8c3"
st.set_page_config(page_title="Capex Portfolio Optimizer", page_icon="📈", layout="wide")
st.markdown("""<style>
div[data-testid="stMetricValue"] {font-size: 1.55rem;}
.block-container {padding-top: 2rem;}
</style>""", unsafe_allow_html=True)


# ------------------------------------------------------------------ loading
@st.cache_resource(show_spinner="Loading project register from PostgreSQL...")
def base_inputs() -> db.Inputs:
    return db.load_inputs()


def layout(fig: go.Figure, title: str, height: int = 340) -> go.Figure:
    fig.update_layout(title=dict(text=title, x=0, font=dict(size=15)), height=height,
                      margin=dict(l=10, r=10, t=50, b=10), plot_bgcolor="rgba(0,0,0,0)",
                      paper_bgcolor="rgba(0,0,0,0)", hovermode="closest",
                      legend=dict(orientation="h", yanchor="bottom", y=1.0, x=1, xanchor="right"))
    fig.update_xaxes(showgrid=False)
    fig.update_yaxes(gridcolor="rgba(128,128,128,0.2)", zeroline=True,
                     zerolinecolor="rgba(128,128,128,0.5)")
    return fig


try:
    inputs0 = base_inputs()
except Exception as exc:  # noqa: BLE001
    st.error("Could not connect to PostgreSQL. Check DATABASE_URL in your .env file and run "
             "`python -m capex.pipeline all` first.")
    st.exception(exc)
    st.stop()

A0 = inputs0.assumptions
BASE_YEAR = int(A0["base_year"])

# ------------------------------------------------------------------ sidebar
with st.sidebar:
    st.header("Scenario")
    scenario = st.selectbox("Scenario", list(inputs0.scenarios.index),
                            help="Multipliers on capex, benefits, energy price and WACC.")
    objective = st.radio("Optimise on", list(OBJECTIVE_COLUMN), horizontal=True,
                         format_func={"npv": "NPV", "mc_mean": "MC mean",
                                      "mc_p10": "P10 (risk-averse)"}.get)
    st.header("Annual budget (PKR mn)")
    budget = tuple(
        st.number_input(str(int(r.plan_year)), value=float(r.capex_budget_mn), step=10.0,
                        min_value=0.0, key=f"b{r.plan_year}")
        for r in inputs0.budget.itertuples()
    )
    st.header("Key assumptions")
    a = dict(A0)
    a["discount_rate"] = st.slider("Real discount rate", 0.05, 0.20, A0["discount_rate"], 0.005,
                                   format="%.3f")
    a["energy_purchase_cost"] = st.number_input("Energy purchase cost (PKR/kWh)",
                                                value=A0["energy_purchase_cost"], step=1.0)
    a["avg_tariff"] = st.number_input("Average tariff (PKR/kWh)", value=A0["avg_tariff"],
                                      step=1.0)
    a["collection_rate"] = st.slider("Collection rate", 0.5, 1.0, A0["collection_rate"], 0.01)
    a["voll"] = st.number_input("Value of lost load (PKR/kWh)", value=A0["voll"], step=5.0)
    a["ens_societal"] = 1.0 if st.toggle("Value outages at VoLL (societal view)",
                                         value=A0["ens_societal"] >= 0.5) else 0.0
    n_sims = st.select_slider("Monte Carlo simulations", [1000, 2000, 5000, 10000], 5000)


# ------------------------------------------------------------------ compute
@st.cache_data(show_spinner="Appraising projects, simulating risk and optimising...")
def compute(scenario: str, objective: str, budget: tuple, a_items: tuple, n_sims: int):
    inputs = replace(inputs0, assumptions=dict(a_items))
    metrics, flows = appraise_all(inputs, scenario)
    sims = simulate(inputs, scenario, n_sims, seed=42)
    metrics = metrics.merge(sims.summary(), on="project_id")
    _, rate = scenario_overrides(inputs, scenario)
    value = metrics.set_index("project_id")[OBJECTIVE_COLUMN[objective]]
    prob = Problem(inputs.projects, inputs.phasing, value, np.array(budget), rate)
    milp = solve_milp(prob, objective)
    g_npv, g_pi = solve_greedy(prob, "npv", objective), solve_greedy(prob, "pi", objective)
    return inputs, metrics, flows, sims, prob, milp, g_npv, g_pi


inputs, metrics, flows, sims, prob, milp, g_npv, g_pi = compute(
    scenario, objective, budget, tuple(sorted(a.items())), n_sims)


@st.cache_data(show_spinner="Running sensitivity analysis...")
def get_tornado(sel: tuple, a_items: tuple):
    return tornado(replace(inputs0, assumptions=dict(a_items)), dict(sel))


@st.cache_data(show_spinner="Solving the efficient frontier (8 optimisations)...")
def get_frontier(scenario, objective, budget, a_items, n_sims):
    *_, p, _, _, _ = compute(scenario, objective, budget, a_items, n_sims)
    return efficient_frontier(p, objective=objective)


proj = inputs.projects.merge(metrics, on="project_id")
names = proj.set_index("project_id")["name"]
cf_port = portfolio_cashflow(inputs, milp.selection, scenario)
dist = sims.portfolio_npv(milp.selection)
pv_b = fin.npv(prob.rate, cf_port["benefits"]) if len(cf_port) else 0
pv_c = fin.npv(prob.rate, cf_port["capex"] + cf_port["opex"]) if len(cf_port) else 1
port_irr = fin.irr(cf_port["net"]) if len(cf_port) else None

# ------------------------------------------------------------------ header
st.title("Capital Investment Portfolio Optimizer")
st.caption(f"{BASE_YEAR}-{BASE_YEAR + len(budget) - 1} distribution capex plan · "
           f"{len(proj)} candidate projects · scenario **{scenario}** · synthetic data, "
           "PKR million, real terms")
if milp.status != "Optimal":
    st.warning(f"Solver status: {milp.status}. The budget may be too small for the mandatory "
               "projects within their deadlines.")

k = st.columns(6)
k[0].metric("Portfolio NPV", f"{milp.total_value:,.0f}",
            f"{milp.total_value / g_npv.total_value - 1:+.1%} vs rank-by-NPV")
k[1].metric("Capex committed", f"{milp.total_capex:,.0f}",
            f"{milp.total_capex / sum(budget):.0%} of budget", delta_color="off")
k[2].metric("Portfolio IRR", "n/a" if port_irr is None else f"{port_irr:.1%}")
k[3].metric("Benefit-cost ratio", f"{pv_b / pv_c:.2f}x")
k[4].metric("P(NPV > 0)", f"{(dist > 0).mean():.0%}",
            f"P10 {np.percentile(dist, 10):,.0f}", delta_color="off")
k[5].metric("Projects funded", f"{len(milp.selection)} / {len(proj)}")

tabs = st.tabs(["Portfolio", "Project explorer", "Risk & sensitivity", "Budget frontier",
                "SQL analytics", "Save & export"])

# ------------------------------------------------------------------ portfolio
with tabs[0]:
    c1, c2 = st.columns([1.1, 1])
    years = [BASE_YEAR + i for i in range(len(budget))]
    fig = go.Figure()
    fig.add_bar(x=years, y=milp.annual_capex, name="Planned capex", marker_color=BLUE,
                hovertemplate="%{x}: PKR %{y:,.0f} mn<extra></extra>")
    fig.add_scatter(x=years, y=list(budget), name="Budget", mode="markers",
                    marker=dict(symbol="line-ew-open", size=40, line=dict(width=3, color="#555")),
                    hovertemplate="Budget %{x}: PKR %{y:,.0f} mn<extra></extra>")
    c1.plotly_chart(layout(fig, "Annual capex vs budget (PKR mn)"), width="stretch")

    comp = pd.DataFrame({
        "Method": ["Optimiser (MILP)", "Rank by NPV", "Rank by NPV per rupee"],
        "NPV": [milp.total_value, g_npv.total_value, g_pi.total_value],
        "Projects": [len(milp.selection), len(g_npv.selection), len(g_pi.selection)],
    })
    fig = go.Figure(go.Bar(y=comp["Method"], x=comp["NPV"], orientation="h",
                           marker_color=[BLUE, GREY, GREY], text=comp["NPV"].map("{:,.0f}".format),
                           textposition="inside",
                           hovertemplate="%{y}: PKR %{x:,.0f} mn<extra></extra>"))
    fig.update_yaxes(autorange="reversed")
    c2.plotly_chart(layout(fig, "Same budget, different method: portfolio NPV"),
                    width="stretch")

    sel = milp.to_frame().merge(proj, on="project_id")
    sel["Start"] = sel["start_year_idx"] + BASE_YEAR
    st.subheader("Recommended portfolio")
    st.dataframe(
        sel.sort_values(["Start", "npv_mn"], ascending=[True, False])[
            ["project_id", "name", "Start", "capex_mn", "npv_mn", "irr", "payback_yrs",
             "bcr", "prob_npv_pos", "risk_class"]],
        hide_index=True, width="stretch",
        column_config={
            "project_id": "ID", "name": "Project", "capex_mn": st.column_config.NumberColumn(
                "Capex", format="%.0f"),
            "npv_mn": st.column_config.NumberColumn("NPV", format="%.0f"),
            "irr": st.column_config.NumberColumn("IRR", format="percent"),
            "payback_yrs": st.column_config.NumberColumn("Payback (y)", format="%.1f"),
            "bcr": st.column_config.NumberColumn("BCR", format="%.2f"),
            "prob_npv_pos": st.column_config.ProgressColumn("P(NPV>0)", min_value=0,
                                                            max_value=1, format="percent"),
            "risk_class": "Risk"})
    with st.expander("Projects not funded, and why"):
        unf = unfunded_reasons(inputs, metrics, milp.selection)
        st.dataframe(unf[["project_id", "name", "capex_mn", "npv_mn", "prob_npv_pos", "reason"]],
                     hide_index=True, width="stretch")

# ------------------------------------------------------------------ explorer
with tabs[1]:
    cats = st.multiselect("Category", sorted(proj["category_code"].unique()))
    view = proj if not cats else proj[proj["category_code"].isin(cats)]
    fig = go.Figure()
    for funded, color, label in [(True, BLUE, "Funded"), (False, GREY, "Not funded")]:
        d = view[view["project_id"].isin(milp.selection) == funded]
        fig.add_scatter(x=d["capex_mn"], y=d["npv_mn"], mode="markers", name=label,
                        marker=dict(size=11, color=color, line=dict(width=1.5, color="white")),
                        customdata=np.stack([d["project_id"], d["name"],
                                             d["prob_npv_pos"]], axis=1),
                        hovertemplate="<b>%{customdata[0]}</b> %{customdata[1]}<br>"
                                      "Capex %{x:,.0f} · NPV %{y:,.0f}<br>"
                                      "P(NPV>0) %{customdata[2]:.0%}<extra></extra>")
    fig.update_xaxes(title="Capex (PKR mn)")
    fig.update_yaxes(title="NPV (PKR mn)")
    st.plotly_chart(layout(fig, "Every candidate: size vs value", 380), width="stretch")

    pid = st.selectbox("Inspect a project", proj["project_id"],
                       format_func=lambda p: f"{p} - {names[p]}")
    p = proj.set_index("project_id").loc[pid]
    cf = flows[flows["project_id"] == pid].copy()
    cf["year"] = cf["year_idx"] + 1
    cf["cum_disc"] = (cf["net"] * fin.discount_factors(prob.rate, len(cf))).cumsum()
    m = st.columns(5)
    m[0].metric("NPV", f"{p['npv_mn']:,.0f}")
    m[1].metric("IRR", "n/a" if pd.isna(p["irr"]) else f"{p['irr']:.1%}")
    m[2].metric("Disc. payback", "never" if pd.isna(p["disc_payback_yrs"])
                else f"{p['disc_payback_yrs']:.1f} y")
    m[3].metric("BCR", f"{p['bcr']:.2f}")
    m[4].metric("P(NPV>0)", f"{p['prob_npv_pos']:.0%}")
    st.caption(f"{p['notes']}" if isinstance(p["notes"], str) else "")
    c1, c2 = st.columns(2)
    fig = go.Figure()
    streams = [("benefit_tech_loss", "Losses avoided", BLUE), ("benefit_theft", "Theft recovered",
               AQUA), ("benefit_ens", "Outages avoided", "#6250d6"),
               ("benefit_opex", "Opex saved", "#eda100")]
    for col, label, color in streams:
        if cf[col].abs().sum() > 0:
            fig.add_bar(x=cf["year"], y=cf[col], name=label, marker_color=color)
    fig.add_bar(x=cf["year"], y=-(cf["capex"] + cf["opex"]), name="Capex + O&M",
                marker_color=ORANGE)
    fig.add_scatter(x=cf["year"], y=cf["cum_disc"], name="Cumulative discounted net",
                    line=dict(color="#333", width=2))
    fig.update_layout(barmode="relative")
    fig.update_xaxes(title="Project year")
    c1.plotly_chart(layout(fig, "Cash flows (PKR mn)"), width="stretch")
    j = sims.project_ids.index(pid)
    fig = go.Figure(go.Histogram(x=sims.npv[:, j], nbinsx=50, marker_color=BLUE,
                                 marker_line=dict(color="white", width=0.5)))
    fig.add_vline(0, line_color=ORANGE, line_width=2)
    fig.update_xaxes(title="NPV (PKR mn)")
    c2.plotly_chart(layout(fig, f"NPV distribution ({len(sims.npv):,} simulations)"),
                    width="stretch")

# ------------------------------------------------------------------ risk
with tabs[2]:
    c1, c2 = st.columns(2)
    fig = go.Figure(go.Histogram(x=dist, nbinsx=60, marker_color=BLUE,
                                 marker_line=dict(color="white", width=0.5)))
    for q in (10, 50, 90):
        fig.add_vline(np.percentile(dist, q), line_dash="dash" if q != 50 else "solid",
                      annotation_text=f"P{q}: {np.percentile(dist, q):,.0f}",
                      annotation_position="top")
    fig.update_xaxes(title="Portfolio NPV (PKR mn)")
    c1.plotly_chart(layout(fig, "Portfolio NPV distribution", 400), width="stretch")
    c1.caption("Capex overrun, benefit shortfall and delay are drawn per project; one energy-"
               "price shock is shared by all projects, so this risk does not diversify away.")

    torn = get_tornado(tuple(sorted(milp.selection.items())), tuple(sorted(a.items())))
    t = torn.iloc[::-1]
    lo, hi = t["npv_low_case"] - t["base_npv"], t["npv_high_case"] - t["base_npv"]
    fig = go.Figure()
    fig.add_bar(y=t["driver"], x=np.minimum(lo, hi), orientation="h", name="Lowers NPV",
                marker_color=ORANGE)
    fig.add_bar(y=t["driver"], x=np.maximum(lo, hi), orientation="h", name="Raises NPV",
                marker_color=BLUE)
    fig.update_layout(barmode="overlay")
    fig.update_xaxes(title=f"Change in portfolio NPV (base {t['base_npv'].iloc[0]:,.0f})")
    c2.plotly_chart(layout(fig, "Sensitivity (tornado)", 400), width="stretch")

    st.subheader("Scenario comparison")

    @st.cache_data(show_spinner="Optimising every scenario...")
    def scenario_table(budget: tuple, a_items: tuple, current: tuple) -> pd.DataFrame:
        inp = replace(inputs0, assumptions=dict(a_items))
        rows = []
        for sc in inp.scenarios.index:
            msc, _ = appraise_all(inp, sc)
            _, r_sc = scenario_overrides(inp, sc)
            pr = Problem(inp.projects, inp.phasing, msc.set_index("project_id")["npv_mn"],
                         np.array(budget), r_sc)
            pf = solve_milp(pr)
            rows.append({"Scenario": sc, "Portfolio NPV": round(pf.total_value),
                         "Capex": round(pf.total_capex), "Projects": len(pf.selection),
                         "Overlap with current plan":
                             f"{len(set(pf.selection) & set(current))} / {len(current)}"})
        return pd.DataFrame(rows)

    st.dataframe(scenario_table(budget, tuple(sorted(a.items())), tuple(milp.selection)),
                 hide_index=True, width="stretch")
    st.caption("Projects that survive all three scenarios are 'no-regret' investments.")

# ------------------------------------------------------------------ frontier
with tabs[3]:
    st.write("How much NPV could the optimiser deliver at budgets from 40% to 180% (in 20% steps) of the "
             "current plan? Each point is a full re-optimisation.")
    fkey = (scenario, objective, budget, tuple(sorted(a.items())), n_sims)
    if st.button("Compute efficient frontier") or st.session_state.get("frontier_key") == fkey:
        st.session_state["frontier_key"] = fkey
        f = get_frontier(*fkey)
        fig = go.Figure(go.Scatter(x=f["budget_mn"], y=f["portfolio_npv_mn"], mode="lines+markers",
                                   line=dict(color=BLUE, width=2), marker=dict(size=8),
                                   customdata=f[["budget_scale", "n_projects"]],
                                   hovertemplate="Budget %{x:,.0f} (x%{customdata[0]})<br>NPV "
                                                 "%{y:,.0f} · %{customdata[1]} projects"
                                                 "<extra></extra>"))
        fig.add_vline(sum(budget), line_dash="dot", annotation_text="Current budget")
        fig.update_xaxes(title="5-year budget (PKR mn)")
        fig.update_yaxes(title="Portfolio NPV (PKR mn)")
        st.plotly_chart(layout(fig, "Efficient frontier", 420), width="stretch")
        st.dataframe(f, hide_index=True, width="stretch")
        st.caption("Marginal NPV per mn = NPV gained per extra PKR mn of budget. While it is well "
                   "above 0, more budget is itself a value-creating decision.")

# ------------------------------------------------------------------ SQL
with tabs[4]:
    st.write("These views and queries run directly against PostgreSQL (results of the last "
             "saved analysis run).")
    queries = {
        "Category summary (view)": "SELECT * FROM capex.v_category_summary "
                                   "ORDER BY total_npv_mn DESC",
        "Top project per category (window function)":
            "SELECT category, project_id, name, npv_mn, irr\n"
            "FROM capex.v_project_economics\nWHERE npv_rank_in_category = 1\n"
            "ORDER BY npv_mn DESC",
        "Budget utilisation of saved portfolios (view)":
            "SELECT * FROM capex.v_portfolio_annual_capex ORDER BY portfolio_id, plan_year",
        "Cumulative capex by PI rank (running total)":
            "SELECT pi_rank_overall, project_id, name, capex_mn, pi,\n"
            "       SUM(capex_mn) OVER (ORDER BY pi_rank_overall) AS cumulative_capex\n"
            "FROM capex.v_project_economics\nORDER BY pi_rank_overall",
    }
    choice = st.selectbox("Query", list(queries))
    st.code(queries[choice], language="sql")
    try:
        st.dataframe(db.read_sql(queries[choice]), hide_index=True, width="stretch")
    except Exception as exc:  # noqa: BLE001
        st.info(f"Run the pipeline first to populate results. ({exc.__class__.__name__})")

# ------------------------------------------------------------------ save
with tabs[5]:
    st.write("Persist this what-if as an analysis run and portfolio in PostgreSQL, or download "
             "the executive business case.")
    if st.button("Save run to database", type="primary"):
        run_id = db.new_run(scenario, n_sims, 42, notes="dashboard what-if")
        db.write_df(metrics.assign(run_id=run_id), "project_metrics")
        for pf in (milp, g_npv, g_pi):
            pid_ = db.new_portfolio(run_id, pf.method, objective, 1.0, pf.total_value,
                                    pf.total_capex, pf.status)
            db.write_df(pf.to_frame().assign(portfolio_id=pid_), "portfolio_selection")
        st.success(f"Saved as analysis run #{run_id}.")
    if st.button("Build business case PDF"):
        from capex.report import build_report
        with st.spinner("Building PDF..."):
            run = pd.Series({"run_id": 0, "scenario_code": scenario, "n_sims": n_sims})
            b = Bundle(inputs, run, metrics, milp, g_npv, g_pi, sims,
                       get_tornado(tuple(sorted(milp.selection.items())),
                                   tuple(sorted(a.items()))),
                       get_frontier(scenario, objective, budget, tuple(sorted(a.items())),
                                    n_sims), cf_port, prob)
            path = build_report(b)
        st.download_button("Download PDF", path.read_bytes(), file_name=path.name,
                           mime="application/pdf")
