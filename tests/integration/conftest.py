"""Integration test infrastructure (SPEC-DOMAIN §9.3).

Opt-in: these run only with ``pytest -m integration``. A disposable Postgres is stood up
via testcontainers for the session; ``DATABASE_URL`` is set so ``settings`` and alembic's
``env.py`` both see it. Unit tests under ``tests/unit`` are unaffected (their autouse
network guard lives in a separate conftest).
"""

from __future__ import annotations

import os

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

import core.db.models  # noqa: F401  (register tables on Base.metadata)
from core.db.base import Base
from core.settings import reset_settings


def pytest_collection_modifyitems(config, items):
    """Mark integration tests and skip them unless run with -m integration.

    Tests under tests/integration get the ``integration`` marker so ``-m integration``
    selects them; without that flag they are skipped (the unit suite stays fast and
    service-free).
    """
    markexpr = config.option.markexpr or ""
    selected = "integration" in markexpr
    for item in items:
        if "tests/integration/" in item.nodeid or "tests\\integration\\" in item.nodeid:
            item.add_marker(pytest.mark.integration)
            if not selected:
                item.add_marker(
                    pytest.mark.skip(reason="integration suite is opt-in: run with -m integration")
                )


@pytest.fixture(scope="session")
def pg_url():
    from testcontainers.postgres import PostgresContainer

    pc = PostgresContainer("postgres:16-alpine")
    pc.start()
    url = pc.get_connection_url()
    os.environ["DATABASE_URL"] = url
    reset_settings()
    yield url
    pc.stop()


@pytest.fixture()
def engine(pg_url):
    eng = create_engine(pg_url, future=True, pool_pre_ping=True)
    Base.metadata.drop_all(eng)
    Base.metadata.create_all(eng)
    yield eng
    Base.metadata.drop_all(eng)
    eng.dispose()


@pytest.fixture()
def session(engine):
    factory = sessionmaker(bind=engine, class_=Session, expire_on_commit=False, autoflush=False)
    with factory() as s:
        yield s


@pytest.fixture()
def factory(engine):
    return sessionmaker(bind=engine, class_=Session, expire_on_commit=False, autoflush=False)
