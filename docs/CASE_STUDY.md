# Synthetic credit portfolio case study

The saved run has 160 positions to 80 obligors across manufacturing, retail, real estate and technology. Base EAD is CNY 169.6m. Each obligor has two positions with potentially different LGD, but one shared default event. Scenario assumptions are in [inputs.json](../demo/inputs.json); the numerical results are in [scenarios.csv](../demo/scenarios.csv).

## Isolate two different effects

| Scenario | PD odds shock | LGD shift | EAD multiplier | Asset correlation multiplier |
| :--- | :--- | :--- | :--- | :--- |
| Baseline | 1 | 0 | 1 | 1 |
| Recession | 2 globally; extra 1.5 for real estate | +0.08 | 1 | 1 |
| Severe | 4 globally; extra 1.5 for real estate | +0.15 | 1.1 | 1 |
| Independent | 1 | 0 | 1 | 0 |
| High correlation | 1 | 0 | 1 | 2 |

Recession and severe scenarios change marginal credit severity. Independent and high-correlation scenarios only change factor dependence. These should not be described as the same experiment.

All five scenarios share 20,000 normal-driver paths, seed 20261001, global systematic share 0.5, and alpha 0.99. The exact tail contains 200 path-equivalents.

| Scenario | Analytic EL / CNY | Simulated mean / CNY | Mean MC SE / CNY | 99% ES / CNY |
| :--- | ---: | ---: | ---: | ---: |
| Baseline | 3,366,310 | 3,380,274 | 23,686 | 18,476,225 |
| Recession | 8,691,990 | 8,672,394 | 45,591 | 33,601,695 |
| Severe | 19,270,345 | 19,230,306 | 78,720 | 57,175,566 |
| Independent | 3,366,310 | 3,371,452 | 13,807 | 9,621,450 |
| High correlation | 3,366,310 | 3,378,838 | 33,000 | 27,706,338 |

These analytic means agree across the correlation-only experiments, while finite-sample means fluctuate. The tail rises strongly with dependence in this particular demonstration. It is not a theorem that all portfolios or all sampled paths behave monotonically when correlation changes.

The report also shows [sector ES contributions](../demo/sector_es.csv), derived using common portfolio-tail weights. Adding standalone sector ES values would answer a different question and generally would not reproduce portfolio ES.

## Reproduce and inspect

```bash
stressatlas verify --out demo
stressatlas run --inputs demo/inputs.json --out outputs
```

Compare the output manifests and inspect `report.html`. The first 200 saved losses are for inspection only; verifying reruns all 20,000 paths. Report hashes check consistency, not source authenticity or model validation.
