"""Leader advisory locks (SPEC-CORE §4.4).

Two Postgres advisory locks make a second ``core`` process a warm standby instead of a
double-firer: ``orchestrator`` and ``scheduler``. This is the only cross-process guard
beyond the DB row locks in SPEC-DOMAIN. On non-Postgres (unit tests) there is no advisory
lock, so callers gate on an injected ``is_leader`` predicate instead.
"""

from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.orm import Session

ORCHESTRATOR_KEY = 71001  # stable ints passed to pg_try_advisory_lock
SCHEDULER_KEY = 71002


def pg_try_advisory_lock(session: Session, key: int) -> bool:
    """Try to take a Postgres advisory lock. Always True on non-Postgres (no advisory lock)."""
    bind = session.bind
    if bind is None or bind.dialect.name != "postgresql":
        return True
    return bool(session.execute(text("SELECT pg_try_advisory_lock(:k)"), {"k": key}).scalar())
