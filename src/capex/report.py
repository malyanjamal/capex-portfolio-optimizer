"""Executive business-case PDF (reportlab + matplotlib)."""
from __future__ import annotations

import io
from datetime import date
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
from reportlab.lib import colors  # noqa: E402
from reportlab.lib.enums import TA_LEFT  # noqa: E402
from reportlab.lib.pagesizes import A4  # noqa: E402
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet  # noqa: E402
from reportlab.lib.units import mm  # noqa: E402
from reportlab.platypus import (Image, KeepTogether, PageBreak, Paragraph,  # noqa: E402
                                SimpleDocTemplate, Spacer, Table, TableStyle)

from . import config, db  # noqa: E402
from .results import Bundle, load_bundle, unfunded_reasons  # noqa: E402

# Reference palette (validated categorical order) + neutral inks
BLUE, ORANGE, AQUA = "#2a78d6", "#eb6834", "#1baf7a"
INK, INK2, MUTED, GRID, SURFACE = "#0b0b0b", "#52514e", "#8a8984", "#e4e3df", "#fcfcfb"
NAVY = "#14304f"

plt.rcParams.update({
    "font.family": "DejaVu Sans", "font.size": 8.5, "axes.edgecolor": GRID,
    "axes.labelcolor": INK2, "xtick.color": INK2, "ytick.color": INK2,
    "axes.spines.top": False, "axes.spines.right": False, "axes.grid": True,
    "grid.color": GRID, "grid.linewidth": 0.6, "axes.axisbelow": True,
    "axes.titlesize": 9.5, "axes.titleweight": "bold", "axes.titlecolor": INK,
    "axes.titlelocation": "left", "figure.facecolor": "white",
})


def _png(fig, width_mm: float) -> Image:
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=200, bbox_inches="tight")
    plt.close(fig)
    buf.seek(0)
    w, h = fig.get_size_inches()
    img = Image(buf, width=width_mm * mm, height=width_mm * mm * h / w)
    return img


def _mn(x: float) -> str:
    return f"PKR {x:,.0f} mn"


# --------------------------------------------------------------------- charts
def chart_capex_vs_budget(b: Bundle):
    years = [b.base_year + i for i in range(len(b.milp.budget))]
    fig, ax = plt.subplots(figsize=(4.6, 2.6))
    ax.bar(years, b.milp.annual_capex, color=BLUE, width=0.55, label="Planned capex",
           edgecolor="white", linewidth=1)
    ax.plot(years, b.milp.budget, color=INK, marker="_", markersize=22, linestyle="none",
            markeredgewidth=2, label="Budget")
    for x, v, bud in zip(years, b.milp.annual_capex, b.milp.budget):
        ax.text(x, v + bud * 0.02, f"{v / bud:.0%}", ha="center", va="bottom", fontsize=7,
                color=INK2)
    ax.set_title("Annual capex vs budget (PKR mn)")
    ax.legend(frameon=False, loc="upper left", fontsize=7, ncol=2)
    ax.grid(axis="x", visible=False)
    ax.set_ylim(0, max(b.milp.budget) * 1.32)
    return fig


def chart_frontier(b: Bundle):
    f = b.frontier
    fig, ax = plt.subplots(figsize=(4.6, 2.6))
    ax.plot(f["budget_mn"], f["portfolio_npv_mn"], color=BLUE, linewidth=2, marker="o",
            markersize=4)
    cur = f.loc[(f["budget_scale"] - 1).abs().idxmin()]
    ax.scatter([cur["budget_mn"]], [cur["portfolio_npv_mn"]], s=60, color=ORANGE, zorder=3,
               edgecolor="white", linewidth=1.5)
    ax.annotate("Current budget", (cur["budget_mn"], cur["portfolio_npv_mn"]),
                xytext=(8, -14), textcoords="offset points", fontsize=7, color=INK2)
    ax.set_title("Efficient frontier: NPV achievable per budget")
    ax.set_xlabel("5-year capex budget (PKR mn)")
    ax.set_ylabel("Portfolio NPV (PKR mn)")
    return fig


