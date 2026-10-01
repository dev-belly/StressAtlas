from dataclasses import asdict

from .core import Loan, Scenario, SectorShock


def demo_inputs(paths=20000, seed=20261001):
    sectors = ("manufacturing", "retail", "real_estate", "technology")
    portfolio = []
    for i in range(80):
        sector = sectors[i % 4]
        # Deterministic synthetic inputs, not fitted to any bank's borrowers.
        pd = (0.018, 0.035, 0.055, 0.025)[i % 4] + (i % 7) * 0.002
        rho = (0.15, 0.12, 0.25, 0.18)[i % 4]
        for tranche in range(2):
            portfolio.append(asdict(Loan(f"L{i:03d}-{tranche}", f"B{i:03d}", sector, float(500000 + (i % 11)*100000 + tranche*150000), pd, 0.40 + (i%4)*0.05 + tranche*0.05, rho)))
    scenarios = [Scenario("baseline"), Scenario("recession", pd_odds_multiplier=2, lgd_add=0.08, sector_shocks=(SectorShock("real_estate", pd_odds_multiplier=1.5),)), Scenario("severe", pd_odds_multiplier=4, lgd_add=0.15, ead_multiplier=1.1, sector_shocks=(SectorShock("real_estate", pd_odds_multiplier=1.5),)), Scenario("independent", rho_multiplier=0), Scenario("high_correlation", rho_multiplier=2)]
    return {"data_kind": "synthetic", "portfolio": portfolio, "scenarios": [asdict(s) for s in scenarios], "config": {"paths": paths, "seed": seed, "alpha": 0.99, "global_share": 0.5}}
