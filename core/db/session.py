"""Engine + session factory (SPEC-DOMAIN §8).

Repositories take an explicit ``Session`` (dependency-injected); this module builds
engines and sessionmakers bound to a URL. No module-level session or ``scoped_session``
global — the runner and scheduler are concurrent (v2's scoped_session is deliberately
dropped). The SQLAlchemy engine's connection pool is the only process-global and it is
thread-safe by design.
"""

from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker


def make_engine(database_url: str, **kwargs):
    return create_engine(database_url, future=True, pool_pre_ping=True, **kwargs)


def make_session_factory(database_url: str, **kwargs) -> sessionmaker[Session]:
    engine = make_engine(database_url, **kwargs)
    return sessionmaker(bind=engine, class_=Session, expire_on_commit=False, autoflush=False)
