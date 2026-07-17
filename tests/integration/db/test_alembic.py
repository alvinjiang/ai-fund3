"""Alembic baseline — SPEC-DOMAIN §7 / §9.3.

``upgrade head`` builds every table; ``alembic check`` reports zero drift vs
``Base.metadata`` (the migration and the models cannot diverge); ``downgrade base`` drops
everything. Uses a clean schema per test (drops + the alembic_version bookkeeping table).
"""

from __future__ import annotations

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect

import core.db.models  # noqa: F401
from core.db.base import Base


@pytest.fixture()
def clean_db(pg_url):
    eng = create_engine(pg_url, future=True)
    Base.metadata.drop_all(eng)
    with eng.begin() as conn:
        conn.exec_driver_sql("DROP TABLE IF EXISTS alembic_version")
    return eng


def test_upgrade_head_builds_tables_and_check_reports_no_diff(clean_db):
    cfg = Config("alembic.ini")
    command.upgrade(cfg, "head")

    insp = inspect(clean_db)
    names = set(insp.get_table_names())
    for required in (
        "coverage",
        "run_stages",
        "stage_attempts",
        "predictions",
        "outbox_events",
        "doctrine_versions",
        "mm_channels",
    ):
        assert required in names

    # no drift between the migrated schema and the models
    command.check(cfg)


def test_downgrade_base_drops_everything(clean_db):
    cfg = Config("alembic.ini")
    command.upgrade(cfg, "head")
    command.downgrade(cfg, "base")
    # every app table is gone; alembic's own version-tracking table may remain
    remaining = set(inspect(clean_db).get_table_names())
    assert remaining <= {"alembic_version"}
    assert "coverage" not in remaining and "run_stages" not in remaining
