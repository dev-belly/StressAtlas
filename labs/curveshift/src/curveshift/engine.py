"""QuantLib cash flows, full curve revaluation and auditable rate sensitivities."""

import threading
from dataclasses import dataclass
from datetime import date

import numpy as np
import QuantLib as ql

KEYS = np.array([2.0, 5.0, 10.0, 30.0])
_QL_LOCK = threading.RLock()


@dataclass(frozen=True)
class BondSpec:
    name: str
    years: int
    coupon: float = 0.04
    notional: float = 2_500_000


DEFAULT_BOOK = tuple(BondSpec(f"{years}Y", years) for years in (2, 5, 10, 30))


def qdate(value):
    value = date.fromisoformat(value) if isinstance(value, str) else value
    return ql.Date(value.day, value.month, value.year)


def key_basis(times):
    """Piecewise-linear zero-rate shifts; constant outside the first/last key."""
    return np.column_stack([np.interp(times, KEYS, np.eye(4)[i]) for i in range(4)])


def build_book(as_of, specs=DEFAULT_BOOK):
    """Hypothetical 4%-coupon, semiannual, unadjusted bonds; all amounts in USD."""
    valuation = qdate(as_of)
    result = []
    with _QL_LOCK:
        old_date = ql.Settings.instance().evaluationDate
        ql.Settings.instance().evaluationDate = valuation
        try:
            for spec in specs:
                if not (0 <= spec.coupon <= 1 and 1 <= spec.years <= 30 and 0 < spec.notional <= 1e12):
                    raise ValueError("Invalid hypothetical bond convention or notional")
                schedule = ql.Schedule(
                    valuation,
                    valuation + ql.Period(spec.years, ql.Years),
                    ql.Period(6, ql.Months),
                    ql.NullCalendar(),
                    ql.Unadjusted,
                    ql.Unadjusted,
                    ql.DateGeneration.Backward,
                    False,
                )
                bond = ql.FixedRateBond(
                    0,
                    100.0,
                    schedule,
                    [spec.coupon],
                    ql.Thirty360(ql.Thirty360.BondBasis),
                    ql.Unadjusted,
                    100.0,
                    valuation,
                    ql.NullCalendar(),
                )
                flows = [flow for flow in bond.cashflows() if flow.date() > valuation]
                result.append(
                    {
                        "name": spec.name,
                        "years": spec.years,
                        "coupon": spec.coupon,
                        "notional": spec.notional,
                        "dates": [flow.date().ISO() for flow in flows],
                        "times": np.array([ql.Actual365Fixed().yearFraction(valuation, flow.date()) for flow in flows]),
                        "amounts": np.array([flow.amount() * spec.notional / 100 for flow in flows]),
                    }
                )
        finally:
            ql.Settings.instance().evaluationDate = old_date
    return result


def validate_shock(shock):
    value = np.asarray(shock, dtype=float)
    if value.shape != (4,) or not np.isfinite(value).all() or np.max(np.abs(value)) > 300:
        raise ValueError("Provide four finite key-rate shifts between -300 and +300 basis points")
    return value


def cashflow_price(times, amounts, zero_rates):
    discounts = np.exp(-np.asarray(times) * np.asarray(zero_rates))
    if not np.isfinite(discounts).all() or (discounts <= 0).any():
        raise ValueError("Invalid discount factors")
    return float(np.dot(amounts, discounts))


def price_bond(bond, curve, shock=(0.0, 0.0, 0.0, 0.0)):
    shift = key_basis(bond["times"]) @ validate_shock(shock) / 10000
    return cashflow_price(bond["times"], bond["amounts"], curve(bond["times"].copy()) + shift)


def quantlib_price(bond, as_of, curve, shock=(0.0, 0.0, 0.0, 0.0)):
    """A separate QuantLib pricing engine over the same schedule and node discounts."""
    valuation = qdate(as_of)
    shift = key_basis(bond["times"]) @ validate_shock(shock) / 10000
    discounts = np.exp(-bond["times"] * (curve(bond["times"].copy()) + shift))
    nodes = {qdate(d).serialNumber(): float(df) for d, df in zip(bond["dates"], discounts, strict=True)}
    nodes[valuation.serialNumber()] = 1.0
    dates = [ql.Date(serial) for serial in sorted(nodes)]
    with _QL_LOCK:
        old_date = ql.Settings.instance().evaluationDate
        ql.Settings.instance().evaluationDate = valuation
        try:
            schedule = ql.Schedule(
                valuation,
                valuation + ql.Period(bond["years"], ql.Years),
                ql.Period(6, ql.Months),
                ql.NullCalendar(),
                ql.Unadjusted,
                ql.Unadjusted,
                ql.DateGeneration.Backward,
                False,
            )
            instrument = ql.FixedRateBond(
                0,
                bond["notional"],
                schedule,
                [bond["coupon"]],
                ql.Thirty360(ql.Thirty360.BondBasis),
                ql.Unadjusted,
                100.0,
                valuation,
                ql.NullCalendar(),
            )
            term = ql.DiscountCurve(dates, [nodes[d.serialNumber()] for d in dates], ql.Actual365Fixed())
            instrument.setPricingEngine(ql.DiscountingBondEngine(ql.YieldTermStructureHandle(term)))
            return float(instrument.NPV())
        finally:
            ql.Settings.instance().evaluationDate = old_date


def sensitivities(bond, curve):
    times, amounts = bond["times"], bond["amounts"]
    present = amounts * np.exp(-times * curve(times.copy()))
    basis = key_basis(times)
    dv01 = (present * times) @ basis / 10000
    gamma = (basis.T * (present * times**2)) @ basis / 10000**2
    pv = float(present.sum())
    return {
        "pv": pv,
        "key_dv01": dv01.tolist(),
        "gamma_per_bp2": gamma.tolist(),
        "parallel_dv01": float(dv01.sum()),
        "effective_duration": float(np.dot(present, times) / pv),
        "convexity": float(np.dot(present, times**2) / pv),
    }


def scenario(book, curve, shock):
    shock = validate_shock(shock)
    rows = []
    for bond in book:
        risk = sensitivities(bond, curve)
        full = price_bond(bond, curve, shock)
        linear = -float(np.dot(risk["key_dv01"], shock))
        second = linear + 0.5 * float(shock @ np.asarray(risk["gamma_per_bp2"]) @ shock)
        rows.append(
            {
                "name": bond["name"],
                "base_pv": risk["pv"],
                "shocked_pv": full,
                "full_pnl": full - risk["pv"],
                "linear_pnl": linear,
                "second_order_pnl": second,
                "linear_error": full - risk["pv"] - linear,
            }
        )
    return {
        "shock_bp": shock.tolist(),
        "bonds": rows,
        **{
            name: sum(row[name] for row in rows)
            for name in ("base_pv", "shocked_pv", "full_pnl", "linear_pnl", "second_order_pnl", "linear_error")
        },
    }
