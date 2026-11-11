"""Check real Chrome against QuantLib, share-state restoration and downloaded inputs."""

import base64
import json
import os
import shutil
import socket
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

from curveshift.cli import evaluate_scenario
from curveshift.engine import build_book, quantlib_price
from curveshift.hedge import FIT_DATE, HEDGES, TARGET
from curveshift.market import load_market, select_curve


def main():
    driver = shutil.which("chromedriver")
    if not driver:
        candidate = Path(os.environ.get("CHROMEWEBDRIVER", "")) / "chromedriver"
        if candidate.is_file():
            driver = str(candidate.resolve())
    if not driver:
        raise RuntimeError("Chrome and a matching chromedriver are required")
    with socket.socket() as available:
        available.bind(("127.0.0.1", 0))
        port = available.getsockname()[1]
    address = f"http://127.0.0.1:{port}"
    artifacts = Path(os.environ.get("CURVESHIFT_BROWSER_ARTIFACTS", "browser-artifacts")).resolve()
    artifacts.mkdir(parents=True, exist_ok=True)
    process = subprocess.Popen(
        [driver, f"--port={port}", f"--log-path={artifacts / 'chromedriver.log'}"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.STDOUT,
    )
    session = None

    def call(method, endpoint, payload=None, timeout=30):
        request = urllib.request.Request(
            address + endpoint,
            data=json.dumps(payload).encode() if payload is not None else None,
            headers={"Content-Type": "application/json"},
            method=method,
        )
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.load(response)["value"]

    frame, source = load_market()
    row, curve = select_curve(frame, "2025-12-31")
    book = build_book(row["Date"].isoformat())
    base = sum(quantlib_price(b, row["Date"].isoformat(), curve) for b in book)
    source_report = Path(__file__).resolve().parents[1] / "reports/demo"
    checks = downloads = 0
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory).resolve()
        download_dir = root / "downloads"
        download_dir.mkdir()
        try:
            for _ in range(150):
                try:
                    call("GET", "/status")
                    break
                except urllib.error.URLError:
                    time.sleep(0.1)
            session = call(
                "POST",
                "/session",
                {
                    "capabilities": {
                        "alwaysMatch": {
                            "browserName": "chrome",
                            "goog:chromeOptions": {
                                "args": ["--headless=new", "--no-sandbox", "--disable-dev-shm-usage"],
                                "prefs": {
                                    "download.default_directory": str(download_dir),
                                    "download.prompt_for_download": False,
                                },
                            },
                        }
                    }
                },
                timeout=90,
            )["sessionId"]

            def execute(script, *args):
                return call("POST", f"/session/{session}/execute/sync", {"script": script, "args": list(args)})

            def navigate(url):
                return call("POST", f"/session/{session}/url", {"url": url})

            def click(selector):
                element = call("POST", f"/session/{session}/element", {"using": "css selector", "value": selector})[
                    "element-6066-11e4-a52e-4f735466cecf"
                ]
                call("POST", f"/session/{session}/element/{element}/click", {})

            def assert_price(shock):
                nonlocal checks
                expected = sum(quantlib_price(b, row["Date"].isoformat(), curve, shock) for b in book) - base
                actual = float(execute("return document.getElementById('full-pnl').dataset.value;"))
                if abs(actual - expected) > 1e-5:
                    raise AssertionError((shock, actual, expected))
                overflow = execute("""
                    const limit=document.documentElement.clientWidth;
                    if(document.documentElement.scrollWidth<=limit+1)return null;
                    return {client:limit,scroll:document.documentElement.scrollWidth,offenders:[...document.querySelectorAll('body *')]
                        .filter(n=>n.getBoundingClientRect().right>limit+1 && !n.closest('.table-wrap'))
                        .map(n=>({tag:n.tagName,id:n.id,right:n.getBoundingClientRect().right,text:n.textContent.slice(0,80)}))
                        .slice(0,12)};
                """)
                if overflow:
                    raise AssertionError(("Report overflows its visible viewport", overflow))
                checks += 1

            def download(filename, selector, expected=None):
                nonlocal downloads
                path = download_dir / filename
                path.unlink(missing_ok=True)
                click(selector)
                for _ in range(150):
                    if path.is_file() and (expected is None or path.read_bytes() == expected):
                        break
                    time.sleep(0.05)
                else:
                    raise AssertionError(f"Download failed or changed: {filename}")
                downloads += 1
                return path.read_bytes()

            def screenshot(name):
                raw = call("GET", f"/session/{session}/screenshot")
                (artifacts / name).write_bytes(base64.b64decode(raw))

            for width in (1280, 390):
                call("POST", f"/session/{session}/window/rect", {"width": width, "height": 1000})
                actual_width = execute("return innerWidth;")
                if actual_width != width:
                    raise AssertionError(("Requested viewport differs from actual viewport", width, actual_width))
                url = (source_report / "index.html").as_uri()
                navigate(url)
                assert_price([50] * 4)
                screenshot(f"curveshift-{width}-overview.png")
                for selector, shock in [
                    ("#steepener", [0, 25, 50, 100]),
                    ("#twist", [-25, 0, 25, 0]),
                    ("#reset", [0] * 4),
                    ("#parallel", [50] * 4),
                ]:
                    click(selector)
                    assert_price(shock)
                execute(
                    "const n=document.getElementById('s30');n.value=120;n.dispatchEvent(new Event('input',{bubbles:true}));"
                )
                assert_price([50, 50, 50, 120])
                shared = execute("return location.href;")
                navigate(url)
                navigate(shared)
                assert_price([50, 50, 50, 120])
                execute("document.getElementById('lab-title').scrollIntoView();")
                screenshot(f"curveshift-{width}-laboratory.png")
                raw = download("curveshift-scenario-2025-12-31.json", "#download-scenario")
                inputs = json.loads(raw)
                if inputs != {
                    "as_of": "2025-12-31",
                    "shock_bp": [50, 50, 50, 120],
                    "source_sha256": source["subset_sha256"],
                }:
                    raise AssertionError("Downloaded scenario is not the visible scenario")
                evaluate_scenario(inputs)
                for exported, original in [
                    ("analysis.json", "analysis.json"),
                    ("key-rate-risk.csv", "key_rate_risk.csv"),
                    ("historical-losses.csv", "historical_losses.csv"),
                    ("curve-source.csv", "curve_source.csv"),
                    ("source.json", "source.json"),
                ]:
                    filename = "curveshift-" + exported
                    download(filename, f'a[download="{filename}"]', (source_report / original).read_bytes())
                navigate(url + "#shock=NaN,0,0,0")
                assert_price([50] * 4)
                if not execute("return document.getElementById('hash-status').textContent.includes('invalid');"):
                    raise AssertionError("Invalid saved scenario has no notice")
                click("#reset")
                assert_price([0] * 4)
                if execute("return document.getElementById('hash-status').textContent;"):
                    raise AssertionError("A valid scenario retained an invalid-link notice")
            hedge_report = source_report.parent / "hedge"
            hedge = json.loads((hedge_report / "hedge.json").read_bytes())
            _, hedge_curve = select_curve(frame, FIT_DATE)
            hedge_book = build_book(FIT_DATE, TARGET + HEDGES)

            def assert_hedge(case, shock):
                nonlocal checks
                deltas = [
                    quantlib_price(b, FIT_DATE, hedge_curve, shock) - quantlib_price(b, FIT_DATE, hedge_curve)
                    for b in hedge_book
                ]
                expected = deltas[0] + sum(
                    p / 1e6 * delta for p, delta in zip(case["positions_face_usd"], deltas[1:], strict=True)
                )
                actual = float(execute("return document.getElementById('stress-after').dataset.value;"))
                es = float(execute("return document.getElementById('after-es').dataset.value;"))
                if abs(actual - expected) > 1e-5 or abs(es - case["evaluation_risk"]["es_usd"]) > 1e-6:
                    raise AssertionError(("Hedge browser result differs from QuantLib", actual, expected, es))
                if execute("return document.documentElement.scrollWidth > document.documentElement.clientWidth+1;"):
                    raise AssertionError("Hedge page overflows its viewport")
                checks += 1

            for width in (1280, 390):
                call("POST", f"/session/{session}/window/rect", {"width": width, "height": 1000})
                url = (hedge_report / "index.html").as_uri()
                navigate(url)
                screenshot(f"hedge-{width}-overview.png")
                for case in hedge["cases"]:
                    click(f'[data-budget="{case["budget_usd"]}"]')
                    assert_hedge(case, [-25, 0, 25, 0])
                click('[data-budget="6000000"]')
                for selector, shock in [("#parallel", [50] * 4), ("#reset", [0] * 4), ("#twist", [-25, 0, 25, 0])]:
                    click(selector)
                    assert_hedge(hedge["cases"][3], shock)
                trades = download("curveshift-hedge-6000000.csv", "#download-plan")
                import csv
                import io

                positions = [float(r["signed_face_usd"]) for r in csv.DictReader(io.StringIO(trades.decode()))]
                if any(
                    abs(a - b) > 1e-5 for a, b in zip(positions, hedge["cases"][3]["positions_face_usd"], strict=True)
                ):
                    raise AssertionError("Downloaded trades differ from the selected hedge")
                for exported, original in [
                    ("hedge.json", "hedge.json"),
                    ("frontier.csv", "frontier.csv"),
                    ("heldout-losses.csv", "heldout_losses.csv"),
                ]:
                    filename = "curveshift-" + exported
                    download(filename, f'a[download="{filename}"]', (hedge_report / original).read_bytes())
                navigate(url + "#budget=4000000&shock=50,50,50,50")
                assert_hedge(hedge["cases"][2], [50] * 4)
                navigate(url + "#budget=bad&shock=NaN,0,0,0")
                assert_hedge(hedge["cases"][3], [-25, 0, 25, 0])
                if not execute("return document.getElementById('state-status').textContent.includes('invalid');"):
                    raise AssertionError("Invalid hedge link has no notice")
                click("#reset")
                if execute("return document.getElementById('state-status').textContent;"):
                    raise AssertionError("A valid hedge retained an invalid-link notice")
            print(
                json.dumps(
                    {
                        "status": "passed",
                        "viewports": [1280, 390],
                        "quantlib_price_checks": checks,
                        "downloads": downloads,
                    }
                )
            )
        finally:
            if session:
                try:
                    call("DELETE", f"/session/{session}")
                except Exception:
                    pass
            process.terminate()
            process.wait(timeout=15)


if __name__ == "__main__":
    main()
