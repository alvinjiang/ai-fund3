"""Shared SQLite fixtures for unit DB tests (SPEC-DOMAIN §9.2).

In-memory SQLite stands in for Postgres for dialect-independent behaviour only; the
``JsonType``/``Uuid`` variants make the models portable. Postgres-only behaviour
(``SKIP LOCKED``, partial unique indexes, ``alembic check``, numeric fidelity) lives under
``tests/integration/db`` and is opt-in.
"""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session

from core.db.base import Base


@pytest.fixture()
def engine():
    eng = create_engine("sqlite+pysqlite:///:memory:", future=True)

    # SQLite does not enforce FKs unless asked; mirror Postgres RESTRICT/CASCADE behaviour.
    @event.listens_for(eng, "connect")
    def _fk_on(dbapi_conn, _):
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA foreign_keys=ON")
        cur.close()

    Base.metadata.create_all(eng)
    return eng


@pytest.fixture()
def session(engine):
    with Session(engine) as s:
        yield s
