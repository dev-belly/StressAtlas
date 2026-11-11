"""Full-revaluation CVaR hedges fitted before a sealed evaluation period."""

import math
from datetime import date

import numpy as np
import polars as pl
from scipy.optimize import linprog
from scipy.sparse import csr_matrix, eye, hstack, vstack

from .engine import BondSpec, build_book, sensitivities
from .market import curve_from_row, load_market, select_curve
from .research import tail_risk

UNIT = 1_000_000.0
SCALE = 1000.0
FIT_DATE = "2024-12-31"
TARGET = (BondSpec("7Y target", 7, 0.035, 10_000_000),)
HEDGES = tuple(BondSpec(f"{t}Y hedge", t, 0.04, UNIT) for t in (2, 5, 10, 30))
BUDGETS = (0, 2_000_000, 4_000_000, 6_000_000, 8_000_000)


def finite_number(value, low, high, name):
    if type(value) not in (int, float) or not math.isfinite(value) or not low <= value <= high:
        raise ValueError(f"{name} must be a finite number in [{low}, {high}]")


def solve_cvar(target_losses, hedge_losses, budget_usd, cost_bp=2.0, confidence=0.975, per_bond_cap_usd=3_000_000):
    """Minimize empirical ES plus one entry cost; hedge columns are per $1m face.

    Signed positions are split into nonnegative buys/sells. Constraints cap
    their total gross face, including both sides, and gross face per maturity.
    Only the supplied training losses enter the sparse linear program.
    """
    finite_number(budget_usd, 0, 10_000_000, "Gross face budget")
    finite_number(cost_bp, 0, 50, "Entry cost in basis points of face")
    finite_number(confidence, 0.5, 0.995, "Confidence")
    finite_number(per_bond_cap_usd, 0, 10_000_000, "Per-bond face cap")
    target = np.asarray(target_losses, dtype=float)
    hedges = np.asarray(hedge_losses, dtype=float)
    if (
        target.ndim != 1
        or len(target) < 2
        or hedges.ndim != 2
        or hedges.shape[0] != len(target)
        or not 1 <= hedges.shape[1] <= 20
        or not np.isfinite(target).all()
        or not np.isfinite(hedges).all()
    ):
        raise ValueError("Finite target losses and aligned nonempty hedge columns are required")
    n, m = hedges.shape
    signed = hstack([csr_matrix(hedges / SCALE), csr_matrix(-hedges / SCALE)], format="csr")
    tail = hstack([signed, csr_matrix(-np.ones((n, 1))), -eye(n)], format="csr")
    gross = csr_matrix(np.r_[np.ones(2 * m), np.zeros(n + 1)][None, :])
    per_bond = hstack([eye(m), eye(m), csr_matrix((m, n + 1))], format="csr")
    matrix = vstack([tail, gross, per_bond], format="csr")
    rhs = np.r_[-target / SCALE, budget_usd / UNIT, np.full(m, per_bond_cap_usd / UNIT)]
    cost_per_unit = UNIT * cost_bp / 10000
    objective = np.r_[np.full(2 * m, cost_per_unit / SCALE), 1.0, np.full(n, 1 / ((1 - confidence) * n))]
    bounds = [(0, None)] * (2 * m) + [(None, None)] + [(0, None)] * n
    fit = linprog(
        objective,
        A_ub=matrix,
        b_ub=rhs,
        bounds=bounds,
        method="highs-ds",
        options={"primal_feasibility_tolerance": 1e-9, "dual_feasibility_tolerance": 1e-9, "time_limit": 10.0},
    )
    if not fit.success:
        raise ValueError(f"Hedge optimization failed: {fit.message}")
    positions = fit.x[:m] - fit.x[m : 2 * m]
    losses = target + hedges @ positions
    gross_face = float(np.abs(positions).sum() * UNIT)
    entry_cost = gross_face * cost_bp / 10000
    risk = tail_risk(losses, confidence)
    recomputed = risk["es_usd"] + entry_cost
    # All finite variable lower bounds are zero; all upper bounds are infinite.
    dual_objective = float(rhs @ fit.ineqlin.marginals) * SCALE
    gap = abs(float(fit.fun) * SCALE - dual_objective)
    residual = max(0.0, float(np.max(matrix @ fit.x - rhs)))
    lower_residual = max(0.0, float(-np.min(np.r_[fit.x[: 2 * m], fit.x[2 * m + 1 :]])))
    if max(residual, lower_residual) > 1e-7 or gap > 1e-3 or abs(recomputed - fit.fun * SCALE) > 1e-3:
        raise ValueError("Solver output failed independent feasibility or objective checks")
    return {
        "budget_usd": budget_usd,
        "positions_face_usd": (positions * UNIT).tolist(),
        "gross_face_usd": gross_face,
        "entry_cost_usd": entry_cost,
        "training_risk": risk,
        "objective_usd": recomputed,
        "solver": {
            "method": "scipy.optimize.linprog / HiGHS dual simplex",
            "status": int(fit.status),
            "variables": len(objective),
            "constraints": len(rhs),
            "nonzero_coefficients": int(matrix.nnz),
            "max_scaled_constraint_violation": max(residual, lower_residual),
            "primal_dual_gap_usd": gap,
            "objective_recheck_error_usd": abs(recomputed - float(fit.fun) * SCALE),
            "budget_shadow_usd_per_extra_usd_face": float(fit.ineqlin.marginals[n]) * SCALE / UNIT,
        },
    }


