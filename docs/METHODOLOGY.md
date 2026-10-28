# Model and numerical methodology

## Default factors

For obligor `i` in sector `s`, the latent asset variable is:

```text
A_i = sqrt(rho_i) [sqrt(g) Z_global + sqrt(1-g) Z_sector_s]
      + sqrt(1-rho_i) epsilon_i
default_i = (A_i < Phi_inverse(PD_i))
```

All drivers are independent standard normals before being combined. `g` is the global share of the systematic component; it is not the total asset correlation. The latent variable has unit variance, so each obligor's marginal default probability is its scenario PD. Rho describes an asset-factor loading parameter, not default-indicator correlation.

The model has a global factor and a sector factor. It is not the Basel asymptotic single-factor regulatory capital formula. Across obligors in the same sector, latent covariance is `sqrt(rho_i*rho_j)`; across sectors it is `g*sqrt(rho_i*rho_j)`.

Loan positions are grouped by obligor before multiplying the default indicator by loss exposure. All positions to one obligor must share sector, PD and rho; their EAD and LGD may differ. Position loss is `default_i × stressed_EAD × stressed_LGD`. PD=0 and PD=1 are exact endpoint cases.

## Scenarios and paired differences

Odds shocks use `PD' = PD*m / (1-PD+PD*m)`. Global and sector odds multipliers multiply; LGD shifts add and are clipped into `[0,1]`; EAD multipliers multiply; rho multipliers multiply and rho is capped at 1. PD endpoints remain endpoints. This is an assumed stress design, not empirical macro calibration.

The first scenario is the comparison reference. A SeedSequence spawns three PCG64 streams with sorted sector and obligor identities. The default mode stores one immutable driver bank. Optional streaming regenerates those same streams for every scenario. Neither the execution mode nor chunk size changes the random draws on the checked numerical build.

For pathwise difference `delta_k = loss_s,k - loss_reference,k`, report its mean and `std(delta, ddof=1)/sqrt(N)`. This is a Monte Carlo error estimate for the difference in means. Neither it nor the mean-loss SE estimates uncertainty in VaR/ES, credit parameters or model specification.

If correlation is held fixed and PD, LGD and EAD are increased nonnegatively, losses are pathwise nondecreasing under common drivers. Changing rho changes latent states; pathwise monotonicity no longer follows.

## Expected loss, discrete VaR and ES

Analytic expected loss is `sum(PD'_i × EAD'_position × LGD'_position)`. Correlation does not enter this mean under fixed deterministic severities. Its Monte Carlo estimate is the sample loss mean with ordinary sample SE.

VaR uses the empirical inverse CDF: sort `N` losses in ascending order and take position `ceil(alpha*N)` (one-based). This is an explicit quantile convention, not NumPy's default linear interpolation. The decimal spelling of `alpha` is converted to an exact integer ratio for the rank and tail mass, so a caller's Decimal precision, rounding mode or traps cannot alter the selected tail.

ES averages exactly the worst `q=N*(1-alpha)` path-equivalents. Descending losses get one unit of mass until the boundary; any fractional remainder is included. When several losses equal the boundary value, the remaining mass is divided equally among **all** boundary ties, then weights are normalized by `q`.

For losses `[0,1,2,3,4]` at alpha=0.5, VaR=2 and ES=`(4+3+0.5*2)/2.5=3.2`. For `[0,0,10,10]` at alpha=0.5, VaR=0 and ES=10. Averaging all losses at least VaR would instead yield 5 in the second example. This is why the discrete boundary matters.

For each sector, `ES_contribution_s = sum(portfolio_tail_weight_k × sector_loss_s,k)`. These contributions sum to portfolio ES. They are not standalone sector ES values. Equal boundary tie weights make attribution independent of the ordering of tied portfolio paths.

## Reproducibility and limitations

