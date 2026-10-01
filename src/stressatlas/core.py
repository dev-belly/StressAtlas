"""A global/sector Gaussian factor model; one default event per obligor."""

from dataclasses import dataclass
from decimal import Decimal, ROUND_CEILING
from hashlib import sha256
import math

import numpy as np
from scipy.special import ndtri


def finite(value, name, lower=0, upper=None, positive=False):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < lower or (positive and value == lower) or (upper is not None and value > upper):
        raise ValueError(f"invalid {name}: {value!r}")


def identifier(value, name):
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        raise ValueError(f"{name} must be a nonempty, trimmed string")


@dataclass(frozen=True)
class Loan:
    loan_id: str
    obligor_id: str
    sector: str
    ead: float
    pd: float
    lgd: float
    rho: float

    def __post_init__(self):
        for name in ("loan_id", "obligor_id", "sector"):
            identifier(getattr(self, name), name)
        finite(self.ead, "ead", upper=1e15, positive=True)
        for name in ("pd", "lgd", "rho"):
            finite(getattr(self, name), name, upper=1)


@dataclass(frozen=True)
class SectorShock:
    sector: str
    pd_odds_multiplier: float = 1.0
    lgd_add: float = 0.0
    ead_multiplier: float = 1.0
    rho_multiplier: float = 1.0

    def __post_init__(self):
        identifier(self.sector, "sector")
        _validate_shock(self)


def _validate_shock(shock):
    for name in ("pd_odds_multiplier", "ead_multiplier"):
        finite(getattr(shock, name), name, upper=1000, positive=True)
    finite(shock.rho_multiplier, "rho_multiplier", upper=1000)
    finite(shock.lgd_add, "lgd_add", lower=-1, upper=1)


@dataclass(frozen=True)
class Scenario:
    name: str
    pd_odds_multiplier: float = 1.0
    lgd_add: float = 0.0
    ead_multiplier: float = 1.0
    rho_multiplier: float = 1.0
    sector_shocks: tuple[SectorShock, ...] = ()

    def __post_init__(self):
        identifier(self.name, "scenario name")
        _validate_shock(self)
        if not isinstance(self.sector_shocks, tuple) or any(not isinstance(s, SectorShock) for s in self.sector_shocks):
            raise ValueError("sector_shocks must be a tuple of SectorShock")
        if len({s.sector for s in self.sector_shocks}) != len(self.sector_shocks):
            raise ValueError("duplicate sector shock")


def validate_portfolio(loans):
    loans = list(loans)
    if not loans or any(not isinstance(l, Loan) for l in loans):
        raise ValueError("portfolio requires at least one Loan")
    if len({l.loan_id for l in loans}) != len(loans):
        raise ValueError("duplicate loan_id")
    borrowers = {}
    for loan in loans:
        terms = (loan.sector, loan.pd, loan.rho)
        if loan.obligor_id in borrowers and borrowers[loan.obligor_id] != terms:
            raise ValueError("loans to the same obligor must share sector, PD and asset correlation")
        borrowers[loan.obligor_id] = terms
    return sorted(loans, key=lambda l: l.loan_id)


@dataclass(frozen=True)
class Drivers:
    obligors: tuple[str, ...]
    sectors: tuple[str, ...]
    seed: int
    global_z: np.ndarray
    sector_z: np.ndarray
    idio_z: np.ndarray
    fingerprint: str

    @property
    def paths(self):
        return self.global_z.size


def make_drivers(loans, paths=20000, seed=20261001):
    loans = validate_portfolio(loans)
    if type(paths) is not int or paths < 2 or type(seed) is not int or not 0 <= seed < 2**32:
        raise ValueError("paths must be an integer >=2; seed must be a uint32 integer")
    obligors = tuple(sorted({l.obligor_id for l in loans}))
    sectors = tuple(sorted({l.sector for l in loans}))
    streams = [np.random.default_rng(s) for s in np.random.SeedSequence(seed).spawn(3)]
    global_z = streams[0].standard_normal(paths)
    sector_z = streams[1].standard_normal((paths, len(sectors)))
    idio_z = streams[2].standard_normal((paths, len(obligors)))
    digest = sha256(repr((obligors, sectors, paths, seed, "PCG64")).encode())
    for array in (global_z, sector_z, idio_z):
        digest.update(array.astype("<f8", copy=False).tobytes())
        array.setflags(write=False)
    return Drivers(obligors, sectors, seed, global_z, sector_z, idio_z, digest.hexdigest())


def stressed_terms(loan, scenario):
    sector = next((s for s in scenario.sector_shocks if s.sector == loan.sector), None)
    odds = scenario.pd_odds_multiplier * (sector.pd_odds_multiplier if sector else 1)
    # Exact endpoints: PD=0 stays 0; PD=1 stays 1.
    pd = loan.pd * odds / (1 - loan.pd + loan.pd * odds)
    lgd = min(1.0, max(0.0, loan.lgd + scenario.lgd_add + (sector.lgd_add if sector else 0)))
    ead = loan.ead * scenario.ead_multiplier * (sector.ead_multiplier if sector else 1)
    rho = min(1.0, loan.rho * scenario.rho_multiplier * (sector.rho_multiplier if sector else 1))
    return pd, lgd, ead, rho


@dataclass(frozen=True)
class Simulation:
    name: str
    losses: np.ndarray
    sector_losses: np.ndarray
    sectors: tuple[str, ...]
    analytic_el: float
    stressed_ead: float
    default_rates: dict[str, float]
    driver_fingerprint: str


