# Input contract

The JSON root requires `portfolio` and `scenarios` arrays plus a `config` object. Optional `data_kind` is `synthetic` or `user_supplied` (default); the label is caller-provided, not audited. Extra columns, non-finite numbers and duplicate JSON keys are rejected.

| Loan field | Rule |
| :--- | :--- |
| `loan_id` | Unique, nonempty trimmed string. |
| `obligor_id`, `sector` | Nonempty trimmed strings. |
| `ead` | Positive finite amount up to `1e15`; all positions use the same currency. |
| `pd`, `lgd`, `rho` | Finite numbers in `[0,1]`; booleans are rejected. |

Positions to the same obligor must share PD, sector and rho. The API does not reconcile borrower identities or convert currencies. Reports label monetary values as CNY; callers must convert their input to CNY before running.

| Scenario field | Rule |
| :--- | :--- |
| `name` | Unique nonempty trimmed string. The first scenario is the comparison reference. |
| `pd_odds_multiplier`, `ead_multiplier` | Positive finite values up to 1000; defaults 1. |
| `lgd_add` | Finite value within `[-1,1]`; default 0. |
| `rho_multiplier` | Finite value within `[0,1000]`; default 1. |
| `sector_shocks` | Array of unique, known sectors plus the same optional shock fields. |

The config requires exactly `paths` (integer >=2), `seed` (uint32 integer), `alpha` (strictly between 0 and 1), `global_share` (within `[0,1]`). Small ES tails are reported with a warning, not silently treated as stable estimates.

See [the full saved input](../demo/inputs.json). JSON scenarios decode into immutable `Scenario`/`SectorShock` contracts; the public Python API uses a tuple of sector shocks.

## Output fields

`scenarios.csv` contains analytic EL, mean loss and its MC SE, inverse-CDF VaR, exact empirical ES, tail path-equivalent mass, alpha, mean delta and paired MC SE, stressed exposure and maximum empirical obligor default rate.

`sector_es.csv` contains each sector's contribution to portfolio ES and its share. The first 200 paths are exported in `loss_sample.csv`, with columns `path` and `loss:<scenario name>`; this prevents a scenario named `path` from overwriting the index.

The driver fingerprint describes the bank of normals plus its obligor/sector universe, path count and seed. It is not proof of model accuracy. All quantities in the example are synthetic.

## Spreadsheet-compatible exports

Formula-shaped text fields and headers beginning with `=`, `+`, `-`, `@`, or control whitespace receive an apostrophe prefix in CSV exports. Numeric losses and negative numeric features keep their values. This affects the spreadsheet representation only: `inputs.json` retains original identifiers and is the authoritative source for replay. The verifier regenerates the same protected CSV representation.
