import socket

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session

import core.db.models  # noqa: F401  (register tables on Base.metadata before create_all)
from core.db.base import Base


@pytest.fixture()
def engine():
    eng = create_engine("sqlite+pysqlite:///:memory:", future=True)

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


@pytest.fixture(autouse=True)
def _block_network(request, monkeypatch):
    """Block outbound network sockets in unit tests (AGENTS.md test-isolation rule).

    Unit tests must not reach Postgres, LLM providers, market data, news, search, or the
    harness. SQLite in-memory and pure functions do not open sockets, so they are
    unaffected. Opt out per-test with ``@pytest.mark.allow_network`` (reviewed exceptions
    only); ``@pytest.mark.integration`` tests live under tests/integration and are opt-in.
    """

    if request.node.get_closest_marker("allow_network"):
        yield
        return

    def _guarded(*args, **kwargs):
        raise RuntimeError(
            "network access blocked in unit tests; mark @pytest.mark.allow_network to opt in"
        )

    monkeypatch.setattr(socket, "socket", _guarded)
    yield
