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
                if not execute("return document.documentElement.scrollWidth <= innerWidth+1;"):
                    raise AssertionError("Report overflows viewport")
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