def scenario_losses(rows, book, curve):
    """Exact constant-cash-flow loss by bond for consecutive NSS curve changes."""
    columns = []
    for bond in book:
        times, amounts = bond["times"], bond["amounts"]
        historic = np.vstack([curve_from_row(row)(times.copy()) for row in rows])
        current = curve(times.copy())
        base = float(amounts @ np.exp(-times * current))
        shocked = np.exp(-times[None, :] * (current + np.diff(historic, axis=0))) @ amounts
        columns.append(base - shocked)
    return np.column_stack(columns)


def prepare_hedge_data(frame):
    """Freeze pricing and 504 fitting changes at 2024-12-31; seal 2025 changes."""
    row, curve = select_curve(frame, FIT_DATE)
    if row["Date"].isoformat() != FIT_DATE:
        raise ValueError("The fitting-date curve is required")
    before = frame.filter(pl.col("Date") <= row["Date"])
    after = frame.filter((pl.col("Date") > row["Date"]) & (pl.col("Date") <= date(2025, 12, 31)))
    if before.height < 505 or after.height < 20:
        raise ValueError("Require 504 fitting changes and at least 20 held-out 2025 changes")
    training_rows = list(before.tail(505).iter_rows(named=True))
    evaluation_rows = [row, *after.iter_rows(named=True)]
    book = build_book(FIT_DATE, TARGET + HEDGES)
    train = scenario_losses(training_rows, book, curve)
    test = scenario_losses(evaluation_rows, book, curve)
    return {
        "book": book,
        "curve": curve,
        "training": train,
        "evaluation": test,
        "training_dates": [
            (a["Date"].isoformat(), b["Date"].isoformat())
            for a, b in zip(training_rows[:-1], training_rows[1:], strict=True)
        ],
        "evaluation_dates": [
            (a["Date"].isoformat(), b["Date"].isoformat())
            for a, b in zip(evaluation_rows[:-1], evaluation_rows[1:], strict=True)
        ],
    }


def evaluate_hedge(data, budget_usd=6_000_000, cost_bp=2.0, confidence=0.975):
    train, test = data["training"], data["evaluation"]
    result = solve_cvar(train[:, 0], train[:, 1:], budget_usd, cost_bp, confidence)
    weights = np.r_[1.0, np.asarray(result["positions_face_usd"]) / UNIT]
    heldout = test @ weights
    result["evaluation_risk"] = tail_risk(heldout, confidence)
    # Entry is paid once; this is a net-of-entry scenario comparison, not a daily charge.
    result["evaluation_es_plus_entry_usd"] = result["evaluation_risk"]["es_usd"] + result["entry_cost_usd"]
    risks = np.array([sensitivities(bond, data["curve"])["key_dv01"] for bond in data["book"]])
    result["residual_key_dv01_usd"] = (weights @ risks).tolist()
    result["evaluation_losses"] = [
        {"from": a, "to": b, "unhedged_loss_usd": float(base), "hedged_loss_usd": float(loss)}
        for (a, b), base, loss in zip(data["evaluation_dates"], test[:, 0], heldout, strict=True)
    ]
    return result


def hedge_study(budgets=BUDGETS, cost_bp=2.0, confidence=0.975, frame=None):
    market, source = load_market()
    data = prepare_hedge_data(market if frame is None else frame)
    cases = [evaluate_hedge(data, b, cost_bp, confidence) for b in budgets]
    instruments = []
    for bond in data["book"]:
        serial = {key: value.tolist() if isinstance(value, np.ndarray) else value for key, value in bond.items()}
        serial["zero_rates"] = data["curve"](bond["times"].copy()).tolist()
        serial["risk"] = sensitivities(bond, data["curve"])
        instruments.append(serial)
    return {
        "schema_version": 1,
        "source_sha256": source["subset_sha256"],
        "pricing_date": FIT_DATE,
        "training_start": data["training_dates"][0][1],
        "training_end": data["training_dates"][-1][1],
        "training_changes": len(data["training"]),
        "evaluation_start": data["evaluation_dates"][0][1],
        "evaluation_end": data["evaluation_dates"][-1][1],
        "evaluation_changes": len(data["evaluation"]),
        "cost_bp": cost_bp,
        "confidence": confidence,
        "per_bond_cap_usd": 3_000_000,
        "instruments": instruments,
        "cases": cases,
    }
