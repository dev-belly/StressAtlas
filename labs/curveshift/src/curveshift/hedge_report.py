"""Portable hedge research with byte hashes and independent numerical replay."""

import base64
import hashlib
import json
import math
from importlib.resources import files
from pathlib import Path

from .hedge import hedge_study
from .report import csv_bytes, json_bytes


def hedge_packet(result=None):
    result = hedge_study() if result is None else result
    frontier = [
        {
            "budget_usd": case["budget_usd"],
            "gross_face_usd": case["gross_face_usd"],
            "entry_cost_usd": case["entry_cost_usd"],
            "training_es_usd": case["training_risk"]["es_usd"],
            "training_objective_usd": case["objective_usd"],
            "heldout_es_usd": case["evaluation_risk"]["es_usd"],
            "heldout_es_plus_entry_usd": case["evaluation_es_plus_entry_usd"],
            **{f"hedge_{t}y_face_usd": case["positions_face_usd"][i] for i, t in enumerate((2, 5, 10, 30))},
        }
        for case in result["cases"]
    ]
    losses = [
        {
            "budget_usd": case["budget_usd"],
            **{key: row[key] for key in ("from", "to", "unhedged_loss_usd", "hedged_loss_usd")},
        }
        for case in result["cases"]
        for row in case["evaluation_losses"]
    ]
    packet = {
        "hedge.json": json_bytes(result),
        "frontier.csv": csv_bytes(frontier),
        "heldout_losses.csv": csv_bytes(losses),
    }
    template = files("curveshift").joinpath("data/hedge.html").read_text(encoding="utf-8")
    payload = json.dumps(result, sort_keys=True, allow_nan=False).replace("<", "\\u003c")
    template = template.replace("@@PAYLOAD@@", payload)
    for name, raw in packet.items():
        mime = "text/csv" if name.endswith(".csv") else "application/json"
        template = template.replace(f"@@DOWNLOAD:{name}@@", f"data:{mime};base64," + base64.b64encode(raw).decode())
    packet["index.html"] = template.encode("utf-8")
    packet["manifest.json"] = json_bytes(
        {"files": {name: hashlib.sha256(raw).hexdigest() for name, raw in packet.items()}}
    )
    return packet


def write_hedge_report(destination):
    target = Path(destination)
    if target.is_symlink() or (target.exists() and (not target.is_dir() or any(target.iterdir()))):
        raise ValueError("Hedge report destination must be new or empty")
    packet = hedge_packet()
    target.mkdir(parents=True, exist_ok=True)
    for name, raw in packet.items():
        (target / name).write_bytes(raw)
    return {"out": str(target), "files": len(packet), "verified": verify_hedge_report(target)["verified"]}


def verify_hedge_report(directory):
    target = Path(directory)
    names = {"hedge.json", "frontier.csv", "heldout_losses.csv", "index.html", "manifest.json"}
    if target.is_symlink() or not target.is_dir() or {p.name for p in target.iterdir()} != names:
        raise ValueError("Unexpected hedge report directory or file set")
    if any(p.is_symlink() or not p.is_file() for p in target.iterdir()):
        raise ValueError("Hedge report members must be ordinary files")
    raw = {p.name: p.read_bytes() for p in target.iterdir()}
    manifest = json.loads(raw["manifest.json"])
    if manifest != {
        "files": {name: hashlib.sha256(data).hexdigest() for name, data in raw.items() if name != "manifest.json"}
    }:
        raise ValueError("Hedge report byte hash mismatch")
    saved = json.loads(raw["hedge.json"])
    current = hedge_study()

    def compare(actual, expected, path="hedge"):
        if isinstance(expected, float):
            # Solver residuals and dollar results may differ in the last digits across builds.
            tolerance = 1e-3 if ".solver." in path else 1e-5
            if type(actual) not in (int, float) or not math.isclose(actual, expected, rel_tol=1e-9, abs_tol=tolerance):
                raise ValueError(f"Hedge numerical replay differs: {path}")
        elif isinstance(expected, dict):
            if not isinstance(actual, dict) or set(actual) != set(expected):
                raise ValueError(f"Hedge replay keys differ: {path}")
            for key, value in expected.items():
                compare(actual[key], value, path + "." + key)
        elif isinstance(expected, list):
            if not isinstance(actual, list) or len(actual) != len(expected):
                raise ValueError(f"Hedge replay length differs: {path}")
            for i, (a, e) in enumerate(zip(actual, expected, strict=True)):
                compare(a, e, f"{path}[{i}]")
        elif type(actual) is not type(expected) or actual != expected:
            raise ValueError(f"Hedge replay value differs: {path}")

    compare(saved, current)
    expected_packet = hedge_packet(saved)
    if raw != expected_packet:
        raise ValueError("Hedge tables or HTML differ from the recorded research")
    return {
        "verified": True,
        "files": len(names),
        "training_end": saved["training_end"],
        "evaluation_end": saved["evaluation_end"],
    }
