"""Deterministic evidence, correlation sensitivity and a self-contained report."""

from dataclasses import asdict
from hashlib import sha256
from html import escape
import io
import csv
import json
from pathlib import Path
import platform

import numpy as np
import scipy

from .core import Loan, Scenario, SectorShock, finite, make_drivers, paired_delta, sector_attribution, simulate, tail_metrics, validate_portfolio


FILES = ("inputs.json", "summary.json", "scenarios.csv", "sector_es.csv", "loss_sample.csv", "report.html")


def canonical(value):
    return json.dumps(value, sort_keys=True, indent=2, ensure_ascii=False, allow_nan=False) + "\n"


def read_json(path):
    def bad(value):
        raise ValueError(f"non-finite JSON constant: {value}")
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError(f"duplicate JSON key: {key}")
            result[key] = value
        return result
    return json.loads(Path(path).read_text(encoding="utf-8"), parse_constant=bad, object_pairs_hook=pairs)


def decode(data):
    if not isinstance(data, dict) or not {"portfolio", "scenarios", "config"} <= set(data) or set(data) - {"portfolio", "scenarios", "config", "data_kind"}:
        raise ValueError("inputs require portfolio, scenarios, config; only data_kind is optional")
    if data.get("data_kind", "user_supplied") not in ("synthetic", "user_supplied"):
        raise ValueError("invalid data_kind")
    if not isinstance(data["portfolio"], list) or not isinstance(data["scenarios"], list) or not isinstance(data["config"], dict):
        raise ValueError("portfolio/scenarios must be arrays and config must be an object")
    scenarios = []
    try:
        loans = validate_portfolio(Loan(**row) for row in data["portfolio"])
        for row in data["scenarios"]:
            fields = dict(row)
            fields["sector_shocks"] = tuple(sorted((SectorShock(**s) for s in fields.get("sector_shocks", [])), key=lambda s: s.sector))
            scenarios.append(Scenario(**fields))
    except (TypeError, KeyError) as exc:
        raise ValueError(f"input schema mismatch: {exc}") from exc
    if not scenarios or len({s.name for s in scenarios}) != len(scenarios):
        raise ValueError("at least one scenario is required, with unique names")
    config = data["config"]
    if set(config) != {"paths", "seed", "alpha", "global_share"}:
        raise ValueError("config requires exactly paths, seed, alpha, global_share")
    finite(config["alpha"], "alpha", upper=1, positive=True)
    if config["alpha"] == 1:
        raise ValueError("alpha must be below 1")
    finite(config["global_share"], "global_share", upper=1)
    sectors = {l.sector for l in loans}
    if any(s.sector not in sectors for scenario in scenarios for s in scenario.sector_shocks):
        raise ValueError("unknown sector shock")
    return loans, scenarios, dict(config)


def spreadsheet_cell(value):
    """Neutralize formula-shaped strings; leave numeric financial values untouched."""
    if isinstance(value, str) and (
        value.startswith(("\t", "\r", "\n"))
        or value.lstrip(" \t\r\n").startswith(("=", "+", "-", "@"))
    ):
        return "'" + value
    return value


def csv_text(rows, fields):
    stream = io.StringIO(newline="")
    writer = csv.DictWriter(stream, fieldnames=fields, lineterminator="\n")
    writer.writerow({field: spreadsheet_cell(field) for field in fields})
    writer.writerows({field: spreadsheet_cell(value) for field, value in row.items()}
                    for row in rows)
    return stream.getvalue()


def concentration(loans, field):
    exposures = {}
    for loan in loans:
        key = getattr(loan, field)
        exposures[key] = exposures.get(key, 0) + loan.ead
    total = sum(exposures.values())
    return float(sum((v/total)**2 for v in exposures.values()))


def money(value):
    return round(float(value), 6)


