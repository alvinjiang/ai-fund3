"""Runs repository — SPEC-DOMAIN §6 / §8 / §9.2.

Owns run + stage lifecycle persistence: creation, stage materialization, the
``start_run`` coverage-lock discipline (held through ``waiting_pm`` for mutating runs;
``monitor_tick``/``pm_query`` take no lock), ``finish_run`` (releases the lock + emits
``run.finished``), and the PM-gate open/answer flow. The pure status decisions come from
``run_sm``; this module owns persistence and lock/outbox side effects.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from core.db import models
from core.db.schemas import StageIn
from core.db.types import utc_now
from core.domain.enums import RunStatus, RunType
from core.domain.run_sm import RunAction, RunTransitionContext, run_transition

_NON_MUTATING = {RunType.MONITOR_TICK.value, RunType.PM_QUERY.value}
_FINISH_ACTION = {
    RunStatus.SUCCEEDED.value: RunAction.SUCCEED,
    RunStatus.FAILED.value: RunAction.FAIL,
    RunStatus.CANCELLED.value: RunAction.CANCEL,
}


def _val(x: Any) -> str:
    return x.value if hasattr(x, "value") else str(x)


def create_run(
    session: Session,
    coverage_id: UUID | None,
    type: Any,
    trigger: str,
    *,
    params: dict,
    priority: int,
    budget_cap_usd: Any,
    doctrine_version_id: UUID | None,
    requested_by: str | None = None,
    trigger_ref: str | None = None,
) -> models.Run:
    type_val = _val(type)
    r = models.Run(
        coverage_id=coverage_id,
        type=type_val,
        status=RunStatus.QUEUED.value,
        trigger=trigger,
        trigger_ref=trigger_ref,
        priority=priority,
        mutates_dossier=type_val not in _NON_MUTATING,
        requested_by=requested_by,
        params=params,
        doctrine_version_id=doctrine_version_id,
        budget_cap_usd=budget_cap_usd,
    )
    session.add(r)
    session.flush()
    return r


def add_stages(session: Session, run_id: UUID, stages: list[StageIn]) -> list[models.RunStage]:
    now = utc_now()
    out = []
    for s in stages:
        st = models.RunStage(
            run_id=run_id,
            seq=s.seq,
            role=s.role,
            house=s.house,
            substrate=s.substrate,
            status="queued",
            depends_on_seq=s.depends_on_seq,
            available_at=now,
            max_attempts=s.max_attempts,
        )
        session.add(st)
        out.append(st)
    session.flush()
    return out


def start_run(session: Session, run_id: UUID) -> bool:
    """Move a run queued -> running, acquiring the per-ticker lock for mutating runs.

    Returns False (run stays ``queued``) when a different mutating run holds the lock.
    """
    r = session.get(models.Run, run_id)
    if r.mutates_dossier and r.coverage_id is not None:
        existing = session.get(models.CoverageRunLock, r.coverage_id)
        if existing is not None and existing.run_id != run_id:
            return False
        if existing is None:
            session.add(
                models.CoverageRunLock(
                    coverage_id=r.coverage_id, run_id=run_id, acquired_at=utc_now()
                )
            )
    r.status = RunStatus.RUNNING.value
    if r.started_at is None:
        r.started_at = utc_now()
    session.flush()
    return True


def requeue_run(session: Session, run_id: UUID, reason: str) -> None:
    """Transient failure: running -> queued at the current stage boundary."""
    r = session.get(models.Run, run_id)
    sm = run_transition(
        RunStatus(r.status), RunAction.REQUEUE, RunTransitionContext(attempts=0, max_attempts=1)
    )
    r.status = sm.to_state.value
    r.error = reason
    session.flush()


def finish_run(
    session: Session,
    run_id: UUID,
    status: str,
    summary: str | None = None,
    error: str | None = None,
) -> None:
    r = session.get(models.Run, run_id)
    action = _FINISH_ACTION[status]
    sm = run_transition(
        RunStatus(r.status), action, RunTransitionContext(attempts=0, max_attempts=1)
    )
    r.status = sm.to_state.value
    r.finished_at = utc_now()
    if summary is not None:
        r.summary = summary
    if error is not None:
        r.error = error
    for eff in sm.side_effects:
        if eff == "release_lock":
            session.query(models.CoverageRunLock).filter_by(run_id=run_id).delete()
        elif eff == "outbox:run.finished":
            session.add(
                models.OutboxEvent(
                    kind="run.finished",
                    coverage_id=r.coverage_id,
                    run_id=run_id,
                    payload={"status": status, "run_id": str(run_id)},
                    dedupe_key=f"run.finished:{run_id}:{uuid4().hex}",
                )
            )
    session.flush()


def open_gate(
    session: Session,
    run_id: UUID,
    kind: str,
    prompt: str,
    payload: dict,
    allowed_answers: list[str],
) -> models.PmGate:
    r = session.get(models.Run, run_id)
    g = models.PmGate(
        run_id=run_id,
        coverage_id=r.coverage_id,
        kind=kind,
        state="open",
        prompt=prompt,
        payload=payload,
        allowed_answers=allowed_answers,
    )
    session.add(g)
    session.flush()
    return g


def answer_gate(
    session: Session,
    gate_id: UUID,
    answer: str,
    actor_id: str,
    notes: str | None,
    idempotency_key: str,
) -> models.PmGate:
    g = session.get(models.PmGate, gate_id)
    if g.state == "answered" and g.idempotency_key == idempotency_key:
        return g  # idempotent replay of the same answer
    g.state = "answered"
    g.answer = answer
    g.answered_by = actor_id
    g.answer_notes = notes
    g.answered_at = utc_now()
    g.idempotency_key = idempotency_key
    session.flush()
    return g


def get(session: Session, run_id: UUID) -> models.Run | None:
    return session.get(models.Run, run_id)


def list_open_for_coverage(session: Session, coverage_id: UUID) -> list[models.Run]:
    return list(
        session.scalars(
            select(models.Run).where(
                models.Run.coverage_id == coverage_id,
                models.Run.status.in_(
                    [RunStatus.QUEUED.value, RunStatus.RUNNING.value, RunStatus.WAITING_PM.value]
                ),
            )
        )
    )
