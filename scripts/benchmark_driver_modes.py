"""Compare complete report bytes, peak allocations and time in fresh processes.

Install StressAtlas first, then run this file from the repository root. No
performance threshold is asserted: hardware, builds and allocators differ.
"""

import argparse
import json
import platform
import subprocess
import sys
import time
import tracemalloc
from hashlib import sha256


def measure(args):
    import numpy as np
    import scipy

    from stressatlas.bundle import artifacts

    data = {
        "data_kind": "synthetic",
        "portfolio": [
            dict(
                loan_id=f"L{i:06d}",
                obligor_id=f"B{i:06d}",
                sector=f"S{i % args.sectors}",
                ead=100000 + i * 100,
                pd=0.02 + (i % 7) * 0.005,
                lgd=0.45,
                rho=0.2,
            )
            for i in range(args.obligors)
        ],
        "scenarios": [
            dict(name="baseline"),
            dict(name="higher_pd", pd_odds_multiplier=2),
            dict(name="higher_correlation", rho_multiplier=2),
        ],
        "config": dict(paths=args.paths, seed=42, alpha=0.99, global_share=0.5),
    }
    tracemalloc.start()
    started = time.perf_counter()
    files = artifacts(data, driver_mode=args.worker_mode)
    elapsed = time.perf_counter() - started
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    digest = sha256()
    for name, content in sorted(files.items()):
        digest.update(name.encode() + b"\0" + content.encode() + b"\0")
    rss = None
    if sys.platform in ("linux", "darwin"):
        import resource

        raw = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        rss = raw / (1024**2 if sys.platform == "darwin" else 1024)
    return dict(
        mode=args.worker_mode,
        elapsed_seconds=round(elapsed, 4),
        peak_traced_mib=round(peak / 1024**2, 3),
        process_peak_rss_mib=round(rss, 3) if rss is not None else None,
        artifact_sha256=digest.hexdigest(),
        python=platform.python_version(),
        numpy=np.__version__,
        scipy=scipy.__version__,
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--paths", type=int, default=50000)
    parser.add_argument("--obligors", type=int, default=400)
    parser.add_argument("--sectors", type=int, default=4)
    parser.add_argument(
        "--worker-mode", choices=("memory", "stream"), help=argparse.SUPPRESS
    )
    args = parser.parse_args()
    if args.paths < 2 or args.obligors < 1 or not 1 <= args.sectors <= args.obligors:
        parser.error("paths >= 2 and 1 <= sectors <= obligors are required")
    if args.worker_mode:
        print(json.dumps(measure(args)))
        return
    rows = []
    for mode in ("memory", "stream"):
        command = [
            sys.executable,
            __file__,
            "--paths",
            str(args.paths),
            "--obligors",
            str(args.obligors),
            "--sectors",
            str(args.sectors),
            "--worker-mode",
            mode,
        ]
        result = subprocess.run(command, check=True, capture_output=True, text=True)
        rows.append(json.loads(result.stdout))
    if rows[0]["artifact_sha256"] != rows[1]["artifact_sha256"]:
        raise SystemExit("Execution modes generated different report artifacts")
    print(
        json.dumps(
            dict(
                paths=args.paths,
                obligors=args.obligors,
                sectors=args.sectors,
                scenarios=3,
                identical_artifacts=True,
                measurements=rows,
            ),
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
