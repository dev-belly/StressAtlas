# Support for StressAtlas

Read [the model and numerical replay boundaries](docs/METHODOLOGY.md) and [input contract](docs/DATA_CONTRACT.md) first. Generate and verify a fresh report in your own environment:

```bash
stressatlas demo --out outputs/support-check
stressatlas verify --out outputs/support-check
```

The directory is a new report destination; use a separate path for evidence you need to keep. The committed Linux evidence can differ across numerical builds even with the same seed and version pins. On Windows, reproduce with a freshly generated local report.

Use the existing [bug report](https://github.com/dev-belly/StressAtlas/issues/new?template=bug_report.md) or [feature form](https://github.com/dev-belly/StressAtlas/issues/new/choose). Include version/commit, OS/Python, NumPy/SciPy versions, seed, paths, confidence level and driver fingerprint, plus a small synthetic portfolio. State whether the discrepancy concerns analytic EL, simulated loss, paired differences, discrete VaR/ES or attribution. Use [the precision guide](docs/PRECISION.md) for bootstrap questions.

See [CONTRIBUTING.md](CONTRIBUTING.md) for validation. Do not silently refresh saved evidence or numerical pins to make a mismatch disappear. Follow [SECURITY.md](SECURITY.md) for security reports.
