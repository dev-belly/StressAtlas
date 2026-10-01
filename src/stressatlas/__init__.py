"""StressAtlas: reproducible credit loss scenarios, with explicit assumptions."""

from .core import Loan, Scenario, SectorShock, make_drivers, simulate, tail_metrics

__version__ = "0.1.0"
__all__ = ["Loan", "Scenario", "SectorShock", "make_drivers", "simulate", "tail_metrics"]