def run(data):
    loans, scenarios, config = decode(data)
    bank = make_drivers(loans, config["paths"], config["seed"])
    results = [simulate(loans, s, bank, config["global_share"]) for s in scenarios]
    scenario_rows, sector_rows = [], []
    for simulation in results:
        metrics = tail_metrics(simulation.losses, config["alpha"])
        delta = paired_delta(simulation, results[0])
        attribution = sector_attribution(simulation, config["alpha"])
        if not np.isclose(sum(attribution.values()), metrics["es"], rtol=1e-12, atol=1e-6):
            raise ValueError("sector ES contributions do not sum to portfolio ES")
        scenario_rows.append({"scenario": simulation.name, "analytic_el": money(simulation.analytic_el), **{k: money(v) for k,v in metrics.items()}, **{k: money(v) for k,v in delta.items()}, "stressed_ead": money(simulation.stressed_ead), "max_obligor_default_rate": round(max(simulation.default_rates.values()), 8)})
        for sector, contribution in attribution.items():
            sector_rows.append({"scenario": simulation.name, "sector": sector, "es_contribution": money(contribution), "portfolio_es": money(metrics["es"]), "es_share": round(contribution/metrics["es"], 8) if metrics["es"] else 0.0})
    max_loss = max(float(r.losses.max()) for r in results)
    edges = np.linspace(0, max_loss if max_loss else 1, 41)
    histograms = {r.name: {"edges": [money(x) for x in edges], "counts": np.histogram(r.losses, bins=edges)[0].tolist()} for r in results}
    normalized = {"data_kind": data.get("data_kind", "user_supplied"), "portfolio": [asdict(l) for l in loans], "scenarios": [asdict(s) for s in scenarios], "config": config}
    summary = {"schema_version": 1, "data_kind": normalized["data_kind"], "currency": "CNY", "horizon": "one period; demo PDs represent one year", "loans": len(loans), "obligors": len(bank.obligors), "sectors": len(bank.sectors), "config": config, "base_ead": money(sum(l.ead for l in loans)), "obligor_ead_hhi": round(concentration(loans, "obligor_id"), 8), "sector_ead_hhi": round(concentration(loans, "sector"), 8), "driver_fingerprint": bank.fingerprint, "baseline_scenario": scenarios[0].name, "scenarios": scenario_rows, "sector_es": sector_rows, "histograms": histograms, "warnings": (["Fewer than 100 path-equivalents in the ES tail; increase paths for a more stable tail estimate."] if (1-config["alpha"])*bank.paths < 100-1e-8 else [])}
    samples = [{"path": i, **{"loss:"+r.name: money(r.losses[i]) for r in results}} for i in range(min(200, bank.paths))]
    return normalized, summary, samples


