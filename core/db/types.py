"""Cross-dialect column types and the money-path helpers (SPEC-DOMAIN §2).

Money is always ``Numeric`` — never ``float`` (the deterministic-money-path rule). The
``JsonType`` emits ``JSONB`` on Postgres and ``JSON`` on SQLite, so the same models serve
production and in-memory unit tests. ``utc_now`` is the only clock helper app code uses
for ``DateTime(timezone=True)`` columns.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import Numeric
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.types import JSON, TypeDecorator


def utc_now() -> datetime:
    """Aware UTC now — for ``DateTime(timezone=True)`` columns and ``onupdate``."""
    return datetime.now(UTC)


class JsonType(TypeDecorator):
    """JSONB on Postgres, JSON elsewhere (SQLite unit tests)."""

    impl = JSON
    cache_ok = True

    def load_dialect_impl(self, dialect):
        if dialect.name == "postgresql":
            return dialect.type_descriptor(JSONB())
        return dialect.type_descriptor(JSON())


class Money(Numeric):
    """Cash amounts — ``Numeric(20, 2)``."""

    def __init__(self) -> None:
        super().__init__(20, 2)


class Price(Numeric):
    """Prices / values — ``Numeric(20, 6)`` (6 dp survives FX/multiples exactly)."""

    def __init__(self) -> None:
        super().__init__(20, 6)


class Cost(Numeric):
    """LLM / API costs — ``Numeric(12, 6)``."""

    def __init__(self) -> None:
        super().__init__(12, 6)


class Prob(Numeric):
    """Probabilities / confidence — ``Numeric(5, 4)`` in [0, 1]."""

    def __init__(self) -> None:
        super().__init__(5, 4)
