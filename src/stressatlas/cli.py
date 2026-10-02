import argparse
import json
import sys

from .bundle import read_json, verify_bundle, write_bundle
from .demo import demo_inputs
from .precision import verify_precision_bundle, write_precision_bundle


def main(argv=None):
    parser = argparse.ArgumentParser(description="Paired credit stress scenarios with finite-sample tail attribution")
    sub = parser.add_subparsers(dest="command", required=True)
    for command in ("demo", "run", "verify", "precision", "verify-precision"):
        p = sub.add_parser(command)
        p.add_argument("--out", default="outputs/precision" if command in ("precision", "verify-precision") else "outputs")
        if command == "run":
            p.add_argument("--inputs", required=True)
        elif command == "demo":
            p.add_argument("--paths", type=int, default=20000)
            p.add_argument("--seed", type=int, default=20261001)
        elif command == "precision":
            p.add_argument("--inputs", default="demo/inputs.json")
            p.add_argument("--resamples", type=int, default=300)
            p.add_argument("--bootstrap-seed", type=int, default=20261002)
            p.add_argument("--confidence-level", type=float, default=0.95)
    args = parser.parse_args(argv)
    try:
        if args.command == "verify-precision":
            result = verify_precision_bundle(args.out)
        elif args.command == "precision":
            data = read_json(args.inputs)
            if not isinstance(data, dict):
                raise ValueError("precision inputs must be an object")
            data = {**data, "precision": dict(resamples=args.resamples, seed=args.bootstrap_seed,
                confidence_level=args.confidence_level)}
            result = write_precision_bundle(data, args.out)
        else:
            result = verify_bundle(args.out) if args.command == "verify" else write_bundle(demo_inputs(args.paths,args.seed) if args.command == "demo" else read_json(args.inputs), args.out)
    except (ValueError, OSError, KeyError) as exc:
        print(f"stressatlas: {exc}", file=sys.stderr)
        return 2
    if args.command == "precision":
        result = {key: result[key] for key in ("simulation_paths", "resamples", "interval_rows", "warnings")}
    elif args.command not in ("verify", "verify-precision"):
        result = {key: result[key] for key in ("loans", "obligors", "config", "scenarios", "warnings")}
    print(json.dumps(result, indent=2, ensure_ascii=False, allow_nan=False))
    return 0
