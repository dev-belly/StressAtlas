"""One self-contained rate-risk laboratory, plus complete JSON/CSV evidence."""

import base64
import csv
import hashlib
import io
import json
import math
import platform
from importlib.metadata import version
from importlib.resources import files
from pathlib import Path

import numpy as np
import polars as pl

from .engine import build_book, scenario, sensitivities
from .market import TENORS, check_reported_yields, load_market, select_curve
from .research import duration_neutral_case, engine_crosscheck, historical_risk, pca_study


def json_bytes(value):
    return (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False) + "\n").encode("utf-8")


def csv_bytes(rows):
    stream = io.StringIO(newline="")
    writer = csv.DictWriter(stream, fieldnames=list(rows[0]), lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    return stream.getvalue().encode("utf-8")


def analyze(as_of="2025-12-31"):
    market, source = load_market()
    row, curve = select_curve(market, as_of)
    valuation = row["Date"].isoformat()
    book = build_book(valuation)
    bonds = []
    for bond in book:
        serial = {key: (value.tolist() if isinstance(value, np.ndarray) else value) for key, value in bond.items()}
        serial["zero_rates"] = curve(bond["times"].copy()).tolist()
        serial["risk"] = sensitivities(bond, curve)
        bonds.append(serial)
    defaults = scenario(book, curve, [50, 50, 50, 50])
    history = historical_risk(market, row["Date"], book, curve)
    result = {
        "schema_version": 1,
        "requested_as_of": as_of,
        "valuation_date": valuation,
        "source": source,
        "environment": {
            name: version(name)
            for name in ("QuantLib", "nelson-siegel-svensson", "numpy", "scipy", "scikit-learn", "polars")
        },
        "conventions": {
            "instrument": "hypothetical USD fixed-rate bonds; no real security identifiers",
            "coupon": "4% annual rate paid semiannually, unadjusted dates, 30/360 Bond Basis",
            "discounting": "Actual/365 Fixed; continuously compounded NSS zero rates",
            "shock": "additive spot-rate shifts; piecewise linear at 2/5/10/30Y, flat beyond endpoints",
            "risk": "constant current cash flows; no passage of time, credit/liquidity/reinvestment/financing effects",
        },
        "bonds": bonds,
        "default_scenario": defaults,
        "historical_risk": history,
        "duration_neutral": duration_neutral_case(book, curve),
        "pca": pca_study(market.filter(pl.col("Date") <= row["Date"])),
        "checks": {
            "reported_yields": check_reported_yields(market),
            "pricing_engines": engine_crosscheck(book, valuation, curve),
        },
        "curve": {"tenors": TENORS.tolist(), "zero_pct": (curve(TENORS.copy()) * 100).tolist()},
    }
    result["environment"]["python"] = platform.python_version()
    return result


def build_packet(as_of="2025-12-31"):
    result = analyze(as_of)
    evidence = {
        "analysis.json": json_bytes(result),
        "historical_losses.csv": csv_bytes(result["historical_risk"]["losses"]),
        "key_rate_risk.csv": csv_bytes(
            [
                {
                    "bond": bond["name"],
                    "pv_usd": bond["risk"]["pv"],
                    **{f"dv01_{tenor}y_usd": bond["risk"]["key_dv01"][i] for i, tenor in enumerate((2, 5, 10, 30))},
                    "parallel_dv01_usd": bond["risk"]["parallel_dv01"],
                    "effective_duration": bond["risk"]["effective_duration"],
                }
                for bond in result["bonds"]
            ]
        ),
        "curve_source.csv": files("curveshift").joinpath("data/fed_gsw_2016_2025.csv").read_bytes(),
        "source.json": json_bytes(result["source"]),
    }
    evidence["index.html"] = render_html(result, evidence)
    evidence["manifest.json"] = json_bytes(
        {"files": {name: hashlib.sha256(raw).hexdigest() for name, raw in evidence.items()}}
    )
    return evidence


def render_html(result, evidence):
    template = files("curveshift").joinpath("data/report.html").read_text(encoding="utf-8")
    payload = json.dumps(result, ensure_ascii=False, sort_keys=True, allow_nan=False).replace("<", "\\u003c")
    template = template.replace("@@PAYLOAD@@", payload)
    for name, raw in evidence.items():
        mime = "text/csv" if name.endswith(".csv") else "application/json"
        template = template.replace(
            f"@@DOWNLOAD:{name}@@", f"data:{mime};charset=utf-8;base64," + base64.b64encode(raw).decode()
        )
    return template.encode("utf-8")


def write_report(out, as_of="2025-12-31"):
    packet = build_packet(as_of)
    target = Path(out)
    if target.is_symlink() or (target.exists() and (not target.is_dir() or any(target.iterdir()))):
        raise ValueError("Report destination must be new or empty")
    target.mkdir(parents=True, exist_ok=True)
    for name, raw in packet.items():
        (target / name).write_bytes(raw)
    analysis = json.loads(packet["analysis.json"])
    return {
        "out": str(target),
        "valuation_date": analysis["valuation_date"],
        "observations": analysis["source"]["observations"],
        "pricing_crosscheck": analysis["checks"]["pricing_engines"],
    }


def verify_report(directory):
    target = Path(directory)
    names = {
        "analysis.json",
        "historical_losses.csv",
        "key_rate_risk.csv",
        "curve_source.csv",
        "source.json",
        "index.html",
    }
    if target.is_symlink() or not target.is_dir() or {p.name for p in target.iterdir()} != names | {"manifest.json"}:
        raise ValueError("Unexpected report directory or file set")
    if any(p.is_symlink() or not p.is_file() for p in target.iterdir()):
        raise ValueError("Report members must be ordinary files")
    raw = {name: (target / name).read_bytes() for name in names}
    manifest = json.loads((target / "manifest.json").read_bytes())
    if manifest != {"files": {name: hashlib.sha256(value).hexdigest() for name, value in raw.items()}}:
        raise ValueError("Report byte hash mismatch")
    saved = json.loads(raw["analysis.json"])
    current = analyze(saved["requested_as_of"])

    def compare(actual, expected, path="analysis"):
        if isinstance(expected, float):
            if type(actual) not in (int, float) or not math.isclose(actual, expected, rel_tol=1e-10, abs_tol=1e-6):
                raise ValueError(f"Numerical replay differs: {path}")
        elif isinstance(expected, dict):
            if not isinstance(actual, dict) or set(actual) != set(expected):
                raise ValueError(f"Replay keys differ: {path}")
            for key in expected:
                if path == "analysis" and key == "environment":
                    continue
                compare(actual[key], expected[key], path + "." + key)
        elif isinstance(expected, list):
            if not isinstance(actual, list) or len(actual) != len(expected):
                raise ValueError(f"Replay length differs: {path}")
            for i, (a, e) in enumerate(zip(actual, expected, strict=True)):
                compare(a, e, f"{path}[{i}]")
        elif type(actual) is not type(expected) or actual != expected:
            raise ValueError(f"Replay value differs: {path}")

    compare(saved, current)
    if raw["curve_source.csv"] != files("curveshift").joinpath("data/fed_gsw_2016_2025.csv").read_bytes():
        raise ValueError("Report curve inputs differ from bundled source")
    if raw["source.json"] != json_bytes(saved["source"]):
        raise ValueError("Source ledger differs")
    expected_losses = csv_bytes(
        [{key: row[key] for key in ("from", "to", "loss_usd")} for row in saved["historical_risk"]["losses"]]
    )
    if raw["historical_losses.csv"] != expected_losses:
        raise ValueError("Loss CSV differs")
    expected_risk = csv_bytes(
        [
            {
                "bond": bond["name"],
                "pv_usd": bond["risk"]["pv"],
                **{f"dv01_{tenor}y_usd": bond["risk"]["key_dv01"][i] for i, tenor in enumerate((2, 5, 10, 30))},
                "parallel_dv01_usd": bond["risk"]["parallel_dv01"],
                "effective_duration": bond["risk"]["effective_duration"],
            }
            for bond in saved["bonds"]
        ]
    )
    if raw["key_rate_risk.csv"] != expected_risk:
        raise ValueError("Risk CSV differs")
    if raw["index.html"] != render_html(saved, {key: value for key, value in raw.items() if key != "index.html"}):
        raise ValueError("Report HTML differs from recorded research")
    return {
        "verified": True,
        "files": len(names) + 1,
        "valuation_date": saved["valuation_date"],
        "numerical_tolerance": {"relative": 1e-10, "absolute": 1e-6},
    }
