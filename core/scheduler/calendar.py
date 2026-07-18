"""Exchange session calendar (SPEC-CORE §4 / §8 module layout).

Parses ``fund.exchanges`` to determine per-exchange session times. Used by the scheduler
to fire ``session_tick`` at the right UTC time per exchange. Minimal: reads the session
list from the config dict; full calendar logic (pandas-market-calendars, holidays) is
deferred to the market-data port (phase 2.3).
"""

from __future__ import annotations

from core.config.fund import FundConfig


def exchange_sessions(fund: FundConfig, exchange: str) -> list[str]:
    """Return the session labels for an exchange (e.g. ``["pre:23:00", "post:06:10"]``).

    Returns an empty list if the exchange is not configured.
    """
    entry = (fund.exchanges or {}).get(exchange, {})
    return list(entry.get("sessions", []))


def all_exchanges(fund: FundConfig) -> list[str]:
    """Return all configured exchange codes."""
    return list((fund.exchanges or {}).keys())
