"""Read a pinned, attributed subset of estimated continuously compounded spot curves."""

import hashlib
import json
from importlib.resources import files

import numpy as np
import polars as pl
from nelson_siegel_svensson import NelsonSiegelSvenssonCurve

TENORS = np.array([1.0, 2.0, 3.0, 5.0, 7.0, 10.0, 20.0, 30.0])
PARAMETERS = ("BETA0", "BETA1", "BETA2", "BETA3", "TAU1", "TAU2")


def load_market():
    resource = files("curveshift").joinpath("data")
    raw = resource.joinpath("fed_gsw_2016_2025.csv").read_bytes()
    source = json.loads(resource.joinpath("source.json").read_text(encoding="utf-8"))
    if hashlib.sha256(raw).hexdigest() != source["subset_sha256"]:
        raise ValueError("Bundled curve source hash differs")
    frame = pl.read_csv(raw, try_parse_dates=True)
    if frame["Date"].n_unique() != frame.height or not frame["Date"].is_sorted():
        raise ValueError("Curve dates must be sorted and unique")
    numeric = frame.select(pl.exclude("Date")).to_numpy()
    if not np.isfinite(numeric).all() or (frame.select("TAU1", "TAU2").to_numpy() <= 0).any():
        raise ValueError("Curve parameters and yields must be finite; taus must be positive")
    return frame, source


def curve_from_row(row):
    # Fed beta and SVENY columns are in percent; taus remain in years.
    return NelsonSiegelSvenssonCurve(
        *[float(row[key]) / 100 for key in PARAMETERS[:4]],
        float(row["TAU1"]),
        float(row["TAU2"]),
    )


def select_curve(frame, as_of):
    from datetime import date

    cutoff = date.fromisoformat(as_of)
    if cutoff > frame["Date"].max():
        raise ValueError("Requested date is beyond the bundled historical curve window")
    selected = frame.filter(pl.col("Date") <= cutoff)
    if selected.is_empty():
        raise ValueError("No bundled curve at or before the requested date")
    row = selected.row(-1, named=True)
    return row, curve_from_row(row)


def check_reported_yields(frame):
    errors = []
    for row in frame.iter_rows(named=True):
        model = curve_from_row(row)(TENORS.copy()) * 100
        recorded = np.array([row[f"SVENY{int(t):02d}"] for t in TENORS])
        errors.extend(np.abs(model - recorded).tolist())
    return {"observations": frame.height, "tenors": len(TENORS), "max_error_bp": float(max(errors) * 100)}
