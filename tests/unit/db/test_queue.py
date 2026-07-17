"""Queue — SPEC-DOMAIN §6.3 / §8 / §9.2.

Claims, heartbeats, and the cost rollup (failed attempts cost real money and must
count). The SKIP LOCKED exclusivity, dependency gating, and lease/reaper under real
concurrency are covered by integration tests; the *logic around* the queue (cost
attribution, attempt rows, retry vs terminal) is unit-tested here on SQLite.
"""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

from core.db import models, queue
from core.db.repo import coverage as cov_repo
from core.db.repo import runs as runs_repo
from core.db.schemas import AttemptIn, StageIn
from core.db.types import utc_now


def _setup(session):
    for k in ("gpt", "gemini"):
        session.add(models.House(key=k, display_name=k.upper(), provider="openai"))
    session.flush()
    c = cov_repo.propose(session, "2267", "tse", "Yakult", "JPY", "pm1")
    session.flush()
    r = runs_repo.create_run(
        session,
        coverage_id=c.id,
        type="initiation",
        trigger="pm",
        params={"lead_house": "gpt"},
        priority=100,
        budget_cap_usd=None,
        doctrine_version_id=None,
    )
    runs_repo.add_stages(
        session, r.id, [StageIn(seq=1, role="author", house="gpt", substrate="harness")]
    )
    runs_repo.start_run(session, r.id)
    return c, r


def test_claim_returns_none_when_no_run_running(session):
    # create a run but do NOT start it -> no claimable stage
    for k in ("gpt",):
        session.add(models.House(key=k, display_name=k.upper(), provider="openai"))
    session.flush()
    c = cov_repo.propose(session, "2267", "tse", "Yakult", "JPY", "pm1")
    session.flush()
    r = runs_repo.create_run(
        session,
        coverage_id=c.id,
        type="initiation",
        trigger="pm",
        params={"lead_house": "gpt"},
        priority=100,
        budget_cap_usd=None,
        doctrine_version_id=None,
    )
    runs_repo.add_stages(
        session, r.id, [StageIn(seq=1, role="author", house="gpt", substrate="harness")]
    )
    assert queue.claim_stage(session, "w1", lease_seconds=300) is None


def test_cost_rollup_counts_failed_attempts(session):
    _c, r = _setup(session)
    run_id = r.id

    stage = queue.claim_stage(session, "w1", lease_seconds=300)
    assert stage is not None
    queue.fail_stage(
        session, stage.id, AttemptIn(worker_id="w1", cost_usd=Decimal("1.00")), retryable=True
    )

    stage = queue.claim_stage(session, "w1", lease_seconds=300)
    queue.fail_stage(
        session, stage.id, AttemptIn(worker_id="w1", cost_usd=Decimal("2.00")), retryable=True
    )

    stage = queue.claim_stage(session, "w1", lease_seconds=300)
    queue.complete_stage(
        session,
        stage.id,
        AttemptIn(worker_id="w1", cost_usd=Decimal("3.00")),
        result={"status": "completed"},
    )

    session.refresh(stage)
    assert stage.status == "succeeded"
    assert stage.cost_usd == Decimal("6.00")  # 1 + 2 + 3 — failed attempts counted
    run = session.get(models.Run, run_id)
    assert run.cost_usd == Decimal("6.00")  # invariant 3: run == sum(stages)
    assert session.query(models.StageAttempt).filter_by(stage_id=stage.id).count() == 3


def test_fail_stage_terminal_when_attempts_exhausted(session):
    _c, r = _setup(session)
    # max_attempts defaults to 3
    stage = queue.claim_stage(session, "w1", lease_seconds=300)  # attempt 1
    queue.fail_stage(session, stage.id, AttemptIn(worker_id="w1"), retryable=True)
    stage = queue.claim_stage(session, "w1", lease_seconds=300)  # attempt 2
    queue.fail_stage(session, stage.id, AttemptIn(worker_id="w1"), retryable=True)
    stage = queue.claim_stage(session, "w1", lease_seconds=300)  # attempt 3
    queue.fail_stage(session, stage.id, AttemptIn(worker_id="w1"), retryable=False)
    session.refresh(stage)
    assert stage.status == "failed"


def test_heartbeat_extends_lease_and_rejects_non_owner(session):
    _c, _r = _setup(session)
    stage = queue.claim_stage(session, "w1", lease_seconds=300)
    session.refresh(stage)
    before = stage.lease_expires_at  # naive on read-back from SQLite
    assert queue.heartbeat(session, stage.id, "w1", lease_seconds=300) is True
    session.refresh(stage)
    assert stage.lease_expires_at > before  # both naive now
    # a different worker cannot heartbeat someone else's stage
    assert queue.heartbeat(session, stage.id, "other", lease_seconds=300) is False


def test_reap_expired_requeues_under_max_and_fails_at_max(session):
    _c, _r = _setup(session)
    stage = queue.claim_stage(session, "w1", lease_seconds=300)
    # force lease into the past
    stage.lease_expires_at = utc_now() - timedelta(seconds=1)
    session.flush()
    reaped = queue.reap_expired(session, utc_now())
    assert stage.id in reaped
    session.refresh(stage)
    assert stage.status == "queued"  # attempts(1) < max(3) -> requeued
    # exhaust: claim + expire past max
    for _ in range(2):
        s = queue.claim_stage(session, "w1", lease_seconds=300)
        s.lease_expires_at = utc_now() - timedelta(seconds=1)
        session.flush()
        queue.reap_expired(session, utc_now())
    session.refresh(stage)
    assert stage.status == "failed"  # attempts exhausted via lease expiry
