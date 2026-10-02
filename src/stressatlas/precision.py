"""Paired path bootstrap: numerical sampling uncertainty under a fixed model."""

from dataclasses import asdict
from hashlib import sha256
from html import escape
from pathlib import Path
import platform

import numpy as np
import scipy

from .bundle import canonical, csv_text, decode, money, read_json
from .core import Simulation, finite, identifier, make_drivers, simulate, tail_metrics

FILES = ("inputs.json", "summary.json", "intervals.csv", "resamples.csv", "report.html")
ENGINE = "stressatlas/precision/1"
METRICS = ("mean_loss", "var", "es")


def settings(resamples=300, seed=20261002, confidence_level=0.95):
    if type(resamples) is not int or not 20 <= resamples <= 10000:
        raise ValueError("resamples must be an integer within [20, 10000]")
    if type(seed) is not int or not 0 <= seed < 2**32:
        raise ValueError("bootstrap seed must be a uint32 integer")
    finite(confidence_level, "confidence_level", upper=1, positive=True)
    if confidence_level == 1:
        raise ValueError("confidence_level must be strictly below 1")
    return dict(resamples=resamples, seed=seed, confidence_level=confidence_level)


def paired_bootstrap(simulations, alpha=0.99, resamples=300, seed=20261002, confidence_level=0.95):
    config = settings(resamples, seed, confidence_level)
    simulations = list(simulations)
    if not simulations or any(not isinstance(row, Simulation) for row in simulations):
        raise ValueError("precision requires at least one Simulation")
    baseline = simulations[0]
    if len({row.name for row in simulations}) != len(simulations):
        raise ValueError("scenario names must be unique")
    for row in simulations:
        identifier(row.name, "scenario name")
        if row.driver_fingerprint != baseline.driver_fingerprint or row.losses.shape != baseline.losses.shape:
            raise ValueError("paired bootstrap requires the same random driver bank and path count")
    paths = baseline.losses.size
    if paths < 2:
        raise ValueError("precision requires at least two paths")
    points = {row.name: tail_metrics(row.losses, alpha) for row in simulations}
    rng = np.random.default_rng(seed)
    digest = sha256(repr((paths, resamples, seed, "PCG64", "paired-path-indices")).encode())
    draws = {(row.name, metric): [] for row in simulations for metric in METRICS}
    deltas = {(row.name, metric): [] for row in simulations for metric in METRICS}
    sample_rows = []
    for resample in range(resamples):
        # One path index vector per replicate, shared by EVERY scenario.
        # Loans are never independently resampled: borrower-level defaults remain intact.
        indices = rng.integers(0, paths, size=paths, dtype=np.int64)
        digest.update(indices.astype("<i8", copy=False).tobytes())
        estimates = {row.name: tail_metrics(row.losses[indices], alpha) for row in simulations}
        for row in simulations:
            output = {"resample": resample, "scenario": row.name}
            for metric in METRICS:
                value = estimates[row.name][metric]
                delta = value - estimates[baseline.name][metric]
                draws[(row.name, metric)].append(value)
                deltas[(row.name, metric)].append(delta)
                output[metric] = money(value)
                output[metric + "_delta"] = money(delta)
            sample_rows.append(output)
    tail = (1 - confidence_level) / 2
    intervals = []
    for row in simulations:
        for metric in METRICS:
            for comparison, distribution, point in (
                ("absolute", draws[(row.name, metric)], points[row.name][metric]),
                ("paired_delta", deltas[(row.name, metric)], points[row.name][metric] - points[baseline.name][metric]),
            ):
                lower, upper = np.quantile(distribution, [tail, 1-tail], method="linear")
                values = (point, lower, upper, np.std(distribution, ddof=1))
                if not all(np.isfinite(value) for value in values):
                    raise ValueError("non-finite bootstrap estimate")
                intervals.append(dict(scenario=row.name, metric=metric, comparison=comparison,
                    estimate=money(point), lower=money(lower), upper=money(upper),
                    bootstrap_se=money(values[3])))
    tail_mass = points[baseline.name]["tail_mass"]
    warnings = []
    if tail_mass < 100:
        warnings.append("Fewer than 100 path-equivalents in the ES tail; percentile intervals may be unstable.")
    if resamples < 200:
        warnings.append("Fewer than 200 resamples; interval endpoints have limited bootstrap resolution.")
    for row in simulations:
        if np.all(row.losses == row.losses[0]):
            warnings.append(f"{row.name}: observed losses are constant; zero bootstrap spread does not validate model assumptions or unseen tail support.")
    return {"simulation_paths": paths, **config, "alpha": alpha, "tail_mass": tail_mass,
        "baseline_scenario": baseline.name, "driver_fingerprint": baseline.driver_fingerprint,
        "resampling_fingerprint": digest.hexdigest(), "method": "paired_path_percentile_bootstrap",
        "endpoint_quantile_method": "linear", "uncertainty_scope": "Monte Carlo sampling under fixed portfolio and model parameters",
        "interval_rows": len(intervals), "intervals": intervals, "warnings": warnings}, sample_rows


