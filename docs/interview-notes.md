# Interview notes

## Resume entry (STAR)

**Capital Investment Portfolio Optimizer** | Python, PostgreSQL, PuLP (MILP), Monte Carlo, Streamlit

- **Situation:** Utilities must choose which network investments to fund from far more candidates than the budget allows. In practice this is often done by ranking projects on NPV, which ignores budget timing, alternatives and dependencies between projects.
- **Task:** Build a decision-support tool that appraises candidate projects, quantifies their risk, and selects and schedules the value-maximising portfolio within a five-year budget.
- **Action:**
  - Designed a 12-table PostgreSQL model with reporting views.
  - Built a cash-flow engine that values benefits from engineering drivers (losses, theft, outages, opex).
  - Implemented NPV, IRR, payback and BCR appraisal and a vectorised 5,000-run Monte Carlo simulation.
  - Formulated a mixed-integer programme with annual budgets, mandatory deadlines, mutually exclusive alternatives and prerequisites.
  - Delivered a Streamlit what-if dashboard and an auto-generated business case PDF, with 20 unit tests and CI.
- **Result:** On a 38-project, PKR 5.5 bn candidate set, the optimiser delivered 4.8% more NPV than NPV-ranking for the same budget (PKR 1,648 mn vs 1,572 mn). Risk analysis flagged a project with positive NPV but only a 10% probability of breaking even.

*(Numbers come from the synthetic base case. If you change the generator or seed, re-run the pipeline and update them.)*

## Questions you are likely to get, and short answers

**Why not just rank by NPV, or by NPV per rupee?**
Ranking ignores that the budget is per year, that some projects are alternatives to each other, that some need a prerequisite, and that start years can shift. The MILP handles all of these at once. The repo includes both rankings as baselines to show the gap.

**Why a real discount rate?**
All cash flows are in constant prices, so the discount rate must exclude inflation too. Mixing real cash flows with a nominal rate is a classic error.

**Why value outages at VoLL rather than lost revenue?**
It depends on whose perspective the decision takes. A regulator cares about customers' cost of outages (the societal view). A utility's P&L only sees lost margin. The model supports both, and you can show how the selection changes when you switch.

**Why is P(NPV>0) more useful than NPV alone?**
High-risk projects have skewed outcomes: overruns are larger than underruns, and delays only push benefits later. Their *expected* NPV is below the central-case NPV. The "optimism gap" query in `sql/03_analysis_queries.sql` measures exactly this.

**Why one shared energy-price factor in the simulation?**
If every project's price risk were independent, it would average out across the portfolio and understate portfolio risk. Price is systematic: it moves all projects together.

**What would you add with real data?**
- Calibrate the risk distributions from historical project cost and schedule outturns.
- Add tax and depreciation.
- Model physical interactions between projects on the same feeder.
- Feed in loss figures from network studies instead of generated drivers.
