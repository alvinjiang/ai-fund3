"""Events repository — SPEC-DOMAIN §4.16 / §8 / §9.2.

``record_event`` is idempotent via ``dedupe_key`` (unique constraint), so a re-run
monitor tick on the same trading date cannot spawn a second ``event_analysis``. Returns
None on the dedupe hit (caller treats that as "nothing new").
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from core.db import models


def record_event(
    session: Session,
    coverage_id: UUID | None,
    kind: str,
    severity: str,
    title: str,
    dedupe_key: str,
    payload: dict,
    *,
    occurred_at: datetime,
    action: str | None = None,
    detail: str | None = None,
    detected_by_run_id: UUID | None = None,
    news_article_id: UUID | None = None,
) -> models.Event | None:
    existing = session.scalar(select(models.Event).where(models.Event.dedupe_key == dedupe_key))
    if existing is not None:
        return None
    ev = models.Event(
        coverage_id=coverage_id,
        kind=kind,
        severity=severity,
        title=title,
        detail=detail,
        payload=payload,
        news_article_id=news_article_id,
        detected_by_run_id=detected_by_run_id,
        action=action,
        dedupe_key=dedupe_key,
        occurred_at=occurred_at,
    )
    session.add(ev)
    session.flush()
    return ev


def mark_handled(session: Session, event_id: UUID, run_id: UUID) -> None:
    ev = session.get(models.Event, event_id)
    ev.handled_by_run_id = run_id
    session.flush()


def get(session: Session, event_id: UUID) -> models.Event | None:
    return session.get(models.Event, event_id)