def render_precision(summary):
    label = "SYNTHETIC PORTFOLIO" if summary["data_kind"] == "synthetic" else "USER-SUPPLIED PORTFOLIO"
    options = "".join(f'<option>{escape(name)}</option>' for name in summary["scenario_names"])
    names = {"mean_loss": "Mean loss", "var": "VaR", "es": "ES"}
    def rows(comparison):
        return "".join(f'<tr data-scenario="{escape(row["scenario"], quote=True)}">'
            + f'<td>{escape(row["scenario"])}</td><td>{names[row["metric"]]}</td>'
            + "".join(f'<td>{row[key]:,.0f}</td>' for key in ("estimate", "lower", "upper", "bootstrap_se"))
            + "</tr>" for row in summary["intervals"] if row["comparison"] == comparison)
    warnings = "".join(f"<li>{escape(warning)}</li>" for warning in summary["warnings"])
    return f"""<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>StressAtlas · Tail precision</title><style>
*{{box-sizing:border-box}}body{{margin:0;background:#0d1424;color:#edf2fa;font:16px/1.6 system-ui,sans-serif}}main{{max-width:1200px;margin:auto;padding:48px 24px}}h1{{font-size:clamp(34px,6vw,60px);line-height:1.1}}h2{{margin-top:40px}}p,li{{color:#a9b9d2}}.tag,a,code{{color:#f4b76a}}.cards{{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:14px}}.card{{padding:20px;background:#131f35;border:1px solid #2a3955;border-radius:12px}}.value{{display:block;font-size:34px;font-weight:700}}.scroll{{overflow:auto;border:1px solid #2a3955;border-radius:12px}}table{{border-collapse:collapse;width:100%;font-size:13px}}th,td{{padding:12px;border-bottom:1px solid #2a3955;text-align:right;white-space:nowrap}}th:first-child,td:first-child,th:nth-child(2),td:nth-child(2){{text-align:left}}th{{color:#f4c892;background:#192842}}label{{display:block;margin:24px 0}}select{{font:inherit;background:#192842;color:#edf2fa;border:1px solid #475c7a;border-radius:6px;padding:8px}}.hidden{{display:none}}footer{{margin-top:40px;color:#8da0bc}}
</style><main><div class="tag">STRESSATLAS / TAIL PRECISION / {label}</div>
<h1>How stable is<br>the tail estimate?</h1><p>Paired path bootstrap for mean loss, VaR and exact empirical ES. Intervals describe numerical sampling uncertainty under fixed model assumptions.</p>
<div class="cards"><div class="card"><span class="value">{summary['simulation_paths']:,}</span>Full simulation paths</div><div class="card"><span class="value">{summary['resamples']}</span>Paired resamples</div><div class="card"><span class="value">{summary['confidence_level']*100:g}%</span>Approximate percentile interval</div><div class="card"><span class="value">{summary['tail_mass']:g}</span>ES tail path-equivalents</div></div>
<label>Scenario <select id="scenario"><option value="">All scenarios</option>{options}</select></label>
<h2>Absolute risk estimate</h2><p>All amounts are CNY. Bootstrap SE is the sample standard deviation across resampled estimates. These bounds are approximate, especially for discrete VaR and sparse tails.</p>
<div class="scroll"><table><thead><tr><th>Scenario</th><th>Metric</th><th>Estimate</th><th>Lower</th><th>Upper</th><th>Bootstrap SE</th></tr></thead><tbody>{rows('absolute')}</tbody></table></div>
<h2>Change from {escape(summary['baseline_scenario'])}</h2><p>The same resampled path indices are applied to every scenario. Each VaR/ES difference compares two separately recomputed tail statistics; it is not VaR/ES of the pathwise loss difference.</p>
<div class="scroll"><table><thead><tr><th>Scenario</th><th>Metric</th><th>Paired change</th><th>Lower</th><th>Upper</th><th>Bootstrap SE</th></tr></thead><tbody>{rows('paired_delta')}</tbody></table></div>
<h2>Read the limits before interpreting the interval</h2><p>Resampling cannot generate rare losses absent from the simulated sample. Ties, too few tail paths and too few resamples can make percentile coverage unreliable. These intervals do not measure PD/LGD calibration error, future economic risk or model validity.</p><ul>{warnings}</ul>
<h2>Replay the evidence</h2><p><code>stressatlas verify-precision --out demo/precision</code> reruns the entire simulation and bootstrap from saved inputs and both seeds, then compares every estimate, resample and report.</p>
<p><a href="intervals.csv">Interval rows</a> · <a href="resamples.csv">All resample statistics</a> · <a href="inputs.json">Inputs and settings</a> · <a href="summary.json">Summary</a></p><footer>{label} · <a href="https://github.com/dev-belly/StressAtlas/blob/main/docs/PRECISION.md">Method and case study</a></footer></main>
<script>const select=document.getElementById('scenario');select.addEventListener('change',()=>document.querySelectorAll('tr[data-scenario]').forEach(row=>row.classList.toggle('hidden',select.value!==''&&row.dataset.scenario!==select.value)));</script></html>\n"""


