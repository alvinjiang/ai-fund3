"""Contributor resolution — SPEC-DOMAIN §4.3.

When a coverage has no explicit ``coverage_contributors`` rows, the contributor set is
``all houses where enabled AND assignable AND key != lead_house`` (a meta house is never
a contributor). The orchestrator calls this at run-creation time and freezes the result
into the run's stage rows, so a later ``houses.yaml`` edit cannot retroactively change a
completed run's attribution. Explicit ``coverage_contributors`` rows (a PM override) are
read directly by the repository and bypass this function.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class HouseInfo:
    key: str
    enabled: bool
    assignable: bool
    meta: bool = False


def resolve_contributors(lead_house: str, houses: list[HouseInfo]) -> list[str]:
    """Default contributor set: enabled + assignable + non-meta houses, excluding the lead."""
    return sorted(
        h.key for h in houses if h.enabled and h.assignable and not h.meta and h.key != lead_house
    )
