"""Offline report generation and replay of a browser's actual four-key scenario."""

import argparse
import json
import sys
from pathlib import Path

from .engine import build_book, scenario, validate_shock
from .hedge import hedge_study
from .hedge_report import verify_hedge_report, write_hedge_report
from .market import load_market, select_curve
from .report import json_bytes, verify_report, write_report


def evaluate_scenario(inputs):
    if not isinstance(inputs, dict) or set(inputs) != {"as_of", "shock_bp", "source_sha256"}:
        raise ValueError("Scenario requires exactly as_of, shock_bp and source_sha256")
    shocks = inputs["shock_bp"]
    if not isinstance(shocks, list) or any(type(x) not in (int, float) for x in shocks):
        raise ValueError("Scenario shifts must be JSON numbers")
    validate_shock(shocks)
    frame, source = load_market()
    if inputs["source_sha256"] != source["subset_sha256"]:
        raise ValueError("Scenario source does not match the bundled market snapshot")
    row, curve = select_curve(frame, inputs["as_of"])
    return {
        "inputs": inputs,
        "valuation_date": row["Date"].isoformat(),
        "result": scenario(build_book(row["Date"].isoformat()), curve, shocks),
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description="CurveShift: source-linked interest-rate risk research")
    commands = parser.add_subparsers(dest="command", required=True)
    report = commands.add_parser("report", help="Build the self-contained historical risk laboratory")
    report.add_argument("--out", required=True)
    report.add_argument("--as-of", default="2025-12-31")
    replay = commands.add_parser("replay", help="Replay the scenario downloaded from the browser")
    replay.add_argument("inputs")
    replay.add_argument("--out", required=True)
    check = commands.add_parser("verify", help="Verify file hashes, source and full numerical replay")
    check.add_argument("directory")
    hedge = commands.add_parser("hedge", help="Fit a constrained CVaR hedge using only pre-2025 curve changes")
    hedge.add_argument("--budget-usd", type=float, default=6_000_000)
    hedge.add_argument("--cost-bp", type=float, default=2.0)
    hedge_report = commands.add_parser("hedge-report", help="Build the independent hedge laboratory")
    hedge_report.add_argument("--out", required=True)
    hedge_check = commands.add_parser("hedge-verify", help="Replay the saved hedge optimization and evaluation")
    hedge_check.add_argument("directory")
    args = parser.parse_args(argv)
    try:
        if args.command == "report":
            result = write_report(args.out, args.as_of)
        elif args.command == "verify":
            result = verify_report(args.directory)
        elif args.command == "hedge":
            result = hedge_study((args.budget_usd,), args.cost_bp)
        elif args.command == "hedge-report":
            result = write_hedge_report(args.out)
        elif args.command == "hedge-verify":
            result = verify_hedge_report(args.directory)
        else:
            destination = Path(args.out)
            if destination.exists() or destination.is_symlink():
                raise ValueError("Scenario output already exists")
            inputs = json.loads(Path(args.inputs).read_text(encoding="utf-8"))
            result = evaluate_scenario(inputs)
            destination.parent.mkdir(parents=True, exist_ok=True)
            with destination.open("xb") as output:
                output.write(json_bytes(result))
        print(json.dumps(result, ensure_ascii=True, allow_nan=False))
        return 0
    except (ValueError, TypeError, KeyError, OSError) as exc:
        print(f"curveshift: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
