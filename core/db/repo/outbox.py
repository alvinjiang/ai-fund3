"""Outbox repository — SPEC-DOMAIN §4.22 / §8.

Transactional-outbox publish/consume/ack/nack. The adapter consumes through the core
API (SPEC-ADAPTER §5.1) rather than touching Postgres directly; this module owns the
SKIP LOCKED claim + lease + backoff. Concurrent consume exclusivity is integration-tested.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from core.db import models
from core.db.types import utc_now

__all__ = ["ack", "consume", "models", "nack", "publish"]


def publish(
    session: Session,
    kind: str,
    payload: dict,
    *,
    coverage_id: UUID | None = None,
    run_id: UUID | None = None,
    dedupe_key: str,
) -> None:
    existing = session.scalar(
        select(models.OutboxEvent).where(models.OutboxEvent.dedupe_key == dedupe_key)
    )
    if existing is not None:
        return
    session.add(
        models.OutboxEvent(
            kind=kind,
            coverage_id=coverage_id,
            run_id=run_id,
            payload=payload,
            dedupe_key=dedupe_key,
        )
    )
    session.flush()


def consume(
    session: Session, worker_id: str, limit: int, lease_seconds: float
) -> list[models.OutboxEvent]:
    now = utc_now()
    rows = session.scalars(
        select(models.OutboxEvent)
        .where(
            models.OutboxEvent.delivered_at.is_(None),
            models.OutboxEvent.available_at <= now,
            or_(
                models.OutboxEvent.lease_expires_at.is_(None),
                models.OutboxEvent.lease_expires_at < now,
            ),
        )
        .order_by(models.OutboxEvent.available_at)
        .with_for_update(skip_locked=True)
        .limit(limit)
    ).all()
    for r in rows:
        r.claimed_by = worker_id
        r.lease_expires_at = now + timedelta(seconds=lease_seconds)
        r.attempts = (r.attempts or 0) + 1
    session.flush()
    return list(rows)


def ack(session: Session, event_id: UUID) -> None:
    e = session.get(models.OutboxEvent, event_id)
    e.delivered_at = utc_now()
    e.claimed_by = None
    e.lease_expires_at = None
    session.flush()


def nack(session: Session, event_id: UUID, error: str, backoff_seconds: float) -> None:
    e = session.get(models.OutboxEvent, event_id)
    e.last_error = error
    e.available_at = utc_now() + timedelta(seconds=backoff_seconds)
    e.attempts = (e.attempts or 0) + 0  # attempts already incremented at consume
    e.claimed_by = None
    e.lease_expires_at = None
    session.flush()


def pending_count(session: Session) -> Any:
    return session.scalar(
        select(models.OutboxEvent.id).where(models.OutboxEvent.delivered_at.is_(None)).limit(1)
    )