def chart_mc(b: Bundle):
    dist = b.sims.portfolio_npv(b.milp.selection)
    fig, ax = plt.subplots(figsize=(4.6, 2.6))
    ax.hist(dist, bins=50, color=BLUE, alpha=0.85, edgecolor="white", linewidth=0.4)
    top = ax.get_ylim()[1]
    ax.set_ylim(0, top * 1.25)
    for q, lab, ha in [(10, "P10", "right"), (50, "P50", "left"), (90, "P90", "left")]:
        v = np.percentile(dist, q)
        ax.axvline(v, color=INK, linewidth=1, linestyle="--" if q != 50 else "-")
        ax.text(v, top * 1.2, f" {lab} {v:,.0f} ", fontsize=6.5, color=INK2, va="top",
                ha=ha)
    ax.set_title(f"Portfolio NPV risk ({len(dist):,} simulations)")
    ax.set_xlabel("Portfolio NPV (PKR mn)")
    ax.set_yticks([])
    ax.grid(axis="y", visible=False)
    return fig


def chart_tornado(b: Bundle):
    t = b.tornado.iloc[::-1]
    base = t["base_npv"].iloc[0]
    fig, ax = plt.subplots(figsize=(6.8, 3.0))
    y = np.arange(len(t))
    lo = (t["npv_low_case"] - base).to_numpy()
    hi = (t["npv_high_case"] - base).to_numpy()
    worse, better = np.minimum(lo, hi), np.maximum(lo, hi)
    ax.barh(y, worse, color=ORANGE, height=0.6, label="Lowers NPV", edgecolor="white")
    ax.barh(y, better, color=BLUE, height=0.6, label="Raises NPV", edgecolor="white")
    ax.axvline(0, color=INK, linewidth=0.8)
    ax.set_yticks(y, t["driver"], fontsize=7.5)
    ax.set_xlabel(f"Change in portfolio NPV vs base (PKR mn; base = {base:,.0f})")
    ax.set_title("What moves the answer: one-at-a-time sensitivity")
    ax.legend(frameon=False, fontsize=7, loc="lower right")
    ax.grid(axis="y", visible=False)
    return fig


def chart_category(b: Bundle):
    proj = b.inputs.projects.merge(b.metrics, on="project_id")
    proj["funded"] = proj["project_id"].isin(b.milp.selection)
    cat_names = b.inputs.categories.set_index("category_code")["name"]
    g = proj.groupby("category_code").apply(
        lambda d: pd.Series({"funded": d.loc[d.funded, "capex_mn"].sum(),
                             "unfunded": d.loc[~d.funded, "capex_mn"].sum()}),
        include_groups=False)
    g = g.loc[g.sum(axis=1).sort_values().index]
    fig, ax = plt.subplots(figsize=(4.6, 2.6))
    labels = [cat_names[c] for c in g.index]
    ax.barh(labels, g["funded"], color=BLUE, height=0.6, label="Funded", edgecolor="white")
    ax.barh(labels, g["unfunded"], left=g["funded"], color=GRID, height=0.6,
            label="Not funded", edgecolor="white")
    ax.set_title("Candidate capex by category (PKR mn)")
    ax.tick_params(axis="y", labelsize=7)
    ax.legend(frameon=False, fontsize=7, loc="lower right")
    ax.grid(axis="y", visible=False)
    return fig