def precision_artifacts(data):
    if not isinstance(data, dict):
        raise ValueError("precision inputs must be an object")
    source = dict(data)
    supplied = source.pop("precision", {})
    if not isinstance(supplied, dict) or supplied.keys() - {"resamples", "seed", "confidence_level"}:
        raise ValueError("precision settings contain unsupported fields")
    config = settings(**supplied)
    loans, scenarios, simulation_config = decode(source)
    bank = make_drivers(loans, simulation_config["paths"], simulation_config["seed"])
    simulations = [simulate(loans, scenario, bank, simulation_config["global_share"]) for scenario in scenarios]
    summary, samples = paired_bootstrap(simulations, alpha=simulation_config["alpha"], **config)
    summary.update(schema_version=1, data_kind=source.get("data_kind", "user_supplied"),
        scenario_names=[scenario.name for scenario in scenarios], loans=len(loans), obligors=len(bank.obligors))
    normalized = dict(data_kind=summary["data_kind"], portfolio=[asdict(loan) for loan in loans],
        scenarios=[asdict(scenario) for scenario in scenarios], config=simulation_config, precision=config)
    return {"inputs.json": canonical(normalized), "summary.json": canonical(summary),
        "intervals.csv": csv_text(summary["intervals"], list(summary["intervals"][0])),
        "resamples.csv": csv_text(samples, list(samples[0])), "report.html": render_precision(summary)}


def write_precision_bundle(data, out):
    artifacts = precision_artifacts(data)
    target = Path(out)
    target.mkdir(parents=True, exist_ok=True)
    for name, content in artifacts.items():
        (target/name).write_text(content, encoding="utf-8", newline="")
    manifest = {"schema_version": 1, "engine": ENGINE,
        "runtime": {"python": platform.python_version(), "numpy": np.__version__, "scipy": scipy.__version__},
        "files": {name: sha256(content.encode()).hexdigest() for name, content in sorted(artifacts.items())}}
    (target/"manifest.json").write_text(canonical(manifest), encoding="utf-8")
    return read_json(target/"summary.json")


def verify_precision_bundle(out):
    target = Path(out)
    manifest = read_json(target/"manifest.json")
    if (not isinstance(manifest, dict) or manifest.get("schema_version") != 1 or manifest.get("engine") != ENGINE
            or not isinstance(manifest.get("files"), dict) or set(manifest["files"]) != set(FILES)):
        raise ValueError("unsupported precision manifest or unexpected artifact paths")
    for name in FILES:
        if sha256((target/name).read_bytes()).hexdigest() != manifest["files"][name]:
            raise ValueError(f"hash mismatch: {name}")
    for name, content in precision_artifacts(read_json(target/"inputs.json")).items():
        if (target/name).read_bytes() != content.encode():
            raise ValueError(f"precision semantic replay mismatch: {name}; use requirements-replay.txt")
    return {"verified": True, "files": len(FILES),
        "replayed_paths": read_json(target/"summary.json")["simulation_paths"],
        "replayed_resamples": read_json(target/"summary.json")["resamples"]}
