"""Postgres-backed stage queue — SPEC-DOMAIN §6.3 / §8.

The only component runners claim work through. ``claim_stage`` uses
``FOR UPDATE SKIP LOCKED`` (ignored on SQLite) so two workers never claim the same stage;
stage dependencies are enforced in the same query (a verifier is invisible to workers
until its author stage reaches ``succeeded``/``skipped``). Cost attribution is per
attempt — failed attempts count toward the stage and run rollups (invariant 3).
"""

from __future__ import annotations

import random
from datetime import timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, aliased

from core.db import models
from core.db.schemas import AttemptIn
from core.db.types import utc_now
from core.domain.backoff import BackoffPolicy, backoff_seconds
from core.domain.enums import RunStatus, StageStatus
from core.domain.run_sm import StageAction, StageTransitionContext, stage_transition

_SATISFIED = [StageStatus.SUCCEEDED.value, StageStatus.SKIPPED.value]


def claim_stage(
    session: Session,
    worker_id: str,
    *,
    substrates: list[str] | None = None,
    houses: list[str] | None = None,
    lease_seconds: float,
) -> models.RunStage | None:
    now = utc_now()
    dep = aliased(models.RunStage)
    q = (
        select(models.RunStage)
        .join(models.Run, models.Run.id == models.RunStage.run_id)
        .where(
            models.RunStage.status == StageStatus.QUEUED.value,
            models.RunStage.available_at <= now,
            models.Run.status == RunStatus.RUNNING.value,
            or_(
                models.RunStage.depends_on_seq.is_(None),
                select(dep.id)
                .where(
                    dep.run_id == models.RunStage.run_id,
                    dep.seq == models.RunStage.depends_on_seq,
                    dep.status.in_(_SATISFIED),
                )
                .exists(),
            ),
        )
        .order_by(models.Run.priority.desc(), models.Run.created_at, models.RunStage.seq)
        .with_for_update(skip_locked=True, of=models.RunStage)
        .limit(1)
    )
    if substrates:
        q = q.where(models.RunStage.substrate.in_(substrates))
    if houses:
        q = q.where(models.RunStage.house.in_(houses))
    stage = session.scalars(q).first()
    if stage is None:
        return None

    stage.status = StageStatus.RUNNING.value
    stage.claimed_by = worker_id
    stage.claimed_at = now
    stage.lease_expires_at = now + timedelta(seconds=lease_seconds)
    stage.attempts = (stage.attempts or 0) + 1
    if stage.started_at is None:
        stage.started_at = now
    session.add(
        models.StageAttempt(
            stage_id=stage.id,
            attempt_no=stage.attempts,
            status="running",
            house=stage.house,
            substrate=stage.substrate,
            worker_id=worker_id,
        )
    )
    session.flush()
    return stage


def heartbeat(session: Session, stage_id: UUID, worker_id: str, *, lease_seconds: float) -> bool:
    stage = session.get(models.RunStage, stage_id)
    if stage is None or stage.claimed_by != worker_id or stage.status != StageStatus.RUNNING.value:
        return False
    stage.lease_expires_at = utc_now() + timedelta(seconds=lease_seconds)
    session.flush()
    return True


def _close_attempt(
    session: Session,
    stage: models.RunStage,
    attempt: AttemptIn,
    status: str,
    *,
    result: dict | None = None,
    validation_errors: list | None = None,
    kill_reason: str | None = None,
) -> None:
    a = session.scalar(
        select(models.StageAttempt).where(
            models.StageAttempt.stage_id == stage.id, models.StageAttempt.status == "running"
        )
    )
    if a is not None:
        a.status = status
        a.finished_at = utc_now()
        a.cost_usd = attempt.cost_usd
        a.cost_source = attempt.cost_source
        a.tokens_in = attempt.tokens_in
        a.tokens_out = attempt.tokens_out
        a.cached_tokens_in = attempt.cached_tokens_in
        a.transcript_ref = attempt.transcript_ref
        a.model = attempt.model
        a.exit_code = attempt.exit_code
        if kill_reason is not None:
            a.kill_reason = kill_reason
        if result is not None:
            a.result = result
        if validation_errors is not None:
            a.validation_errors = validation_errors
    current = stage.cost_usd if stage.cost_usd is not None else Decimal("0")
    stage.cost_usd = current + (attempt.cost_usd or Decimal("0"))