def render(summary):
    labels = "SYNTHETIC PORTFOLIO" if summary["data_kind"] == "synthetic" else "USER-SUPPLIED PORTFOLIO"
    rows = "".join("<tr>" + f"<td>{escape(r['scenario'])}</td>" + "".join(f"<td>{r[k]:,.0f}</td>" for k in ("analytic_el", "mean_loss", "mean_mc_se", "var", "es", "mean_delta", "paired_mc_se")) + "</tr>" for r in summary["scenarios"])
    options = "".join(f'<option value="{escape(r["scenario"],quote=True)}">{escape(r["scenario"])}</option>' for r in summary["scenarios"])
    max_es = max(r["es"] for r in summary["scenarios"]) or 1
    bars = "".join(f'<div class="barrow"><span>{escape(r["scenario"])}</span><div class="track"><div class="bar" style="width:{r["es"]/max_es*100:.3f}%"></div></div><b>{r["es"]/1e6:.2f}m</b></div>' for r in summary["scenarios"])
    data = json.dumps(summary, sort_keys=True, ensure_ascii=False, allow_nan=False).replace("<", "\\u003c").replace("&", "\\u0026")
    warnings = " ".join(escape(w) for w in summary["warnings"])
    return f"""<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>StressAtlas · Portfolio loss scenarios</title><style>
*{{box-sizing:border-box}}body{{margin:0;background:#0d1424;color:#edf2fa;font:16px/1.6 system-ui,sans-serif}}main{{max-width:1200px;margin:auto;padding:48px 24px}}h1{{font-size:clamp(32px,6vw,62px);line-height:1.1;margin:16px 0}}h2{{margin-top:44px}}p{{color:#a9b9d2}}.tag{{font-size:12px;letter-spacing:.14em;color:#f4b76a}}.cards{{display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));gap:16px;margin:32px 0}}.card{{background:#131f35;border:1px solid #2a3955;border-radius:14px;padding:22px}}.value{{display:block;font-size:36px;font-weight:700}}.scroll{{overflow-x:auto;border:1px solid #2a3955;border-radius:12px}}table{{width:100%;border-collapse:collapse;font-size:13px}}th,td{{padding:12px;border-bottom:1px solid #24334e;white-space:nowrap;text-align:right}}th:first-child,td:first-child{{text-align:left}}th{{background:#192842;color:#f4c892}}.barrow{{display:grid;grid-template-columns:150px 1fr 85px;gap:12px;align-items:center;margin:12px 0;font-size:13px}}.track{{background:#1b2c46;height:20px;border-radius:5px;overflow:hidden}}.bar{{height:100%;background:#f4b76a}}select{{font:inherit;background:#192842;color:#edf2fa;border:1px solid #475c7a;border-radius:6px;padding:8px}}#histogram{{width:100%;height:auto;min-height:180px}}code,a{{color:#f4c892}}footer{{margin-top:48px;color:#8da0bc}}@media(max-width:600px){{.barrow{{grid-template-columns:110px 1fr 65px}}}}
</style><main><div class="tag">STRESSATLAS / {labels}</div><h1>When defaults cluster,<br>the tail changes.</h1><p>Credit portfolio stress testing with common random numbers, borrower-level defaults and additive expected-shortfall attribution.</p><p><a href="https://dev-belly.github.io/StressAtlas/precision/">Inspect VaR/ES sampling precision and paired intervals</a></p><div class="cards"><div class="card"><span class="value">{summary['loans']}</span>Loan positions / {summary['obligors']} obligors</div><div class="card"><span class="value">{summary['base_ead']/1e6:.1f}m</span>Base exposure / CNY</div><div class="card"><span class="value">{summary['config']['paths']:,}</span>Shared Monte Carlo paths</div><div class="card"><span class="value">{summary['config']['alpha']*100:g}%</span>VaR and ES level</div></div><h2>Scenario comparison</h2><p>All amounts in CNY. MC SE measures sampling error in the mean; it is not a confidence interval for VaR or ES. Paired deltas compare each scenario with {escape(summary['baseline_scenario'])} using the same driver bank.</p><div class="scroll"><table><thead><tr><th>Scenario</th><th>Analytic EL</th><th>Simulated mean</th><th>Mean MC SE</th><th>VaR</th><th>ES</th><th>Mean Δ</th><th>Paired SE</th></tr></thead><tbody>{rows}</tbody></table></div><h2>Tail loss at a glance</h2>{bars}<p>Compare scenarios that change correlation separately from scenarios that change marginal PD, LGD or EAD. Inspect the saved inputs for the assumptions behind each named scenario.</p><h2>Inspect loss distribution and sector attribution</h2><label>Scenario <select id="scenario">{options}</select></label><svg id="histogram" viewBox="0 0 1000 270" role="img" aria-label="Simulated loss histogram"></svg><p id="hist-note"></p><div class="scroll"><table><thead><tr><th>Sector</th><th>Contribution to portfolio ES / CNY</th><th>ES share</th></tr></thead><tbody id="sectors"></tbody></table></div><p>Sector contributions use the same portfolio-tail weights. They sum to portfolio ES. Tied boundary losses share their tail weight equally.</p><h2>Assumptions and replay</h2><p>One-period Gaussian factors, deterministic scenario LGD and EAD, fixed marginal PDs. No rating migration, dynamic recoveries or empirical macro calibration. This is a research prototype, not a regulatory capital calculation. {warnings}</p><p><code>stressatlas verify --out demo</code> validates artifact hashes and reruns all paths from saved inputs and seed. The CSV sample contains the first 200 paths, not the complete tail.</p><footer>Inspectable assumptions · {labels} · <a href="https://github.com/dev-belly/StressAtlas">Source and methodology</a></footer></main><script>
const data={data};const select=document.getElementById('scenario');function show(){{const name=select.value,h=data.histograms[name],svg=document.getElementById('histogram');svg.replaceChildren();const ns='http://www.w3.org/2000/svg';const peak=Math.max(...h.counts,1);h.counts.forEach((c,i)=>{{const rect=document.createElementNS(ns,'rect');rect.setAttribute('x',20+i*24);rect.setAttribute('y',230-200*c/peak);rect.setAttribute('width',21);rect.setAttribute('height',200*c/peak);rect.setAttribute('fill','#f4b76a');const title=document.createElementNS(ns,'title');title.textContent=`${{h.edges[i].toFixed(0)}}–${{h.edges[i+1].toFixed(0)}} CNY: ${{c}} paths`;rect.append(title);svg.append(rect)}});const note=document.getElementById('hist-note');note.textContent=`Fixed loss bins: 0 to ${{(h.edges[h.edges.length-1]/1e6).toFixed(2)}}m CNY. Bars show path counts; hover for values.`;const body=document.getElementById('sectors');body.replaceChildren();data.sector_es.filter(r=>r.scenario===name).forEach(r=>{{const tr=document.createElement('tr');[r.sector,r.es_contribution.toLocaleString(undefined,{{maximumFractionDigits:0}}),(100*r.es_share).toFixed(1)+'%'].forEach(v=>{{const td=document.createElement('td');td.textContent=v;tr.append(td)}});body.append(tr)}})}}select.addEventListener('change',show);show();</script></html>\n"""


