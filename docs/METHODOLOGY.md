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

The first scenario is the comparison reference. A SeedSequence spawns three PCG64 streams, generating one immutable driver bank with sorted sector and obligor identities. Every scenario reuses that bank. Chunk sizes only change intermediate memory, not the random numbers.

For pathwise difference `delta_k = loss_s,k - loss_reference,k`, report its mean and `std(delta, ddof=1)/sqrt(N)`. This is a Monte Carlo error estimate for the difference in means. Neither it nor the mean-loss SE estimates uncertainty in VaR/ES, credit parameters or model specification.

If correlation is held fixed and PD, LGD and EAD are increased nonnegatively, losses are pathwise nondecreasing under common drivers. Changing rho changes latent states; pathwise monotonicity no longer follows.

## Expected loss, discrete VaR and ES

Analytic expected loss is `sum(PD'_i × EAD'_position × LGD'_position)`. Correlation does not enter this mean under fixed deterministic severities. Its Monte Carlo estimate is the sample loss mean with ordinary sample SE.

VaR uses the empirical inverse CDF: sort `N` losses in ascending order and take position `ceil(alpha*N)` (one-based). This is an explicit quantile convention, not NumPy's default linear interpolation.

ES averages exactly the worst `q=N*(1-alpha)` path-equivalents. Descending losses get one unit of mass until the boundary; any fractional remainder is included. When several losses equal the boundary value, the remaining mass is divided equally among **all** boundary ties, then weights are normalized by `q`.

For losses `[0,1,2,3,4]` at alpha=0.5, VaR=2 and ES=`(4+3+0.5*2)/2.5=3.2`. For `[0,0,10,10]` at alpha=0.5, VaR=0 and ES=10. Averaging all losses at least VaR would instead yield 5 in the second example. This is why the discrete boundary matters.

For each sector, `ES_contribution_s = sum(portfolio_tail_weight_k × sector_loss_s,k)`. These contributions sum to portfolio ES. They are not standalone sector ES values. Equal boundary tie weights make attribution independent of the ordering of tied portfolio paths.

## Reproducibility and limitations

Reports record the full input portfolio/scenarios/config, random-driver SHA-256 fingerprint and numerical runtime versions. Six artifacts are hashed; semantic verification reruns all paths and reconstructs them. The sample CSV contains the first 200 paths only. Use the pinned NumPy/SciPy versions for byte-for-byte numerical replay; arbitrary numerical stacks are not promised identical artifacts.

The bank stores all normal drivers; memory is `O(N*obligors)`. Chunking limits intermediate latent/default arrays, not stored drivers. No dynamic exposure, migration, recovery correlation, fat-tailed factors, contagion, parameter uncertainty or tail-confidence interval is modeled.

## Primary references

- [BCBS: explanatory note on IRB risk weight functions](https://www.bis.org/bcbs/irbriskweight.pdf): Gaussian latent-factor credit-risk context. This project uses its own multi-factor finite-portfolio design.
- [Acerbi and Tasche, Expected Shortfall](https://arxiv.org/abs/cond-mat/0105191): tail-average risk measure and discrete-distribution considerations.
- [SciPy `ndtri`](https://docs.scipy.org/doc/scipy/reference/generated/scipy.special.ndtri.html): inverse standard-normal CDF used in default thresholds.
- [NumPy quantile conventions](https://numpy.org/doc/stable/reference/generated/numpy.quantile.html): distinguishes empirical inverse-CDF quantiles from interpolation choices.
