# CurveShift

**Parallel DV01 can be zero while curve risk remains.**

A standalone fixed-income research application inside StressAtlas. Move four
points on an estimated Treasury zero curve, reprice a hypothetical bond ladder,
and compare full revaluation with a linear DV01 approximation.

**[Open the interactive laboratory](https://dev-belly.github.io/StressAtlas/curveshift/)** ·
[中文研究简报](docs/RESEARCH.zh-CN.md) · [Method](docs/METHOD.md) ·
[Sources and upstream credit](ATTRIBUTION.md)

**New: [Give the hedge a budget](https://dev-belly.github.io/StressAtlas/curveshift/hedge/)** —
sparse CVaR optimization with SciPy/HiGHS, gross and per-maturity face caps,
entry costs and a sealed-year evaluation. A $6m gross hedge-face budget reduces
2025 scenario ES from **$71,789.20 to $29,039.70** before a **$1,200** entry cost.
The target is a fixed hypothetical $10m 7Y bond; this is a scenario study,
not realized trading performance. [中文方法推导与复现](docs/HEDGING.zh-CN.md).

## One result worth explaining

At the frozen **2025-12-31** valuation date, a hypothetical USD 2.5m-face long
10Y bond and USD 10.381m-face short 2Y bond have essentially zero combined
parallel DV01. A **2Y −25bp / 10Y +25bp** twist nevertheless produces
**−USD 93,918.70** in full-revaluation P&L. The gross face exposure is USD
12.881m; financing, credit and liquidity effects are outside this illustration.

The separate, long-only USD 10m-face ladder loses **USD 344,453.93** under a
parallel +50bp move. A linear DV01 estimate predicts **USD 357,210.08** of loss.
The USD 12,756.15 difference makes curvature visible instead of treating the
linear approximation as an exact answer.

## Implemented stack

| Component | What it actually does |
| --- | --- |
| QuantLib 1.44 | Semiannual cash-flow schedules and a separate discounting-bond pricing engine. |
| Nelson–Siegel–Svensson 0.5.0 | luphord's credited MIT implementation evaluates the fitted zero curves. |
| NumPy / SciPy | Full cash-flow revaluation, analytical key-rate sensitivities and cross-key gamma. |
| SciPy sparse / HiGHS | A 513-variable, 509-constraint CVaR linear program; gross and per-bond caps, costs, feasibility and primal–dual checks. |
| scikit-learn | PCA fitted on 2016–2024 yield changes, evaluated on 2025 changes. |
| Polars | Typed, date-filtered loading of the frozen curve snapshot. |
| FastAPI / Pydantic | Optional read-only pricing API with finite numeric inputs and source-bound scenarios. |
| uv / pytest / GitHub Actions | Locked dependencies, financial counterexamples, wheel replay and browser checks. |

The source contains **2,499 daily curve estimates** across 2016–2025. The risk
study replays **756 consecutive available curve changes** against fixed current
cash flows, including exact empirical tail-mass ES and 300 moving-block
resamples. This is a historical scenario study, not a historical portfolio-P&L
backtest. The curve observations are revised staff research estimates acquired
through the pinned public mirror in the source ledger, not raw bond quotes.

## Run from the repository root

Python 3.11–3.13. Linux/macOS:

```bash
python -m venv .venv-curve
.venv-curve/bin/python -m pip install ./labs/curveshift
.venv-curve/bin/curveshift report --out output/curve
.venv-curve/bin/curveshift verify output/curve
python -m http.server 8000 --directory output/curve
```

Windows PowerShell:

```powershell
python -m venv .venv-curve
.\.venv-curve\Scripts\python.exe -m pip install .\labs\curveshift
.\.venv-curve\Scripts\curveshift.exe report --out output\curve
.\.venv-curve\Scripts\curveshift.exe verify output\curve
python -m http.server 8000 --directory output\curve
```

Open `http://localhost:8000`. After installation, report generation and browser
interaction need no API key, model service, CDN or external data request.
JavaScript enables the interactive view; the standalone HTML also embeds all
five source/result downloads.

For the locked development environment:

```bash
uv sync --project labs/curveshift --locked --extra dev --extra api
uv run --project labs/curveshift --locked pytest labs/curveshift/tests
uv run --project labs/curveshift --locked curveshift verify labs/curveshift/reports/demo
```

## Take a browser scenario back to Python

Click **Download this scenario**, then use its actual saved path:

```bash
curveshift replay curveshift-scenario-2025-12-31.json --out scenario-result.json
```

The downloaded JSON records the four visible shifts, valuation date and exact
source fingerprint. Unknown fields, nonnumeric/nonfinite shifts, values outside
±300bp and a different source snapshot fail explicitly. Existing destinations
are preserved. The report's address retains the current shifts for sharing.

## Optional API

```bash
python -m pip install './labs/curveshift[api]'
uvicorn curveshift.api:app --host 127.0.0.1 --port 8001
```

Open `http://127.0.0.1:8001/docs`, inspect `GET /health`, then send
`{"shock_bp": [-25, 0, 25, 0]}` to `POST /scenario`. The endpoint uses the same
source and pricing code, with no orders, brokerage connection or live quotes.

## Constrained hedge laboratory

The $10m-face 7Y target remains fixed. Signed 2Y/5Y/10Y/30Y hedge positions
are optimized using 504 changes through 2024-12-31, capped at $3m face per
maturity. Pricing and cash flows are frozen at that date. The 249 subsequent
2025 changes evaluate the fixed hedge; they never enter its objective.

```bash
curveshift hedge --budget-usd 6000000 --cost-bp 2
curveshift hedge-report --out output/hedge
curveshift hedge-verify output/hedge
```

The standalone HTML compares five independently solved budgets, residual key
DV01, custom stresses and downloaded trades. `POST /hedge` accepts
`{"budget_usd": 6000000, "cost_bp": 2}` and optionally the source SHA-256.
The API can solve other budgets up to $10m face. Costs are an illustrative
one-time face-based charge; financing, collateral and borrow costs are excluded.

## Attribution and scope

CurveShift's application, risk workflow, API, research protocol, tests and
dashboard are additions by dev-belly. The NSS model implementation is
**luphord's MIT-licensed library**, not an original algorithm claimed here.
Its copyright notice is retained in [NSS-LICENSE](NSS-LICENSE).
QuantLib retains its BSD license. This project was published as an independently
installable application in this repository; no separate upstream fork repository
is claimed.

[Saved research files](reports/demo) · [Application code](src/curveshift) ·
[Financial tests](tests/test_risk.py) · [Source snapshot](src/curveshift/data/source.json)
