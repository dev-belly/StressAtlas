"""Rebuild the registered subset from its pinned raw mirror, without updating the snapshot."""

import argparse
import csv
import hashlib
import io
import json
import math
import urllib.request
from importlib.resources import files
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--raw", help="Use an already-downloaded original CSV")
    parser.add_argument("--out", required=True, help="New CSV file destination")
    args = parser.parse_args()
    meta = json.loads(files("curveshift").joinpath("data/source.json").read_text(encoding="utf-8"))
    if args.raw:
        raw = Path(args.raw).read_bytes()
    else:
        request = urllib.request.Request(
            meta["download_url"], headers={"User-Agent": "CurveShift/0.1 public research client"}
        )
        with urllib.request.urlopen(request, timeout=60) as response:
            raw = response.read(50_000_001)
    if hashlib.sha256(raw).hexdigest() != meta["raw_sha256"]:
        raise ValueError("Raw source identity differs; do not silently label a new vintage as the frozen snapshot")
    decoded = raw.decode("utf-8")
    reader = csv.DictReader(io.StringIO(decoded[decoded.index("Date,BETA0") :]))
    rows = []
    excluded = []
    for row in reader:
        if not "2016-01-01" <= row["Date"] <= "2025-12-31":
            continue
        try:
            numeric = [float(row[name]) for name in meta["columns"][1:]]
            if not all(math.isfinite(v) for v in numeric) or float(row["TAU1"]) <= 0 or float(row["TAU2"]) <= 0:
                raise ValueError()
            rows.append({key: row[key] for key in meta["columns"]})
        except ValueError:
            excluded.append(row["Date"])
    stream = io.StringIO(newline="")
    writer = csv.DictWriter(stream, fieldnames=meta["columns"], lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    output = stream.getvalue().encode("utf-8")
    if hashlib.sha256(output).hexdigest() != meta["subset_sha256"] or excluded != meta["excluded_dates"]:
        raise ValueError("Rebuilt subset or excluded dates differ")
    target = Path(args.out)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("xb") as destination:
        destination.write(output)
    print(json.dumps({"observations": len(rows), "subset_sha256": meta["subset_sha256"], "matches_snapshot": True}))


if __name__ == "__main__":
    main()
