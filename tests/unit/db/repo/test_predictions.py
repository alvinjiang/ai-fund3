"""Predictions repository — SPEC-DOMAIN §4.12 / §8 / §9.2.

Idempotent registration (key derived from the stage id, never model text), immutability
of everything but the scored/superseded columns, and NULL confidence that stays NULL.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from core.db.repo import predictions as pred_repo
from core.db.schemas import PredictionIn


def _entry(value=Decimal("2900"), currency="JPY", confidence=Decimal("0.65")):
    return PredictionIn(
        kind="target_price",
        horizon_date=date(2027, 1, 1),
        value=value,
        currency=currency,
        confidence=confidence,
    )


def test_register_is_idempotent_on_same_stage_and_entry(session, chain):
    entries = [_entry()]
    first = pred_repo.register_from_stage(session, chain.stage.id, entries)
    second = pred_repo.register_from_stage(session, chain.stage.id, entries)
    assert len(first) == 1
    assert len(second) == 1
    assert first[0].id == second[0].id  # same row, not a duplicate


def test_confidence_none_persists_as_null(session, chain):
    [p] = pred_repo.register_from_stage(session, chain.stage.id, [_entry(confidence=None)])
    session.refresh(p)
    assert p.confidence is None  # never synthesized to 0.5


def test_update_immutable_columns_raises(session, chain):
    [p] = pred_repo.register_from_stage(session, chain.stage.id, [_entry()])
    session.flush()
    with pytest.raises(pred_repo.ImmutableColumnError):
        pred_repo.update_prediction(session, p.id, value=Decimal("9999"))
    with pytest.raises(pred_repo.ImmutableColumnError):
        pred_repo.update_prediction(session, p.id, horizon_date=date(2028, 1, 1))
    with pytest.raises(pred_repo.ImmutableColumnError):
        pred_repo.update_prediction(session, p.id, house="gemini")


def test_supersede_marks_old_and_preserves_score(session, chain):
    [p] = pred_repo.register_from_stage(session, chain.stage.id, [_entry()])
    pred_repo.score(
        session,
        p.id,
        status="hit",
        realized_price=Decimal("2950"),
        realized_return=Decimal("0.017"),
        error_pct=Decimal("0.017"),
    )
    # a new prediction supersedes it
    [p2] = pred_repo.register_from_stage(
        session,
        chain.stage.id,
        [_entry(value=Decimal("3100"))],  # different value -> different idempotency key
    )
    pred_repo.supersede(session, prediction_id=p.id, by_prediction_id=p2.id, run_id=chain.run.id)
    session.refresh(p)
    assert p.status == "superseded"
    assert p.superseded_by_id == p2.id
    # the old score is preserved (history kept)
    assert p.realized_price == Decimal("2950")
    assert p.status == "superseded"  # score not wiped
