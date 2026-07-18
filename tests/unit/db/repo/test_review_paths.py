"""Untested paths flagged by the BUGS.md review.

- ``requeue_run`` (running -> queued transient re-entry): the coverage lock SURVIVES.
- Reaper writes ``kill_reason='lease_expired'`` on the closed attempt.
- ``monitor_tick`` starts against an already-held lock (it takes no lock itself).
"""

from __future__ import annotations

from datetime import timedelta

from core.db import models, queue
from core.db.repo import coverage as cov_repo
from core.db.repo import runs as runs_repo
from core.db.schemas import StageIn
from core.db.types import utc_now
from core.domain.backoff import BackoffPolicy
from core.domain.enums import RunType


def _setup(session):
    for k in ("gpt", "gemini", "deepseek", "glm", "qwen"):
        session.add(models.House(key=k, display_name=k.upper(), provider="openai"))
    session.flush()
    c = cov_repo.propose(session, "2267", "tse", "Yakult", "JPY", "pm1")
    session.flush()
    return c


_BACKOFF = BackoffPolicy(base_s=30, max_s=3600, jitter=0.0)


def test_requeue_run_keeps_the_coverage_lock(session):
    """The lock is held across the transient running -> queued re-entry (checklist item)."""
    c = _setup(session)
    r = runs_repo.create_run(
        session,
        coverage_id=c.id,
        type=RunType.INITIATION,
        trigger="pm",
        params={"lead_house": "gpt"},
        priority=100,
        budget_cap_usd=None,
        doctrine_version_id=None,
    )
    runs_repo.add_stages(
        session,
        r.id,
        [StageIn(seq=1, role="author", house="gpt", substrate="harness", max_attempts=3)],
    )
    assert runs_repo.start_run(session, r.id) is True
    lock = session.query(models.CoverageRunLock).filter_by(coverage_id=c.id).one()
    assert lock.run_id == r.id

    runs_repo.requeue_run(session, r.id, reason="transient provider outage")

    session.refresh(r)
    assert r.status == "queued"
    # lock retained — the run is still "in flight"; another mutating run cannot start
    retained = session.query(models.CoverageRunLock).filter_by(coverage_id=c.id).one()
    assert retained.run_id == r.id


def test_reap_expired_writes_kill_reason_on_the_closed_attempt(session):
    c = _setup(session)
    r = runs_repo.create_run(
        session,
        coverage_id=c.id,
        type=RunType.INITIATION,
        trigger="pm",
        params={"lead_house": "gpt"},
        priority=100,
        budget_cap_usd=None,
        doctrine_version_id=None,
    )
    runs_repo.add_stages(
        session,
        r.id,
        [StageIn(seq=1, role="author", house="gpt", substrate="harness", max_attempts=3)],
    )
    runs_repo.start_run(session, r.id)
    stage = queue.claim_stage(session, "w1", lease_seconds=300)
    # force the lease into the past
    stage.lease_expires_at = utc_now() - timedelta(seconds=1)
    session.flush()

    queue.reap_expired(session, utc_now(), backoff=_BACKOFF)

    attempt = session.query(models.StageAttempt).filter_by(stage_id=stage.id).one()
    assert attempt.kill_reason == "lease_expired"
    assert attempt.status == "failed"


def test_monitor_tick_starts_against_an_already_held_lock(session):
    c = _setup(session)
    init = runs_repo.create_run(
        session,
        coverage_id=c.id,
        type=RunType.INITIATION,
        trigger="pm",
        params={"lead_house": "gpt"},
        priority=100,
        budget_cap_usd=None,
        doctrine_version_id=None,
    )
    runs_repo.start_run(session, init.id)  # mutating -> acquires the coverage lock
    assert session.query(models.CoverageRunLock).filter_by(coverage_id=c.id).one().run_id == init.id

    mt = runs_repo.create_run(
        session,
        coverage_id=c.id,
        type=RunType.MONITOR_TICK,
        trigger="scheduler",
        params={},
        priority=100,
        budget_cap_usd=None,
        doctrine_version_id=None,
    )
    # BUGS #3 confirmation: monitor_tick is non-mutating by DERIVATION from run type, never
    # the column default (the server_default=true is the fail-safe; prove it's unreachable).
    assert mt.mutates_dossier is False
    # -> starts immediately, takes no lock, adds no lock row even with the lock held
    assert runs_repo.start_run(session, mt.id) is True
    session.refresh(mt)
    assert mt.status == "running"
    assert session.query(models.CoverageRunLock).filter_by(coverage_id=c.id).count() == 1
