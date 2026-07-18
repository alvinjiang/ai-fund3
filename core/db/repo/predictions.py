"""Predictions repository — SPEC-DOMAIN §4.12 / §8 / §9.2.

The immutable ledger. Registration is idempotent on a key derived from the *stage id*
and typed fields (never from model text), so a retry or resumed run cannot duplicate or
collide rows. Only the scored/superseded columns are mutable; touching anything else
raises — the money path and the thesis never silently change.
"""

from __future__ import annotations

import hashlib
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from core.db import models
from core.db.schemas import PredictionIn
from core.db.types import utc_now
from core.domain.enums import PredictionStatus

_MUTABLE = {
    "status",
    "superseded_by_id",
    "superseded_run_id",
    "scored_at",
    "realized_price",
    "realized_return",
    "error_pct",
    "outcome_notes",
}


class ImmutableColumnError(ValueError):
    """Raised when a caller tries to UPDATE a non-scored/superseded prediction column."""


def _key(stage_id: UUID, e: PredictionIn) -> str:
    raw = f"{stage_id}|{e.kind}|{e.scenario_label or ''}|{e.value or ''}|{e.horizon_date}"
    return hashlib.sha256(raw.encode()).hexdigest()


def _conflict_insert(session: Session, model: type) -> Any:
    """Dialect-aware INSERT supporting ON CONFLICT DO NOTHING (PG + SQLite)."""
    name = session.get_bind().dialect.name
    return pg_insert(model) if name == "postgresql" else sqlite_insert(model)


def register_from_stage(
    session: Session, stage_id: UUID, entries: list[PredictionIn]
) -> list[models.Prediction]:
    stage = session.get(models.RunStage, stage_id)
    run = session.get(models.Run, stage.run_id)
    out: list[models.Prediction] = []
    for e in entries:
        key = _key(stage_id, e)
        values = {
            "coverage_id": run.coverage_id,
            "run_id": stage.run_id,
            "stage_id": stage_id,
            "house": stage.house,  # identity copied from the stage, never model output
            "kind": e.kind,
            "value": e.value,
            "currency": e.currency,
            "stance": e.stance,
            "scenario_label": e.scenario_label,
            "scenario_prob": e.scenario_prob,
            "scenario_group": e.scenario_group,
            "horizon_date": e.horizon_date,
            "confidence": e.confidence,  # NULL if the model did not state one
            "rationale_ref": e.rationale_ref,
            "direction": e.direction,
            "pinned_price": e.pinned_price,
            "status": PredictionStatus.OPEN.value,
            "idempotency_key": key,
        }
        # ON CONFLICT DO NOTHING so concurrent replays of the same stage cannot raise
        # (SPEC §4.12 item 3); re-select returns the winning row either way.
        stmt = (
            _conflict_insert(session, models.Prediction)
            .values(**values)
            .on_conflict_do_nothing(index_elements=["idempotency_key"])
        )
        session.execute(stmt)
        row = session.scalar(
            select(models.Prediction).where(models.Prediction.idempotency_key == key)
        )
        out.append(row)
    session.flush()
    return out


def update_prediction(session: Session, prediction_id: UUID, **fields: Any) -> models.Prediction:
    bad = set(fields) - _MUTABLE
    if bad:
        raise ImmutableColumnError(f"prediction columns are immutable after insert: {sorted(bad)}")
    p = session.get(models.Prediction, prediction_id)
    for k, v in fields.items():
        setattr(p, k, v)
    session.flush()
    return p


def supersede(session: Session, prediction_id: UUID, by_prediction_id: UUID, run_id: UUID) -> None:
    p = session.get(models.Prediction, prediction_id)
    p.status = PredictionStatus.SUPERSEDED.value
    p.superseded_by_id = by_prediction_id
    p.superseded_run_id = run_id
    session.flush()


def score(
    session: Session,
    prediction_id: UUID,
    status: str,
    *,
    realized_price: Any = None,
    realized_return: Any = None,
    error_pct: Any = None,
    outcome_notes: str | None = None,
) -> None:
    p = session.get(models.Prediction, prediction_id)
    p.status = status
    p.scored_at = utc_now()
    if realized_price is not None:
        p.realized_price = realized_price
    if realized_return is not None:
        p.realized_return = realized_return
    if error_pct is not None:
        p.error_pct = error_pct
    if outcome_notes is not None:
        p.outcome_notes = outcome_notes
    session.flush()
