"""Historical full revaluation and PCA fitted strictly before the evaluation year."""

import numpy as np
import polars as pl
from sklearn.decomposition import PCA

from .engine import price_bond, quantlib_price, sensitivities
from .market import TENORS, curve_from_row


def tail_risk(losses, confidence=0.99):
    """Empirical inverse-CDF VaR and exact upper-tail probability mass ES."""
    losses = np.asarray(losses, dtype=float)
    if losses.ndim != 1 or not len(losses) or not np.isfinite(losses).all() or not 0 < confidence < 1:
        raise ValueError("Finite nonempty losses and a confidence strictly between zero and one are required")
    ordered = np.sort(losses)
    var = float(ordered[max(0, int(np.ceil(confidence * len(losses))) - 1)])
    # Integrate the empirical quantile function over [confidence, 1].
    left = np.maximum(np.arange(len(losses)) / len(losses), confidence)
    right = (np.arange(len(losses)) + 1) / len(losses)
    masses = np.maximum(0.0, right - left)
    es = float(np.dot(ordered, masses) / (1 - confidence))
    return {"confidence": confidence, "var_usd": var, "es_usd": es, "observations": len(losses)}


def historical_risk(frame, as_of, book, curve, window=756, confidence=0.99):
    if not 20 <= window <= 5000:
        raise ValueError("Historical window must contain between 20 and 5000 changes")
    eligible = frame.filter(pl.col("Date") <= as_of)
    if eligible.height < window + 1:
        raise ValueError(f"Historical replay requires at least {window + 1} curve observations before the cutoff")
    chosen = list(eligible.tail(window + 1).iter_rows(named=True))
    times = np.concatenate([bond["times"] for bond in book])
    amounts = np.concatenate([bond["amounts"] for bond in book])
    levels = np.vstack([curve_from_row(row)(times.copy()) for row in chosen])
    changes = np.diff(levels, axis=0)
    base_rates = curve(times.copy())
    base = float(np.dot(amounts, np.exp(-times * base_rates)))
    repriced = np.exp(-times[None, :] * (base_rates[None, :] + changes)) @ amounts
    losses = base - repriced
    result = tail_risk(losses, confidence)
    rng = np.random.default_rng(20261111)
    estimates = []
    for _ in range(300):
        starts = rng.integers(0, len(losses) - 20 + 1, size=int(np.ceil(len(losses) / 20)))
        sample = np.concatenate([losses[start : start + 20] for start in starts])[: len(losses)]
        estimates.append(tail_risk(sample, confidence)["es_usd"])
    result.update(
        {
            "scenario_start": chosen[1]["Date"].isoformat(),
            "scenario_end": chosen[-1]["Date"].isoformat(),
            "es_block_bootstrap_95pct_usd": np.quantile(estimates, [0.025, 0.975]).tolist(),
            "bootstrap": {"resamples": 300, "block_observations": 20, "seed": 20261111},
            "losses": [
                {"from": prev["Date"].isoformat(), "to": nxt["Date"].isoformat(), "loss_usd": float(loss)}
                for prev, nxt, loss in zip(chosen[:-1], chosen[1:], losses, strict=True)
            ],
            "worst_scenario": {
                "from": chosen[int(np.argmax(losses))]["Date"].isoformat(),
                "to": chosen[int(np.argmax(losses)) + 1]["Date"].isoformat(),
                "loss_usd": float(losses.max()),
            },
        }
    )
    return result


def pca_study(frame, cutoff_year=2025):
    yield_columns = [f"SVENY{int(t):02d}" for t in TENORS]
    levels = frame.select(yield_columns).to_numpy() * 100  # percent -> basis points
    changes = np.diff(levels, axis=0)
    dates = frame["Date"].to_list()[1:]
    train = np.array([d.year < cutoff_year for d in dates])
    test = np.array([d.year == cutoff_year for d in dates])
    if train.sum() < 100 or test.sum() < 20:
        raise ValueError("PCA needs at least 100 training changes and 20 held-out changes")
    fit = PCA(n_components=3, svd_solver="full").fit(changes[train])
    components = fit.components_.copy()
    for row in components:
        if row[np.argmax(np.abs(row))] < 0:
            row *= -1
    centered = changes[test] - fit.mean_
    reconstructed = (centered @ components.T) @ components
    energy = 1 - float(np.sum((centered - reconstructed) ** 2) / np.sum(centered**2))
    return {
        "training_start": min(d for d, keep in zip(dates, train, strict=True) if keep).isoformat(),
        "training_end": max(d for d, keep in zip(dates, train, strict=True) if keep).isoformat(),
        "held_out_start": min(d for d, keep in zip(dates, test, strict=True) if keep).isoformat(),
        "held_out_end": max(d for d, keep in zip(dates, test, strict=True) if keep).isoformat(),
        "training_changes": int(train.sum()),
        "held_out_changes": int(test.sum()),
        "tenors_years": TENORS.tolist(),
        "components": components.tolist(),
        "training_variance_ratios": fit.explained_variance_ratio_.tolist(),
        "held_out_reconstruction_energy": energy,
        "mean_training_change_bp": fit.mean_.tolist(),
    }


def engine_crosscheck(book, as_of, curve):
    errors = []
    for shifts in ([0, 0, 0, 0], [50, 50, 50, 50], [-100, -100, -100, -100], [0, 25, 50, 100], [-30, 0, 10, 0]):
        for bond in book:
            errors.append(abs(price_bond(bond, curve, shifts) - quantlib_price(bond, as_of, curve, shifts)))
    return {"valuations": len(errors), "max_abs_difference_usd": max(errors)}


def duration_neutral_case(book, curve):
    short, long = book[0], book[2]
    short_risk, long_risk = sensitivities(short, curve), sensitivities(long, curve)
    ratio = long_risk["parallel_dv01"] / short_risk["parallel_dv01"]
    key_risk = np.asarray(long_risk["key_dv01"]) - ratio * np.asarray(short_risk["key_dv01"])
    outcomes = []
    for label, shock in [("parallel +50bp", [50, 50, 50, 50]), ("2Y -25bp / 10Y +25bp", [-25, 0, 25, 0])]:
        long_pnl = price_bond(long, curve, shock) - long_risk["pv"]
        short_pnl = price_bond(short, curve, shock) - short_risk["pv"]
        outcomes.append(
            {
                "scenario": label,
                "shock_bp": shock,
                "full_pnl_usd": long_pnl - ratio * short_pnl,
                "linear_pnl_usd": -float(key_risk @ shock),
            }
        )
    return {
        "long_10y_face_usd": long["notional"],
        "short_2y_face_usd": ratio * short["notional"],
        "gross_face_usd": long["notional"] + ratio * short["notional"],
        "parallel_dv01_usd": float(key_risk.sum()),
        "key_dv01_usd": key_risk.tolist(),
        "outcomes": outcomes,
    }