Reports record the full input portfolio/scenarios/config, random-driver SHA-256 fingerprint and numerical runtime versions. Six artifacts are hashed; semantic verification reruns all paths and reconstructs them. The sample CSV contains the first 200 paths only. Use the pinned NumPy/SciPy versions and a compatible numerical build for byte-for-byte replay. [NumPy's random-stream compatibility policy](https://numpy.org/doc/2.0/reference/random/compatibility.html) also requires matching build, environment and machine; version pins alone do not guarantee identical floating-point driver bytes across platforms.

The committed reports use a Linux driver fingerprint. On Windows with the same Python 3.12.14, NumPy 2.3.5 and SciPy 1.17.0 versions, the saved examples' numerical result tables match, but the regenerated driver fingerprint differs. Strict semantic verification therefore rejects those saved Linux reports on Windows. Windows CI checks their committed file hashes after checkout, then generates new reports and fully verifies both the scenario and precision bundles locally. Linux CI continues to replay the saved reports. The verifier retains exact byte comparisons on every platform; it never substitutes a tolerance for a fingerprint mismatch. `.gitattributes` preserves LF endings so Git's Windows checkout conversion does not alter evidence hashes.

Default memory mode stores all normal drivers; that bank costs
`8 * N * (1 + sectors + obligors)` bytes, before intermediates and outputs.
Streaming removes the retained bank as described below. No dynamic exposure,
migration, recovery correlation, fat-tailed factors, contagion or parameter
uncertainty is modeled. Bootstrap intervals only diagnose Monte Carlo sampling
under the fixed model; they do not cover those omitted uncertainties.

## Driver memory and streaming

`--driver-mode memory|stream` is an execution option for `demo`, `run`, `verify`,
`precision` and `verify-precision`. The Python equivalent is
`make_drivers(loans, paths, seed, mode="stream")`; bundle functions accept
`driver_mode="stream"`. It is not a new financial assumption or saved input
field. Default `memory` remains compatible with earlier invocations.

Streaming first hashes all three streams in the original array/row order using
blocks of at most 4,096 paths, including bounded little-endian byte conversion.
Each scenario then restarts the same seeded streams and generates its blocks.
Sorted identities, shared borrower events and cross-scenario pairing remain
unchanged. Tests compare the streamed bytes with independent full-array draws,
every scenario's loss and sector paths, and all scenario/precision artifacts.
The existing 20,000-path scenario bundle and 300-resample precision bundle
replay exactly in either mode without rewriting their eleven evidence files.

Driver/intermediate memory becomes `O(chunk * obligors)` instead of retaining
`O(N * obligors)` drivers. The report still holds `O(N * sectors * scenarios)`
loss arrays; bootstrap draws and stored resample statistics also consume memory.
Streaming requires a fingerprint pass and generates normal draws again for each
scenario. It can therefore be slower, especially with many scenarios. It is not
an out-of-core tail estimator or a fixed-memory guarantee for the full workflow.
Numerical-build and cross-platform replay limits above continue to apply.

Reproduce the complete six-artifact comparison in separate fresh processes:

```bash
python scripts/benchmark_driver_modes.py --paths 50000 --obligors 400 --sectors 4
```

One local Linux run on 2026-10-28 used Python 3.12.14, NumPy 2.3.5 and SciPy
1.17.0, one synthetic position per obligor and three scenarios:

| Measurement | Memory | Stream |
| --- | ---: | ---: |
| Process peak RSS / MiB | 352.207 | 139.750 |
| Peak traced Python/NumPy allocations / MiB | 307.172 | 82.922 |
| Report generation / seconds | 4.5773 | 5.4441 |

RSS includes imports and other process memory. Traced allocations are a
different measure; they are not added to RSS. Elapsed time excludes startup
and imports. Each worker emits runtime versions and a digest of the six
ordered artifact names and contents. Both modes produced
`bac4d84220128cf3e634e53ce7a33c5dceae087d714dcb62c385e0953b587288`.
The script fails if these digests differ. No speed or capacity guarantee follows
from one synthetic machine-specific trial.

### One-row reduction correction

The previous source could sum a one-row block differently from a larger block
because NumPy chose a different reduction order. A 503-path severe demo at
seed 7 had 90 differing total-loss entries between chunks of 1 and 4,096,
with a maximum absolute difference of `7.450580596923828e-09` CNY. Accumulating
obligor losses in fixed sorted order now makes these paths exactly equal,
including a final block with one path. Both execution modes use this order.

The committed reports keep their existing bytes. A prior custom report with a
single-row final block (including path counts `4096*k + 1`) may retain the old
rounding and fail strict replay. Preserve that evidence and use its original
[source checkpoint](https://github.com/dev-belly/StressAtlas/commit/bc822179ac5e8f6663e62783c44d35c9e4666def)
when investigating; generate any corrected comparison in a new directory.
The verifier does not replace exact comparisons with a tolerance.

## Primary references

- [BCBS: explanatory note on IRB risk weight functions](https://www.bis.org/bcbs/irbriskweight.pdf): Gaussian latent-factor credit-risk context. This project uses its own multi-factor finite-portfolio design.
- [Acerbi and Tasche, Expected Shortfall](https://arxiv.org/abs/cond-mat/0105191): tail-average risk measure and discrete-distribution considerations.
- [SciPy `ndtri`](https://docs.scipy.org/doc/scipy/reference/generated/scipy.special.ndtri.html): inverse standard-normal CDF used in default thresholds.
- [NumPy quantile conventions](https://numpy.org/doc/stable/reference/generated/numpy.quantile.html): distinguishes empirical inverse-CDF quantiles from interpolation choices.
