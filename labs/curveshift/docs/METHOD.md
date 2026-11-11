# Financial and numerical method

## Source, units and dates

The frozen 2016–2025 subset contains 2,499 complete observations. Source rows
with unavailable values or invalid tau parameters are excluded explicitly;
their dates remain in the ledger. There is no forward fill or imputation.
Rows describe the Fed staff's fitted off-the-run nominal Treasury curve.
Historical vintages can be revised, so a calendar cutoff does not establish
historical publication-time availability.

`BETA0`–`BETA3` and `SVENYXX` are percentages and are divided by 100 for
valuation. `TAU1`/`TAU2` remain in years. `SVENYXX` are continuously compounded
**zero** yields; `SVENPYXX` are par yields and are not used for discounting.

NSS reconstruction is compared with all eight stored published tenors for all
2,499 dates. The maximum discrepancy in this snapshot is approximately
0.005333bp, including the source's published rounding/numerical precision.
The source values are not recalibrated to improve the comparison.

An explicit cutoff selects the last available observation on or before that
date, and the selected valuation date is recorded. A date outside the upper
source window fails rather than silently presenting stale estimates as current.
The report's PCA study needs sufficient held-out observations in 2025; the
scenario API can price other available historical dates.

## Hypothetical bond cash flows

The default ladder has four bonds with USD 2.5m face each, maturities of 2, 5,
10 and 30 calendar years from valuation, 4% annual coupons paid semiannually,
unadjusted payment dates, 30/360 Bond Basis coupon accrual, zero settlement lag,
and 100% redemption. Discount time uses Actual/365 Fixed.

These are model instruments without real security identifiers. Short-end cash
flows use the NSS specification below one year; the source's model estimation
does not include Treasury bills. No claim of observed transaction prices or a
complete market-convention pricer is made.

For cash flow `C_i` at time `t_i`, value is
`sum(C_i * exp(-t_i * z(t_i)))`. QuantLib provides the schedule. A separate
NumPy discount sum is checked against `QuantLib::DiscountingBondEngine` over
the same schedule and node discounts. This checks two pricing paths, not two
independently generated schedules. Flat-curve and manually specified coupon
cases supply independent financial expectations.

## Shocks and sensitivities

Four key shifts at 2, 5, 10 and 30 years interpolate linearly in maturity;
the nearest key stays constant outside the endpoints. Their basis functions
sum to one, so equal shifts reproduce a parallel zero-rate change.

`DV01_k = sum(PV_i * t_i * basis_k(t_i)) / 10000` is positive loss per +1bp.
The linear P&L is `-sum(DV01_k * shock_bp_k)`. Cross-key gamma is
`sum(PV_i * t_i^2 * basis_j(t_i) * basis_k(t_i)) / 10000^2`.
Second-order P&L adds half the quadratic form. Full revaluation applies the
shocked rate to every cash flow; it is not approximated by duration.

The hedge example takes one 10Y leg and sizes an opposite 2Y leg to cancel
their **parallel** analytical DV01. Their key-rate vectors still differ.
It is separate from the long-only ladder, has substantial gross face exposure,
and omits financing, credit and liquidity changes. Even a finite parallel move
can leave convexity P&L after first-order risk cancels.

## Historical risk

Use the most recent 757 available source observations at or before valuation,
giving 756 consecutive-available-observation curve changes. Each change is
evaluated at every current cash-flow maturity, added to the valuation-date
curve, and fully repriced. Current cash flows and time do not roll forward.
Holiday/weekend gaps remain consecutive available observations. This is a
historical scenario distribution, not historical portfolio returns or a VaR
coverage backtest.

VaR is the empirical inverse CDF. ES integrates the empirical quantile function
over the upper `(1-confidence)` probability mass; a fractional boundary atom
is included when the tail sample count is nonintegral. Ties and negative losses
remain valid. The 95% ES resample interval uses 300 noncircular moving-block
resamples of length 20, with seed 20261111. It measures sensitivity to this
sample and block rule, not guaranteed predictive coverage.

## Chronological PCA

Use first differences in basis points of the 1/2/3/5/7/10/20/30Y published zero
yields. Fit the mean and three PCA directions on changes ending before 2025;
evaluate changes ending during 2025. A boundary change belongs to its ending
date. There is no per-tenor standardization or held-out fitting.

Loading signs are oriented so each largest absolute loading is positive.
Held-out reconstruction energy is `1 - SSE / sum((X_test - mean_train)^2)`.
It is not a forecasting score. Do not automatically rename the learned modes
as level/slope/curvature without inspecting the loadings.

## Replay

Each report stores source hashes, source rows, conventions, runtime versions,
all losses, risk vectors, research JSON and standalone HTML. Verification checks
complete file membership and hashes, reloads the registered source, recomputes
all numerical results, and rebuilds the CSV and HTML from recorded research.
Numerical comparison allows relative `1e-10` and absolute `1e-6` differences
across compatible floating-point builds; environment metadata records the
original build and is not compared as a numerical result. Changed bytes alone
and coherently rehashed false financial outputs are both tested.

The verifier establishes consistency with the installed model and frozen
source. It does not independently authenticate the original data publisher
or detect an authorized wholesale replacement of source, code and outputs.