# ---------------------------------------------------------------------- PDF
def _styles():
    ss = getSampleStyleSheet()
    return {
        "title": ParagraphStyle("t", parent=ss["Title"], fontName="Helvetica-Bold", fontSize=19,
                                textColor=colors.HexColor(NAVY), alignment=TA_LEFT,
                                spaceAfter=2),
        "sub": ParagraphStyle("s", parent=ss["Normal"], fontSize=9.5,
                              textColor=colors.HexColor(INK2), spaceAfter=10),
        "h2": ParagraphStyle("h2", parent=ss["Heading2"], fontName="Helvetica-Bold",
                             fontSize=12, textColor=colors.HexColor(NAVY), spaceBefore=8,
                             spaceAfter=5),
        "body": ParagraphStyle("b", parent=ss["Normal"], fontSize=9, leading=12.5,
                               textColor=colors.HexColor(INK)),
        "small": ParagraphStyle("sm", parent=ss["Normal"], fontSize=7.5, leading=10,
                                textColor=colors.HexColor(INK2)),
        "cell": ParagraphStyle("c", parent=ss["Normal"], fontSize=7.5, leading=9.5),
        "kpi_v": ParagraphStyle("kv", parent=ss["Normal"], fontName="Helvetica-Bold",
                                fontSize=14, leading=17, textColor=colors.HexColor(NAVY)),
        "kpi_l": ParagraphStyle("kl", parent=ss["Normal"], fontSize=7.5, leading=9,
                                textColor=colors.HexColor(INK2)),
    }


def _table(df: pd.DataFrame, widths, st, align_right_from: int = 2):
    data = [[Paragraph(f"<b>{c}</b>", st["cell"]) for c in df.columns]]
    data += [[Paragraph(str(v), st["cell"]) for v in row] for row in df.itertuples(index=False)]
    t = Table(data, colWidths=[w * mm for w in widths], repeatRows=1)
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#eef2f7")),
        ("LINEBELOW", (0, 0), (-1, 0), 0.8, colors.HexColor(NAVY)),
        ("LINEBELOW", (0, 1), (-1, -1), 0.3, colors.HexColor(GRID)),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 2.5), ("BOTTOMPADDING", (0, 0), (-1, -1), 2.5),
    ]))
    return t


def _kpi_row(k: dict, st):
    irr = "n/a" if k["irr"] is None else f"{k['irr']:.1%}"
    tiles = [
        (_mn(k["npv"]), "Portfolio NPV (real, at plan start)"),
        (_mn(k["capex"]), f"Capex committed of {_mn(k['budget'])} budget"),
        (irr, "Portfolio IRR (real)"),
        (f"{k['bcr']:.2f}x", "Benefit-cost ratio"),
        (f"{k['prob_pos']:.0%}", "Probability portfolio NPV > 0"),
        (f"{k['n_funded']} / {k['n_candidates']}", "Projects funded"),
    ]
    cells = [[Paragraph(v, st["kpi_v"]), Paragraph(lbl, st["kpi_l"])] for v, lbl in tiles]
    rows = [cells[:3], cells[3:]]
    data = [[Table([[c[0]], [c[1]]], colWidths=[56 * mm]) for c in r] for r in rows]
    t = Table(data, colWidths=[59 * mm] * 3)
    t.setStyle(TableStyle([
        ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor(GRID)),
        ("INNERGRID", (0, 0), (-1, -1), 0.5, colors.HexColor(GRID)),
        ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor(SURFACE)),
        ("TOPPADDING", (0, 0), (-1, -1), 4), ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]))
    return t


def _footer(canvas, doc):
    canvas.saveState()
    canvas.setFont("Helvetica", 7)
    canvas.setFillColor(colors.HexColor(MUTED))
    canvas.drawString(15 * mm, 9 * mm, "Capital Investment Portfolio Optimizer - synthetic "
                      "data, illustrative assumptions. Values in PKR million, real terms.")
    canvas.drawRightString(195 * mm, 9 * mm, f"Page {doc.page}")
    canvas.restoreState()