def artifacts(data):
    inputs, summary, samples = run(data)
    return {"inputs.json": canonical(inputs), "summary.json": canonical(summary), "scenarios.csv": csv_text(summary["scenarios"], list(summary["scenarios"][0])), "sector_es.csv": csv_text(summary["sector_es"], list(summary["sector_es"][0])), "loss_sample.csv": csv_text(samples, list(samples[0])), "report.html": render(summary)}


def write_bundle(data, out):
    files = artifacts(data)
    target = Path(out)
    target.mkdir(parents=True, exist_ok=True)
    for name, content in files.items():
        (target/name).write_text(content, encoding="utf-8", newline="")
    manifest = {"schema_version": 1, "engine": "stressatlas/0.1.0", "runtime": {"python": platform.python_version(), "numpy": np.__version__, "scipy": scipy.__version__}, "files": {name: sha256(content.encode()).hexdigest() for name,content in sorted(files.items())}}
    (target/"manifest.json").write_text(canonical(manifest), encoding="utf-8")
    return read_json(target/"summary.json")


def verify_bundle(out):
    target = Path(out)
    manifest = read_json(target/"manifest.json")
    if not isinstance(manifest, dict) or not isinstance(manifest.get("files"), dict) or manifest.get("schema_version") != 1 or manifest.get("engine") != "stressatlas/0.1.0" or set(manifest["files"]) != set(FILES):
        raise ValueError("unsupported manifest or unexpected artifact paths")
    for name in FILES:
        if sha256((target/name).read_bytes()).hexdigest() != manifest["files"][name]:
            raise ValueError(f"hash mismatch: {name}")
    expected = artifacts(read_json(target/"inputs.json"))
    for name, content in expected.items():
        if (target/name).read_bytes() != content.encode():
            raise ValueError(f"semantic replay mismatch: {name}; use requirements-replay.txt for the saved numerical stack")
    return {"verified": True, "files": len(FILES), "replayed_paths": read_json(target/"summary.json")["config"]["paths"]}
