"""Schema-level constraints — SPEC-DOMAIN §4 / §9.2.

These pin the CHECK / FK / unique constraints that must hold regardless of repository
logic. Lifecycle, atomicity, idempotency, and rollup behaviour are repo-level
(test_models is intentionally schema-only).
"""

from datetime import UTC, date, datetime

import pytest
from sqlalchemy.exc import IntegrityError

from core.db import models
from core.domain.enums import CoverageState

_OCCURRED = datetime(2026, 1, 1, tzinfo=UTC)


def _house(session, key="gpt", **kw):
    h = models.House(key=key, display_name=key.upper(), provider="openai", **kw)
    session.add(h)
    session.flush()
    return h


def _chain(session, house_key="gpt"):
    """house → coverage(active) → run → stage, the prerequisite chain for predictions."""
    _house(session, house_key)
    c = models.Coverage(
        ticker="2267",
        exchange="tse",
        name="Yakult",
        currency="JPY",
        state=CoverageState.ACTIVE.value,
        dossier_slug="tse_2267",
        lead_house=house_key,
    )
    session.add(c)
    session.flush()
    r = models.Run(
        coverage_id=c.id,
        type="initiation",
        status="running",
        trigger="pm",
        mutates_dossier=True,
        params={},
    )
    session.add(r)
    session.flush()
    s = models.RunStage(
        run_id=r.id,
        seq=1,
        role="author",
        house=house_key,
        substrate="harness",
        status="queued",
        max_attempts=3,
    )
    session.add(s)
    session.flush()
    return c, r, s


# --- houses ---


def test_house_assignable_and_meta_is_rejected(session):
    with pytest.raises(IntegrityError):
        session.add(
            models.House(key="x", display_name="X", provider="openai", assignable=True, meta=True)
        )
        session.commit()


def test_house_meta_not_assignable_inserts(session):
    _house(session, "claude", assignable=False, meta=True)
    session.commit()


# --- coverage ---


def test_coverage_bad_state_rejected(session):
    _house(session, "gpt")
    with pytest.raises(IntegrityError):
        session.add(
            models.Coverage(
                ticker="2267",
                exchange="tse",
                name="Yakult",
                currency="JPY",
                state="bogus",
                dossier_slug="tse_2267",
            )
        )
        session.commit()


def test_coverage_valid_proposed_inserts(session):
    _house(session, "gpt")
    c = models.Coverage(
        ticker="2267",
        exchange="tse",
        name="Yakult",
        currency="JPY",
        state=CoverageState.PROPOSED.value,
        dossier_slug="tse_2267",
    )
    session.add(c)
    session.commit()
    assert c.id is not None


def test_coverage_unique_exchange_ticker(session):
    _house(session, "gpt")

    def make():
        return models.Coverage(
            ticker="2267",
            exchange="tse",
            name="Yakult",
            currency="JPY",
            state=CoverageState.PROPOSED.value,
            dossier_slug="tse_2267",
        )

    session.add(make())
    session.commit()
    session.add(make())
    with pytest.raises(IntegrityError):
        session.commit()


def test_coverage_lead_house_fk_enforced(session):
    with pytest.raises(IntegrityError):
        session.add(
            models.Coverage(
                ticker="2267",
                exchange="tse",
                name="Yakult",
                currency="JPY",
                state=CoverageState.ACTIVE.value,
                dossier_slug="tse_2267",
                lead_house="nonexistent_house",
            )
        )
        session.commit()


# --- coverage_levels ---


def test_level_value_must_be_positive(session):
    c, _, _ = _chain(session)
    with pytest.raises(IntegrityError):
        session.add(
            models.CoverageLevel(
                coverage_id=c.id,
                kind="entry",
                value=0,
                currency="JPY",
                direction="above",
                set_by="pm",
            )
        )
        session.commit()


def test_level_valid_inserts_and_supersedes(session):
    c, _, _ = _chain(session)
    session.add(
        models.CoverageLevel(
            coverage_id=c.id,
            kind="entry",
            value=100,
            currency="JPY",
            direction="above",
            set_by="pm",
        )
    )
    session.commit()


# --- predictions ---


def test_prediction_confidence_out_of_range_rejected(session):
    c, r, s = _chain(session)
    with pytest.raises(IntegrityError):
        session.add(
            models.Prediction(
                coverage_id=c.id,
                run_id=r.id,
                stage_id=s.id,
                house="gpt",
                kind="target_price",
                value=100,
                currency="JPY",
                horizon_date=date(2027, 1, 1),
                confidence=1.5,
                idempotency_key="k1",
            )
        )
        session.commit()


def test_prediction_target_price_requires_value_and_currency(session):
    c, r, s = _chain(session)
    with pytest.raises(IntegrityError):
        session.add(
            models.Prediction(
                coverage_id=c.id,
                run_id=r.id,
                stage_id=s.id,
                house="gpt",
                kind="target_price",
                value=None,
                currency=None,
                horizon_date=date(2027, 1, 1),
                idempotency_key="k2",
            )
        )
        session.commit()


def test_prediction_valid_inserts(session):
    c, r, s = _chain(session)
    p = models.Prediction(
        coverage_id=c.id,
        run_id=r.id,
        stage_id=s.id,
        house="gpt",
        kind="target_price",
        value=2900,
        currency="JPY",
        horizon_date=date(2027, 1, 1),
        confidence=0.65,
        idempotency_key="k3",
    )
    session.add(p)
    session.commit()
    assert p.id is not None


# --- events dedupe ---


def test_event_dedupe_key_unique(session):
    c, _, _ = _chain(session)
    session.add(
        models.Event(
            coverage_id=c.id,
            kind="price_level",
            severity="info",
            title="x",
            payload={},
            dedupe_key="price_level:c1:l1:20260101",
            occurred_at=_OCCURRED,
        )
    )
    session.commit()
    with pytest.raises(IntegrityError):
        session.add(
            models.Event(
                coverage_id=c.id,
                kind="price_level",
                severity="info",
                title="x",
                payload={},
                dedupe_key="price_level:c1:l1:20260101",
                occurred_at=_OCCURRED,
            )
        )
        session.commit()
