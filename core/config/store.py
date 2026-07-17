"""Config store — atomic-snapshot access to houses + fund + pricing (SPEC-CORE §2.5).

A single module-level reference to a frozen ``Config`` snapshot, mutated only by atomic
rebind. Concurrent readers always see one consistent version; runs additionally pin their
resolved config into ``runs.params`` at creation. ``reload`` validates the new files before
rebinding — an invalid file leaves the previous snapshot active.
"""

from __future__ import annotations

from dataclasses import dataclass

from core.config.fund import FundConfig, load_fund
from core.config.houses import HousesConfig, load_houses
from core.config.pricing import PricingConfig, load_pricing


@dataclass(frozen=True)
class Config:
    houses: HousesConfig
    fund: FundConfig
    pricing: PricingConfig


_config: Config | None = None


def get_config() -> Config:
    if _config is None:
        raise RuntimeError("config not loaded — call set_config(...) or reload_config(...) first")
    return _config


def set_config(cfg: Config) -> None:
    """Atomic rebind (test/reload hook)."""
    global _config
    _config = cfg


def reload_config(*, houses_yaml: str, fund_yaml: str, pricing_yaml: str) -> Config:
    """Validate all three sources, then rebind atomically.

    Raises on any validation error (the previous snapshot stays active); the caller logs.
    """
    new = Config(
        houses=load_houses(houses_yaml),
        fund=load_fund(fund_yaml),
        pricing=load_pricing(pricing_yaml),
    )
    set_config(new)
    return new
