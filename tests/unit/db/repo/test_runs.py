"""Runs repository — SPEC-DOMAIN §6 / §8 / §9.2.

Pins the coverage-lock discipline: a mutating run acquires the per-ticker lock on
``start_run`` (held through ``waiting_pm``), a second mutating run stays ``queued``,
``monitor_tick`` takes no lock, and a terminal ``finish_run`` releases the lock and emits
``run.finished``. Gate open/answer round out the PM-gate flow.
"""

from __future__ import annotations

from core.db import models
from core.db.repo import coverage as cov_repo
from core.db.repo import runs as runs_repo
from core.db.schemas import StageIn
from core.domain.enums import RunType


def _setup_coverage(session, lead="gpt"):
    for k in ("gpt", "gemini", "deepseek", "glm", "qwen"):
        session.add(models.House(key=k, display_name=k.upper(), provider="openai"))
    session.flush()
    c = cov_repo.propose(session, "2267", "tse", "Yakult", "JPY", "pm1")
    session.flush()
    return c


def test_start_run_acquires_lock_and_second_mutating_run_waits(session):
    c = _setup_coverage(session)
    r1 = runs_repo.create_run(
        session,
        coverage_id=c.id,
        type=RunType.INITIATION,
        trigger="pm",
        params={"lead_house": "gpt"},
        priority=100,
        budget_cap_usd=None,
        doctrine_version_id=None,
    )
    r2 = runs_repo.create_run(
        session,
        coverage_id=c.id,
        type=RunType.DEEP_REVIEW,
        trigger="pm",
        params={},
        priority=100,
        budget_cap_usd=None,
        doctrine_version_id=None,
    )
    assert runs_repo.start_run(session, r1.id) is True
    assert r1.status == "running"
    assert session.query(models.CoverageRunLock).filter_by(coverage_id=c.id).one().run_id == r1.id
    # second mutating run cannot acquire -> stays queued
    assert runs_repo.start_run(session, r2.id) is False
    session.refresh(r2)
    assert r2.status == "queued"


def test_monitor_tick_run_takes_no_lock(session):
    c = _setup_coverage(session)
    # walk to active so a monitor tick is meaningful (not strictly required by the lock)
    r = runs_repo.create_run(
        session,
        coverage_id=c.id,
        type=RunType.MONITOR_TICK,
        trigger="scheduler",
        params={},
        priority=100,
        budget_cap_usd=None,
        doctrine_version_id=None,
    )
    assert r.mutates_dossier is False
    assert runs_repo.start_run(session, r.id) is True
    # no lock row for a non-mutating run
    assert session.query(models.CoverageRunLock).filter_by(coverage_id=c.id).one_or_none() is None


def test_finish_run_releases_lock_and_emits_outbox(session):
    c = _setup_coverage(session)
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
    runs_repo.finish_run(session, r.id, status="succeeded", summary="ok")
    session.refresh(r)
    assert r.status == "succeeded"
    assert session.query(models.CoverageRunLock).filter_by(coverage_id=c.id).one_or_none() is None
    assert (
        session.query(models.OutboxEvent).filter_by(run_id=r.id, kind="run.finished").count() == 1
    )


def test_open_gate_and_answer_gate(session):
    c = _setup_coverage(session)
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
    runs_repo.start_run(session, r.id)
    gate = runs_repo.open_gate(
        session,
        r.id,
        kind="initiation_decision",
        prompt="decide",
        payload={},
        allowed_answers=["active", "watch", "reject"],
    )
    assert gate.state == "open"
    answered = runs_repo.answer_gate(
        session,
        gate.id,
        answer="watch",
        actor_id="pm1",
        notes="x",
        idempotency_key="k1",
    )
    assert answered.state == "answered"
    assert answered.answer == "watch"
    # idempotent re-answer is a no-op replay
    again = runs_repo.answer_gate(
        session,
        gate.id,
        answer="active",
        actor_id="pm1",
        notes="x",
        idempotency_key="k1",
    )
    assert again.answer == "watch"


def test_add_stages_materializes_seq_and_house(session):
    c = _setup_coverage(session)
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
    stages = runs_repo.add_stages(
        session,
        r.id,
        [
            StageIn(seq=1, role="author", house="gpt", substrate="harness", max_attempts=3),
            StageIn(
                seq=2,
                role="verifier",
                house="gemini",
                substrate="harness",
                depends_on_seq=1,
                max_attempts=3,
            ),
        ],
    )
    assert [s.seq for s in stages] == [1, 2]
    assert stages[1].depends_on_seq == 1
    assert stages[1].house == "gemini"