def build_report(bundle: Bundle | None = None, path: Path | None = None) -> Path:
    b = bundle or load_bundle()
    k = b.kpis()
    st = _styles()
    path = path or config.REPORTS_DIR / "capex_business_case.pdf"
    path.parent.mkdir(parents=True, exist_ok=True)
    by = b.base_year
    names = b.inputs.projects.set_index("project_id")["name"]
    top = b.tornado.iloc[0]
    mand = b.inputs.projects.loc[b.inputs.projects["mandatory"], "project_id"].tolist()

    story = [
        Paragraph(f"{by}-{by + len(b.milp.budget) - 1} Distribution Capex Plan", st["title"]),
        Paragraph(f"Business case for the recommended investment portfolio &middot; "
                  f"scenario: <b>{b.run['scenario_code']}</b> &middot; "
                  + (f"analysis run #{int(b.run['run_id'])}" if int(b.run["run_id"])
                     else "dashboard what-if")
                  + f" &middot; {date.today():%d %B %Y}", st["sub"]),
        _kpi_row(k, st),
        Paragraph("Recommendation", st["h2"]),
        Paragraph(
            f"Fund <b>{k['n_funded']} of {k['n_candidates']}</b> candidate projects, committing "
            f"<b>{_mn(k['capex'])}</b> against a five-year budget of {_mn(k['budget'])}. The "
            f"portfolio returns an expected NPV of <b>{_mn(k['npv'])}</b> "
            f"(benefit-cost ratio {k['bcr']:.2f}x"
            + (f", simple payback {k['payback']:.1f} years from plan start" if k["payback"]
               else "") + ").", st["body"]),
        Spacer(1, 4),
        Paragraph(
            f"<b>Why this set, not a ranked list.</b> The selection and start years were chosen "
            f"by a mixed-integer optimisation that respects every year's budget, mutually "
            f"exclusive alternatives, prerequisite projects and regulatory deadlines at the "
            f"same time. It delivers <b>{k['uplift_vs_npv']:+.1%}</b> more NPV than funding "
            f"projects in order of NPV, and <b>{k['uplift_vs_pi']:+.1%}</b> more than ranking by "
            f"NPV per rupee of capex, using the same budget.", st["body"]),
        Spacer(1, 4),
        Paragraph(
            f"<b>Risk.</b> Across {int(b.run['n_sims']):,} Monte Carlo simulations of cost "
            f"overruns, benefit shortfalls, commissioning delays and a shared energy-price "
            f"shock, portfolio NPV ranges from {_mn(k['p10'])} (P10) to {_mn(k['p90'])} (P90), "
            f"and is positive in <b>{k['prob_pos']:.0%}</b> of outcomes. The single largest "
            f"driver is <b>{top['driver'].split(' (')[0].lower()}</b>, which swings NPV by "
            f"{_mn(top['swing'])}.", st["body"]),
        Spacer(1, 4),
        Paragraph(
            f"<b>Compliance.</b> {len(mand)} regulatory project(s) "
            f"({', '.join(mand)}) are NPV-negative but mandatory; they are funded within their "
            f"deadlines and their cost is absorbed by the rest of the portfolio.", st["body"]),
        Spacer(1, 6),
        Table([[_png(chart_capex_vs_budget(b), 88), _png(chart_mc(b), 88)]],
              colWidths=[92 * mm, 92 * mm]),
        PageBreak(),
        Paragraph("Recommended portfolio", st["h2"]),
    ]

    sel = b.milp.to_frame().merge(b.metrics, on="project_id").merge(
        b.inputs.projects[["project_id", "category_code", "capex_mn", "risk_class"]])
    sel = sel.sort_values(["start_year_idx", "npv_mn"], ascending=[True, False])
    tbl = pd.DataFrame({
        "ID": sel["project_id"],
        "Project": [names[p] for p in sel["project_id"]],
        "Start": sel["start_year_idx"] + by,
        "Capex": sel["capex_mn"].map("{:,.0f}".format),
        "NPV": sel["npv_mn"].map("{:,.0f}".format),
        "IRR": sel["irr"].map(lambda v: "n/a" if pd.isna(v) else f"{v:.0%}"),
        "Payback": sel["payback_yrs"].map(lambda v: "-" if pd.isna(v) else f"{v:.1f} y"),
        "P(NPV>0)": sel["prob_npv_pos"].map("{:.0%}".format),
        "Risk": sel["risk_class"],
    })
    story += [_table(tbl, [11, 60, 12, 14, 13, 11, 15, 17, 17], st), Spacer(1, 8),
              Table([[_png(chart_frontier(b), 88), _png(chart_category(b), 88)]],
                    colWidths=[92 * mm, 92 * mm]),
              Paragraph(
                  "The frontier shows the NPV the optimiser can deliver at each budget level. "
                  "Where it flattens, extra budget buys little; where it is steep, a budget "
                  "increase is itself a high-return decision.", st["small"]),
              PageBreak(),
              Paragraph("Sensitivity", st["h2"]),
              _png(chart_tornado(b), 175)]

    unf = unfunded_reasons(b.inputs, b.metrics, b.milp.selection)
    unf_tbl = pd.DataFrame({
        "ID": unf["project_id"], "Project": unf["name"],
        "Capex": unf["capex_mn"].map("{:,.0f}".format),
        "NPV": unf["npv_mn"].map("{:,.0f}".format),
        "P(NPV>0)": unf["prob_npv_pos"].map("{:.0%}".format),
        "Reason not funded": unf["reason"],
    })
    story += [KeepTogether([Paragraph("Projects not funded", st["h2"]),
                            _table(unf_tbl, [11, 62, 15, 15, 17, 53], st)])]

    raw = db.read_sql("SELECT * FROM capex.assumption ORDER BY key")
    used = b.inputs.assumptions  # may include dashboard what-if overrides
    a = pd.DataFrame({"Assumption": raw["key"],
                      "Value": [f"{used.get(key, v):g}" for key, v in
                                zip(raw["key"], raw["value"].astype(float))],
                      "Unit": raw["unit"], "Meaning": raw["description"]})
    story += [PageBreak(), Paragraph("Assumptions", st["h2"]),
              _table(a, [38, 15, 22, 105], st), Spacer(1, 6),
              Paragraph("Methodology and limitations", st["h2"])]
    for txt in [
        "<b>Benefits</b> are derived from physical drivers: technical losses avoided are valued "
        "at energy purchase cost; recovered theft at tariff times collection rate; avoided "
        "outages at the value of lost load (societal view; switchable to utility margin); plus "
        "avoided maintenance cost. All values are real, discounted at a real WACC.",
        "<b>Metrics</b>: NPV, IRR, simple and discounted payback, benefit-cost ratio, "
        "profitability index (NPV / PV capex) and equivalent annual value.",
        "<b>Optimisation</b>: binary choice of project and start year, maximising NPV discounted "
        "to plan start, subject to annual budgets, mandatory projects with deadlines, mutually "
        "exclusive alternatives and prerequisites. Solved with CBC via PuLP. All capex must fall "
        "inside the planning window.",
        "<b>Risk</b>: triangular capex and benefit multipliers and discrete delay probabilities "
        "by risk class; one lognormal energy-price factor shared across projects (systematic "
        "risk). Selection can also be optimised on mean or P10 NPV for a risk-averse plan.",
        "<b>Limitations</b>: synthetic data; no taxes, financing structure or salvage value; "
        "projects are assumed independent except through explicit constraints and the shared "
        "price factor; deferral shifts a project's whole cash flow without changing its "
        "benefits.",
    ]:
        story += [Paragraph(txt, st["body"]), Spacer(1, 3)]

    doc = SimpleDocTemplate(str(path), pagesize=A4, leftMargin=15 * mm, rightMargin=15 * mm,
                            topMargin=14 * mm, bottomMargin=16 * mm,
                            title="Capex Portfolio Business Case", author="Capex Optimizer")
    doc.build(story, onFirstPage=_footer, onLaterPages=_footer)
    return path