def simulate(loans, scenario, drivers, global_share=0.5, chunk_size=4096):
    loans = validate_portfolio(loans)
    if not isinstance(scenario, Scenario) or not isinstance(drivers, Drivers):
        raise ValueError("expected Scenario and Drivers")
    finite(global_share, "global_share", upper=1)
    if type(chunk_size) is not int or chunk_size < 1:
        raise ValueError("chunk_size must be a positive integer")
    if tuple(sorted({l.obligor_id for l in loans})) != drivers.obligors or tuple(sorted({l.sector for l in loans})) != drivers.sectors:
        raise ValueError("driver bank belongs to a different obligor/sector universe")
    if any(s.sector not in drivers.sectors for s in scenario.sector_shocks):
        raise ValueError("scenario contains an unknown sector")
    positions = {name: i for i, name in enumerate(drivers.obligors)}
    sectors = {name: i for i, name in enumerate(drivers.sectors)}
    amount = np.zeros(len(positions))
    threshold = np.zeros(len(positions))
    correlation = np.zeros(len(positions))
    sector_idx = np.zeros(len(positions), dtype=int)
    analytic_el, total_ead = 0.0, 0.0
    for loan in loans:
        pd, lgd, ead, rho = stressed_terms(loan, scenario)
        index = positions[loan.obligor_id]
        amount[index] += ead * lgd
        threshold[index] = ndtri(pd)
        correlation[index] = rho
        sector_idx[index] = sectors[loan.sector]
        analytic_el += pd * ead * lgd
        total_ead += ead
    losses = np.zeros(drivers.paths)
    sector_losses = np.zeros((drivers.paths, len(sectors)))
    default_counts = np.zeros(len(positions), dtype=np.int64)
    # Drivers were generated once. Chunking only bounds intermediate memory.
    for start in range(0, drivers.paths, chunk_size):
        stop = min(start + chunk_size, drivers.paths)
        common = math.sqrt(global_share) * drivers.global_z[start:stop, None] + math.sqrt(1 - global_share) * drivers.sector_z[start:stop, sector_idx]
        latent = common * np.sqrt(correlation) + drivers.idio_z[start:stop] * np.sqrt(1 - correlation)
        defaults = latent < threshold
        default_counts += defaults.sum(axis=0)
        loan_losses = defaults * amount
        for name, index in sectors.items():
            sector_losses[start:stop, index] = loan_losses[:, sector_idx == index].sum(axis=1)
        losses[start:stop] = sector_losses[start:stop].sum(axis=1)
    losses.setflags(write=False)
    sector_losses.setflags(write=False)
    return Simulation(scenario.name, losses, sector_losses, drivers.sectors, analytic_el, total_ead, {name: float(default_counts[i]/drivers.paths) for i, name in enumerate(drivers.obligors)}, drivers.fingerprint)


def tail_weights(losses, alpha=0.99):
    finite(alpha, "alpha", upper=1, positive=True)
    if alpha == 1:
        raise ValueError("alpha must be strictly below 1")
    raw = np.asarray(losses)
    if raw.dtype.kind not in "iuf" or raw.ndim != 1 or not raw.size:
        raise ValueError("losses must be a nonempty one-dimensional numeric array")
    losses = raw.astype(float)
    if not np.all(np.isfinite(losses)) or np.any(losses < 0):
        raise ValueError("losses must be finite and nonnegative")
    n = losses.size
    alpha_decimal = Decimal(str(alpha))
    tail_mass = float((Decimal(1) - alpha_decimal) * n)
    quantile_index = int((alpha_decimal * n).to_integral_value(rounding=ROUND_CEILING)) - 1
    order = np.argsort(-losses, kind="stable")
    weights = np.zeros(n)
    full = int(math.floor(tail_mass))
    weights[order[:full]] = 1
    if tail_mass > full:
        weights[order[full]] = tail_mass - full
    # Split boundary weight evenly across tied losses so attribution is order-invariant.
    boundary = losses[order[max(0, int(math.ceil(tail_mass)) - 1)]]
    tied = losses == boundary
    weights[tied] = (tail_mass - float(np.sum(losses > boundary))) / tied.sum()
    weights /= tail_mass
    var = float(np.sort(losses)[quantile_index])
    return var, weights, tail_mass


def tail_metrics(losses, alpha=0.99):
    var, weights, tail_mass = tail_weights(losses, alpha)
    array = np.asarray(losses, dtype=float)
    return {"mean_loss": float(array.mean()), "mean_mc_se": float(array.std(ddof=1)/math.sqrt(array.size)) if array.size > 1 else 0.0, "var": var, "es": float(np.sum(weights * array)), "tail_mass": tail_mass, "alpha": alpha}


def sector_attribution(simulation, alpha=0.99):
    _, weights, _ = tail_weights(simulation.losses, alpha)
    return {name: float(np.sum(weights * simulation.sector_losses[:, i])) for i, name in enumerate(simulation.sectors)}


def paired_delta(current, baseline):
    if current.driver_fingerprint != baseline.driver_fingerprint or current.losses.shape != baseline.losses.shape:
        raise ValueError("paired comparison requires the same random driver bank")
    delta = current.losses - baseline.losses
    return {"mean_delta": float(delta.mean()), "paired_mc_se": float(delta.std(ddof=1)/math.sqrt(delta.size))}
