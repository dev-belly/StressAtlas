# Monte Carlo tail precision

[Online report](https://dev-belly.github.io/StressAtlas/precision/) ·
[Intervals](../demo/precision/intervals.csv) · [All resample statistics](../demo/precision/resamples.csv)

The original mean MC standard error is not a VaR/ES confidence interval.
This separate paired percentile path bootstrap diagnoses numerical sampling
variation of mean loss, VaR, ES and their differences from the baseline.

## Run and replay

```bash
stressatlas precision --inputs demo/inputs.json --resamples 300 \
  --bootstrap-seed 20261002 --confidence-level 0.95 --out outputs/precision
stressatlas verify-precision --out outputs/precision
```

The model seed remains in input `config`. Bootstrap uses a separate seed,
leaving simulation unchanged. Select a scenario in `report.html`. The
five-artifact bundle uses a `stressatlas/precision/1` manifest and stores
both simulation and precision settings in normalized inputs.

## Method and pairing

One full simulated path is the sampling unit. For each replicate, sample
`N` indices with replacement from `N` paths and apply the SAME indices to
every scenario. Borrower-level defaults and sector/global dependence within
a path remain intact. Individual loans are never independently resampled.

Recompute inverse-ECDF VaR and exact fractional empirical ES for each
resample. Approximate bounds are linearly interpolated percentiles at
`(1-confidence)/2` and `1-(1-confidence)/2`. Bootstrap SE is the sample
standard deviation across resampled estimates. Definitions follow the
[SciPy paired and percentile bootstrap documentation](https://docs.scipy.org/doc/scipy/reference/generated/scipy.stats.bootstrap.html).
No BCa or exact-coverage claim is made for discrete tails.

Each replicate's scenario delta compares separately recomputed statistics:
`VaR(stress)-VaR(base)` is not `VaR(stress-base)`; similarly for ES. A
constant shift produces a constant paired VaR/ES change, checked by tests.
Pairing does not guarantee narrower intervals in every scenario.

Only one bootstrap index vector is kept at a time. The existing driver bank
and full scenario-loss arrays remain in memory. All resampled statistics
and a fingerprint of all index vectors are saved.

## Computed synthetic example

The existing 160-loan / 80-obligor / 20,000-path example has 300 resamples,
bootstrap seed `20261002`, 99% risk level, nominal 95% interval level and
200 ES tail path-equivalents.

| Metric, CNY million | Estimate | Approximate lower | Approximate upper | Bootstrap SE |
| --- | ---: | ---: | ---: | ---: |
| Baseline VaR | 15.140 | 14.684 | 15.700 | 0.275 |
| Baseline ES | 18.476 | 17.764 | 19.116 | 0.327 |
| Severe ES | 57.176 | 56.202 | 58.147 | 0.518 |
| Severe minus baseline ES | 38.699 | 37.926 | 39.467 | 0.384 |

Rounded values come from `demo/precision/intervals.csv`. `resamples.csv`
contains all 1,500 scenario/replicate rows. Verification reruns all 20,000
simulation paths and all 300 resamples, not just the original first-200-path
inspection CSV. Rehashed fabricated intervals, resamples and summaries fail
semantic replay. Use `requirements-replay.txt` for the saved numerical stack.

## Interpretation limits

These approximate intervals describe finite Monte Carlo sampling UNDER
THE GIVEN model parameters. They do not estimate calibration error,
economic-scenario likelihood, future-loss ranges, model risk or capital bounds.

Discrete VaR ties and rare defaults can make percentile coverage unreliable.
Resampling cannot invent tail events absent from the sample. Fewer than
100 tail path-equivalents, fewer than 200 resamples and constant samples
are flagged. Zero spread does not validate assumptions. More paths,
independent simulation seeds and model-sensitivity studies remain separate
checks; formal coverage validation is not claimed.

## 面试追问

**为什么均值标准误不能解释 ES 精度？** 均值和尾部统计量的抽样行为不同，这里每次都重新计算 VaR 和 ES。

**为什么所有情景共用重采样下标？** 分别抽样会破坏原本的路径配对，情景差异中的共同噪声无法正确保留。

**为什么不按贷款抽样？** 一条路径内的贷款损失共享借款人、行业和全局冲击，按贷款抽样会改变相关结构。

**95% 区间是未来损失的概率区间吗？** 不是。风险层面的 99% VaR，与固定模型下估计精度的 95% 近似区间，是两个不同概念。
