# Contributing

Small fixes to the model contracts, reports, examples and documentation are welcome.
Start with the [README setup](README.md#run-in-one-minute) and the
[model boundaries](docs/METHODOLOGY.md#reproducibility-and-limitations).

## Report a problem

Use the [bug report template](https://github.com/dev-belly/StressAtlas/issues/new?template=bug_report.md).
Include a minimal synthetic input, exact commands, expected and actual results,
full error output, source commit and Python/NumPy/SciPy versions. Say whether
you generated the bundle locally or are replaying the committed `demo`.
For simulation differences, retain the saved inputs, seed, path count and
manifest runtime information. Public examples should contain synthetic data.

## Make and check a change

Use a focused branch. After the README setup, use an editable install for
development so changes under `src` are included in the checks.

**Linux / macOS**, from the repository root:

```bash
.venv/bin/python -m pip install -e .
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/python -m stressatlas demo --out outputs-check
.venv/bin/python -m stressatlas verify --out outputs-check
.venv/bin/python -m stressatlas precision --inputs outputs-check/inputs.json --resamples 20 --bootstrap-seed 7 --out outputs-check/precision
.venv/bin/python -m stressatlas verify-precision --out outputs-check/precision
```

**Windows PowerShell** uses the same checks with the environment's Python:

```powershell
.\.venv\Scripts\python.exe -m pip install -e .
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
.\.venv\Scripts\python.exe -m stressatlas demo --out outputs-check
.\.venv\Scripts\python.exe -m stressatlas verify --out outputs-check
.\.venv\Scripts\python.exe -m stressatlas precision --inputs outputs-check/inputs.json --resamples 20 --bootstrap-seed 7 --out outputs-check/precision
.\.venv\Scripts\python.exe -m stressatlas verify-precision --out outputs-check/precision
```

Choose an unused output directory. The small bootstrap above checks the workflow;
it does not establish interval coverage or adequate tail precision.

Add a regression for changed behavior and explain the financial assumption or
contract it protects. Documentation-only changes need the relevant links and
commands checked. In a pull request, state the problem, change, checks run and
any platform you could not verify.

## Numerical replay and installed packages

The [CI workflow](.github/workflows/ci.yml) runs contract tests, replays committed
evidence on Linux, and generates fresh reports on Linux and Windows. Its Linux
package check installs a wheel into a new environment and runs it outside the
source checkout, including a fresh simulation and precision replay.

Committed evidence needs `requirements-replay.txt` **and a compatible numerical
build**. The verifier checks hashes and exact regenerated artifacts, including
the driver fingerprint; version pins alone do not guarantee cross-platform
byte equality. On a different build, generate and verify a fresh local bundle.
If committed replay fails, report the environment and mismatch; do not update
hashes or regenerate saved evidence merely to hide it. Changes to saved results
need a documented method/input reason and review.

Synthetic results demonstrate the implementation, not real portfolio calibration
or regulatory capital. Preserve that distinction when changing examples or claims.

## Browser selector check

The `Chrome report selectors` CI job uses the Ubuntu runner's installed Chrome
and matching chromedriver to select CR/LF, NUL and Unicode identities in the
actual report. `python scripts/check_browser.py` reproduces the check where
Chrome and chromedriver are installed; `CHROMEWEBDRIVER` can name the driver's
directory. No extra Python dependency is required. HTML-parser regressions
remain part of the ordinary unit suite on every platform.
