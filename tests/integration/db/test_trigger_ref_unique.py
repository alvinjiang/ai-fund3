"""Scheduler double-fire idempotency via the partial unique index — SPEC-CORE §9.6.

On Postgres, ``uq_runs_trigger_ref`` (a partial unique index where trigger_ref IS NOT NULL)
rejects a second run with the same (coverage_id, type, trigger_ref), so a scheduler restart
that re-fires a job cannot create a duplicate. SQLite does not enforce this the same way
(it's checked in the job before insert), so it is integration-tested here.
"""

from __future__ import annotations

import pytest
from sqlalchemy.exc import IntegrityError

from core.db import models
from core.db.repo import coverage as cov_repo
from core.db.repo import runs as runs_repo


def test_duplicate_trigger_ref_rejected(engine, session):
    for k in ("gpt",):
        session.add(models.House(key=k, display_name=k.upper(), provider="openai"))
    session.flush()
    c = cov_repo.propose(session, "2267", "tse", "Yakult", "JPY", "pm1")
    session.flush()

    runs_repo.create_run(
        session,
        coverage_id=c.id,
        type="monitor_tick",
        trigger="scheduler",
        params={},
        priority=100,
        budget_cap_usd=None,
        doctrine_version_id=None,
        trigger_ref="watch_tick:20260105",
    )
    session.commit()

    with pytest.raises(IntegrityError):
        runs_repo.create_run(
            session,
            coverage_id=c.id,
            type="monitor_tick",
            trigger="scheduler",
            params={},
            priority=100,
            budget_cap_usd=None,
            doctrine_version_id=None,
            trigger_ref="watch_tick:20260105",
        )
        session.commit()
