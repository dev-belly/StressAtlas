import argparse
import json
import sys

from .bundle import read_json, verify_bundle, write_bundle
from .demo import demo_inputs


def main(argv=None):
    parser = argparse.ArgumentParser(description="Paired credit stress scenarios with finite-sample tail attribution")
    sub = parser.add_subparsers(dest="command", required=True)
    for command in ("demo", "run", "verify"):
        p = sub.add_parser(command)
        p.add_argument("--out", default="outputs")
        if command == "run":
            p.add_argument("--inputs", required=True)
        elif command == "demo":
            p.add_argument("--paths", type=int, default=20000)
            p.add_argument("--seed", type=int, default=20261001)
    args = parser.parse_args(argv)
    try:
        result = verify_bundle(args.out) if args.command == "verify" else write_bundle(demo_inputs(args.paths,args.seed) if args.command == "demo" else read_json(args.inputs), args.out)
    except (ValueError, OSError, KeyError) as exc:
        print(f"stressatlas: {exc}", file=sys.stderr)
        return 2
    if args.command != "verify":
        result = {key: result[key] for key in ("loans", "obligors", "config", "scenarios", "warnings")}
    print(json.dumps(result, indent=2, ensure_ascii=False, allow_nan=False))
    return 0
