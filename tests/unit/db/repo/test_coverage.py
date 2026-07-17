"""Coverage repository — SPEC-DOMAIN §4 / §8 / §9.2.

Pins the lifecycle chain (transition row + audit_log + outbox in one transaction), the
request_id linkage (invariant 5), rollback atomicity, the dossier_slug stability, lead
history, level supersession, and contributor resolution.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from core.db import models
from core.db.repo import coverage as cov_repo
from core.db.schemas import LevelIn
from core.domain.coverage_sm import Action
from core.domain.enums import ActorType, CoverageState


def _houses(session, keys=("gpt", "gemini", "deepseek", "glm", "qwen")):
    for k in keys:
        session.add(models.House(key=k, display_name=k.upper(), provider="openai"))
    session.flush()


def _make_run(session, coverage_id, lead="gpt"):
    r = models.Run(
        coverage_id=coverage_id,
        type="initiation",
        status="queued",
        trigger="pm",
        mutates_dossier=True,
        params={"lead_house": lead},
    )
    session.add(r)
    session.flush()
    return r


def test_propose_creates_coverage_slug_and_chain(session):
    _houses(session)
    c = cov_repo.propose(
        session, ticker="2267", exchange="tse", name="Yakult", currency="JPY", actor_id="pm1"
    )
    session.commit()
    assert c.state == CoverageState.PROPOSED.value
    assert c.dossier_slug == "tse_2267"
    assert c.lead_house is None
    # one transition row whose to_state is 'proposed', an audit row, and an outbox row
    assert session.query(models.CoverageTransition).filter_by(coverage_id=c.id).count() == 1
    assert session.query(models.AuditLog).filter_by(target=f"coverage:{c.id}").count() == 1
    assert session.query(models.OutboxEvent).filter_by(coverage_id=c.id).count() >= 1


def test_full_lifecycle_propose_to_active(session):
    _houses(session)
    c = cov_repo.propose(session, "2267", "tse", "Yakult", "JPY", "pm1")
    session.flush()
    r = _make_run(session, c.id, lead="gpt")

    cov_repo.transition(
        session,
        c.id,
        Action.START_INITIATION,
        ActorType.RUN,
        str(r.id),
        run_id=r.id,
        request_id="req-start",
    )
    assert c.state == CoverageState.INITIATING.value
    assert c.lead_house == "gpt"
    # lock acquisition is a run-lifecycle concern (runs_repo.start_run), not the
    # coverage transition's; verified in tests/unit/db/repo/test_runs.py.

    cov_repo.transition(
        session,
        c.id,
        Action.INITIATION_DELIVERED,
        ActorType.RUN,
        str(r.id),
        run_id=r.id,
        request_id="req-deliver",
    )
    assert c.state == CoverageState.DECISION_PENDING.value
    gate = session.query(models.PmGate).filter_by(run_id=r.id).one()
    assert gate.state == "open"
    assert set(gate.allowed_answers) == {"active", "watch", "reject"}

    cov_repo.transition(
        session,
        c.id,
        Action.DECIDE_ACTIVE,
        ActorType.PM,
        "pm1",
        run_id=r.id,
        request_id="req-decide",
    )
    assert c.state == CoverageState.ACTIVE.value
    session.refresh(gate)
    assert gate.state == "answered"
    assert gate.answer == "active"


def test_transition_atomicity_rollback(session):
    _houses(session)
    c = cov_repo.propose(session, "2267", "tse", "Yakult", "JPY", "pm1")
    session.flush()
    r = _make_run(session, c.id)
    cov_repo.transition(
        session, c.id, Action.START_INITIATION, ActorType.RUN, str(r.id), run_id=r.id
    )
    # rollback the whole transaction -> nothing persisted
    session.rollback()
    assert session.query(models.Coverage).filter_by(id=c.id).one_or_none() is None
    assert session.query(models.CoverageTransition).count() == 0
    assert session.query(models.AuditLog).count() == 0
    assert session.query(models.OutboxEvent).count() == 0


def test_request_id_links_audit_and_outbox(session):
    _houses(session)
    c = cov_repo.propose(session, "2267", "tse", "Yakult", "JPY", "pm1", request_id="RID-1")
    session.flush()
    audit = session.query(models.AuditLog).filter_by(request_id="RID-1").one()
    assert audit.target == f"coverage:{c.id}"
    ob = session.query(models.OutboxEvent).filter_by(coverage_id=c.id).one()
    assert ob.payload["request_id"] == "RID-1"


def test_illegal_transition_raises_and_leaves_state_unchanged(session):
    _houses(session)
    c = cov_repo.propose(session, "2267", "tse", "Yakult", "JPY", "pm1")
    session.flush()
    before = c.state
    with pytest.raises(cov_repo.IllegalTransition):
        cov_repo.transition(
            session,
            c.id,
            Action.PROMOTE,
            ActorType.PM,
            "pm1",  # promote is illegal from proposed
        )
    session.refresh(c)
    assert c.state == before


def test_dossier_slug_stable_across_re_proposal(session):
    _houses(session)
    c = cov_repo.propose(session, "2267", "tse", "Yakult", "JPY", "pm1")
    session.flush()
    slug_before = c.dossier_slug
    r = _make_run(session, c.id)
    # proposed -> initiating -> decision_pending -> active -> exited -> proposed (re_propose)
    cov_repo.transition(
        session, c.id, Action.START_INITIATION, ActorType.RUN, str(r.id), run_id=r.id
    )
    cov_repo.transition(
        session, c.id, Action.INITIATION_DELIVERED, ActorType.RUN, str(r.id), run_id=r.id
    )
    cov_repo.transition(session, c.id, Action.DECIDE_ACTIVE, ActorType.PM, "pm1", run_id=r.id)
    cov_repo.transition(session, c.id, Action.EXIT, ActorType.PM, "pm1")
    assert c.state == CoverageState.EXITED.value
    cov_repo.transition(session, c.id, Action.RE_PROPOSE, ActorType.PM, "pm1")
    assert c.state == CoverageState.PROPOSED.value
    session.refresh(c)
    assert c.dossier_slug == slug_before  # same row, same slug, reuses dossier history


def test_set_lead_writes_lead_history(session):
    _houses(session)
    c = cov_repo.propose(session, "2267", "tse", "Yakult", "JPY", "pm1")
    session.flush()
    r = _make_run(session, c.id, lead="gpt")
    cov_repo.transition(
        session, c.id, Action.START_INITIATION, ActorType.RUN, str(r.id), run_id=r.id
    )
    cov_repo.transition(
        session, c.id, Action.INITIATION_DELIVERED, ActorType.RUN, str(r.id), run_id=r.id
    )
    cov_repo.transition(session, c.id, Action.DECIDE_WATCH, ActorType.PM, "pm1", run_id=r.id)
    cov_repo.set_lead(session, c.id, "gemini", actor_id="pm1", rationale="better record")
    session.commit()
    hist = session.query(models.CoverageLeadHistory).filter_by(coverage_id=c.id).one()
    assert hist.from_house == "gpt"
    assert hist.to_house == "gemini"
    assert hist.approved_by == "pm1"


def test_set_lead_rejects_meta_or_non_assignable(session):
    _houses(session)
    session.add(
        models.House(
            key="claude", display_name="Claude", provider="anthropic", assignable=False, meta=True
        )
    )
    session.flush()
    c = cov_repo.propose(session, "2267", "tse", "Yakult", "JPY", "pm1")
    session.flush()
    r = _make_run(session, c.id)
    cov_repo.transition(
        session, c.id, Action.START_INITIATION, ActorType.RUN, str(r.id), run_id=r.id
    )
    cov_repo.transition(
        session, c.id, Action.INITIATION_DELIVERED, ActorType.RUN, str(r.id), run_id=r.id
    )
    cov_repo.transition(session, c.id, Action.DECIDE_ACTIVE, ActorType.PM, "pm1", run_id=r.id)
    with pytest.raises(cov_repo.IllegalTransition):
        cov_repo.set_lead(session, c.id, "claude", actor_id="pm1")


def test_set_levels_supersedes_old_and_keeps_history(session):
    _houses(session)
    c = cov_repo.propose(session, "2267", "tse", "Yakult", "JPY", "pm1")
    session.flush()
    cov_repo.set_levels(
        session, c.id, [LevelIn("entry", Decimal("100"), "JPY", "above")], set_by="pm"
    )
    cov_repo.set_levels(
        session, c.id, [LevelIn("entry", Decimal("120"), "JPY", "above")], set_by="pm"
    )
    session.commit()
    rows = (
        session.query(models.CoverageLevel)
        .filter_by(coverage_id=c.id, kind="entry")
        .order_by(models.CoverageLevel.created_at)
        .all()
    )
    assert len(rows) == 2
    assert rows[0].active is False  # superseded
    assert rows[0].superseded_at is not None
    assert rows[1].active is True
    assert rows[1].value == Decimal("120")


def test_resolve_contributors_default_and_override(session):
    _houses(session)
    c = cov_repo.propose(session, "2267", "tse", "Yakult", "JPY", "pm1")
    session.flush()
    # default: all enabled+assignable houses except lead
    session.add(
        models.House(
            key="claude", display_name="Claude", provider="anthropic", assignable=False, meta=True
        )
    )
    session.flush()
    r = _make_run(session, c.id, lead="gpt")
    cov_repo.transition(
        session, c.id, Action.START_INITIATION, ActorType.RUN, str(r.id), run_id=r.id
    )
    assert set(cov_repo.resolve_contributors(session, c.id)) == {
        "gemini",
        "deepseek",
        "glm",
        "qwen",
    }
    # PM override
    session.add(models.CoverageContributor(coverage_id=c.id, house="gemini"))
    session.flush()
    assert cov_repo.resolve_contributors(session, c.id) == ["gemini"]