def _rollup_run(session: Session, run_id: UUID) -> None:
    total = session.scalar(
        select(func.coalesce(func.sum(models.RunStage.cost_usd), 0)).where(
            models.RunStage.run_id == run_id
        )
    )
    run = session.get(models.Run, run_id)
    run.cost_usd = total


def _next_available_at(attempts: int, backoff: BackoffPolicy):
    """§6.2: exponential with jitter. Randomness is applied here, at the edge."""
    delay = backoff_seconds(attempts, backoff, jitter_factor=random.uniform(-1.0, 1.0))
    return utc_now() + timedelta(seconds=delay)


def _release_claim(stage: models.RunStage) -> None:
    stage.claimed_by = None
    stage.claimed_at = None
    stage.lease_expires_at = None


def complete_stage(session: Session, stage_id: UUID, attempt: AttemptIn, result: dict) -> None:
    stage = session.get(models.RunStage, stage_id)
    _close_attempt(session, stage, attempt, status="succeeded", result=result)
    sm = stage_transition(
        StageStatus(stage.status),
        StageAction.COMPLETE,
        StageTransitionContext(attempts=stage.attempts, max_attempts=stage.max_attempts),
    )
    stage.status = sm.to_state.value
    stage.finished_at = utc_now()
    stage.result = result
    stage.tokens_in = attempt.tokens_in
    stage.tokens_out = attempt.tokens_out
    stage.transcript_ref = attempt.transcript_ref
    _release_claim(stage)
    _rollup_run(session, stage.run_id)
    session.flush()


def fail_stage(
    session: Session,
    stage_id: UUID,
    attempt: AttemptIn,
    retryable: bool,
    *,
    backoff: BackoffPolicy,
) -> None:
    stage = session.get(models.RunStage, stage_id)
    _close_attempt(session, stage, attempt, status="failed", kill_reason=attempt.kill_reason)
    can_retry = retryable and stage.attempts < stage.max_attempts
    if can_retry:
        sm = stage_transition(
            StageStatus(stage.status),
            StageAction.FAIL_RETRYABLE,
            StageTransitionContext(attempts=stage.attempts, max_attempts=stage.max_attempts),
        )
        stage.status = sm.to_state.value
        if "set_available_at_backoff" in sm.side_effects:
            stage.available_at = _next_available_at(stage.attempts, backoff)
        else:
            stage.available_at = utc_now()
    else:
        stage.status = StageStatus.FAILED.value
        stage.finished_at = utc_now()
    _release_claim(stage)
    _rollup_run(session, stage.run_id)
    session.flush()


def reap_expired(session: Session, now: Any, *, backoff: BackoffPolicy) -> list[UUID]:
    """Return stages whose lease lapsed; requeue under max_attempts, else fail."""
    expired = session.scalars(
        select(models.RunStage).where(
            models.RunStage.status == StageStatus.RUNNING.value,
            models.RunStage.lease_expires_at < now,
        )
    ).all()
    reaped: list[UUID] = []
    for stage in expired:
        a = session.scalar(
            select(models.StageAttempt).where(
                models.StageAttempt.stage_id == stage.id,
                models.StageAttempt.status == "running",
            )
        )
        if a is not None:
            a.status = "failed"
            a.kill_reason = "lease_expired"
            a.finished_at = utc_now()
        if stage.attempts < stage.max_attempts:
            stage.status = StageStatus.QUEUED.value
            stage.available_at = _next_available_at(stage.attempts, backoff)
        else:
            stage.status = StageStatus.FAILED.value
            stage.finished_at = utc_now()
        _release_claim(stage)
        reaped.append(stage.id)
    session.flush()
    return reaped
