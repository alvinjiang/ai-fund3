"""Coverage repository — SPEC-DOMAIN §4 / §8 / §9.2.

The ONLY layer that mutates ``coverage`` state. Each transition is applied inside the
caller's session/transaction: the ``coverage.state`` UPDATE, a ``coverage_transitions``
row, an ``audit_log`` row, and a ``coverage.state_changed`` outbox event are written
together (invariant 5 + atomicity). The pure decision is delegated to ``coverage_sm``;
this module owns persistence and the side effects that touch THIS module's tables
(locks, gates, lead history, levels). Cross-module side effects (dossier merge,
prediction supersede, deep-review spawn) are left for the orchestrator (SPEC-CORE) —
the outbox carries the signal.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from core.db import models
from core.db.schemas import LevelIn
from core.db.types import utc_now
from core.domain.contributors import HouseInfo
from core.domain.contributors import resolve_contributors as _resolve_default
from core.domain.coverage_sm import (
    DECIDE_ACTIONS,
    Action,
    IllegalTransition,
    TransitionContext,
)
from core.domain.coverage_sm import (
    Transition as _SmTransition,
)
from core.domain.coverage_sm import (
    transition as _sm_transition,
)
from core.domain.dossier_slug import compute_dossier_slug
from core.domain.enums import ActorType, CoverageState

__all__ = [
    "IllegalTransition",
    "get_by_slug",
    "list_by_state",
    "propose",
    "resolve_contributors",
    "set_lead",
    "set_levels",
    "transition",
]


def _actor_value(actor_type: Any) -> str:
    return actor_type.value if hasattr(actor_type, "value") else str(actor_type)


def _decision_answer(action: Action) -> str:
    return {
        Action.DECIDE_ACTIVE: "active",
        Action.DECIDE_WATCH: "watch",
        Action.DECIDE_REJECT: "reject",
    }[action]


def _has_open_position(session: Session, ticker: str) -> bool:
    """Exit guard: is there any book holding a non-zero quantity of this ticker?"""
    row = session.execute(
        select(models.Position.id)
        .where(models.Position.ticker == ticker, models.Position.net_quantity != 0)
        .limit(1)
    ).first()
    return row is not None


def _build_ctx(
    session: Session,
    cov: models.Coverage | None,
    action: Action,
    run_id: UUID | None,
    force: bool,
    notes: str | None,
) -> TransitionContext:
    has_open_position = _has_open_position(session, cov.ticker) if cov else False
    gate_open = True
    answer_in_allowed = True
    if action in DECIDE_ACTIONS:
        gate = (
            session.scalar(
                select(models.PmGate).where(
                    models.PmGate.run_id == run_id, models.PmGate.state == "open"
                )
            )
            if run_id is not None
            else None
        )
        gate_open = gate is not None
        answer_in_allowed = bool(
            gate is not None and _decision_answer(action) in (gate.allowed_answers or [])
        )
    return TransitionContext(
        force=force,
        note_provided=bool(notes),
        has_open_position=has_open_position,
        gate_open=gate_open,
        answer_in_allowed=answer_in_allowed,
    )


def _record(
    session: Session,
    cov: models.Coverage,
    from_state: str | None,
    sm: _SmTransition,
    action: Action,
    actor_type: Any,
    actor_id: str,
    run_id: UUID | None,
    notes: str | None,
    request_id: str | None,
) -> models.CoverageTransition:
    """Write the transition/audit/outbox chain + this module's side effects.

    Caller has already set ``cov.state`` (and, for set_lead, ``lead_house``). The SM
    transition ``sm`` carries cause + the specific side effects to apply.
    """
    request_id = request_id or uuid4().hex
    actor_type_val = _actor_value(actor_type)
    to_state = sm.to_state.value

    tr = models.CoverageTransition(
        coverage_id=cov.id,
        from_state=from_state,
        to_state=to_state,
        actor_type=actor_type_val,
        actor_id=actor_id,
        cause=sm.cause,
        run_id=run_id,
        notes=notes,
    )
    session.add(tr)
    session.add(
        models.AuditLog(
            action=f"coverage.{action.value}",
            actor_type=actor_type_val,
            actor_id=actor_id,
            target=f"coverage:{cov.id}",
            before_state={"state": from_state},
            after_state={"state": to_state},
            request_id=request_id,
        )
    )
    # Universal outbox row (one per transition the desk sees). dedupe_key is unique per
    # emission; the payload carries request_id so audit/outbox/transition are linkable.
    session.add(
        models.OutboxEvent(
            kind="coverage.state_changed",
            coverage_id=cov.id,
            run_id=run_id,
            payload={"request_id": request_id, "to_state": to_state, "cause": sm.cause},
            dedupe_key=f"coverage.state_changed:{cov.id}:{uuid4().hex}",
        )
    )

    for eff in sm.side_effects:
        if eff.startswith("outbox:"):
            kind = eff.split(":", 1)[1]
            if kind == "coverage.state_changed":
                continue  # universal row already emitted above
            session.add(
                models.OutboxEvent(
                    kind=kind,
                    coverage_id=cov.id,
                    run_id=run_id,
                    payload={"request_id": request_id, "coverage_id": str(cov.id)},
                    dedupe_key=f"{kind}:{cov.id}:{uuid4().hex}",
                )
            )
        elif eff == "cancel_open_gate":
            session.query(models.PmGate).filter_by(run_id=run_id, state="open").update(
                {"state": "cancelled"}
            )
        elif eff == "open_gate:initiation_decision":
            session.add(
                models.PmGate(
                    run_id=run_id,
                    coverage_id=cov.id,
                    kind="initiation_decision",
                    state="open",
                    prompt="Decide initiation: active, watch, or reject?",
                    payload={},
                    allowed_answers=["active", "watch", "reject"],
                )
            )
        elif eff == "answer_gate":
            gate = session.scalar(
                select(models.PmGate).where(
                    models.PmGate.run_id == run_id, models.PmGate.state == "open"
                )
            )
            if gate is not None:
                gate.state = "answered"
                gate.answer = _decision_answer(action)
                gate.answered_by = actor_id
                gate.answered_at = utc_now()
        elif eff == "set_lead_house":
            run = session.get(models.Run, run_id) if run_id is not None else None
            if run is not None:
                cov.lead_house = run.params.get("lead_house")
        # merge_branch / predictions_open / spawn_deep_review / supersede_predictions /
        # expire_open_predictions / write_coverage_levels / write_lead_history are
        # cross-module or handled by the caller (set_lead) — no-op here.
    session.flush()
    return tr


def propose(
    session: Session,
    ticker: str,
    exchange: str,
    name: str,
    currency: str,
    actor_id: str,
    *,
    notes: str | None = None,
    isin: str | None = None,
    request_id: str | None = None,
) -> models.Coverage:
    slug = compute_dossier_slug(exchange, ticker)
    cov = models.Coverage(
        ticker=ticker,
        exchange=exchange,
        name=name,
        currency=currency,
        isin=isin,
        state=CoverageState.PROPOSED.value,
        dossier_slug=slug,
        proposed_by=actor_id,
        pm_notes=notes,
    )
    session.add(cov)
    session.flush()
    sm = _sm_transition(None, Action.PROPOSE, TransitionContext())
    _record(session, cov, None, sm, Action.PROPOSE, ActorType.PM, actor_id, None, notes, request_id)
    return cov


def transition(
    session: Session,
    coverage_id: UUID,
    action: Action,
    actor_type: Any,
    actor_id: str,
    *,
    run_id: UUID | None = None,
    force: bool = False,
    notes: str | None = None,
    request_id: str | None = None,
) -> models.CoverageTransition:
    cov = session.get(models.Coverage, coverage_id)
    if cov is None:
        raise IllegalTransition(None, action, "coverage not found")
    from_state = cov.state
    ctx = _build_ctx(session, cov, action, run_id, force, notes)
    sm = _sm_transition(CoverageState(from_state), action, ctx)  # raises IllegalTransition
    cov.state = sm.to_state.value
    return _record(
        session, cov, from_state, sm, action, actor_type, actor_id, run_id, notes, request_id
    )


def set_lead(
    session: Session,
    coverage_id: UUID,
    house: str,
    actor_id: str,
    *,
    run_id: UUID | None = None,
    rationale: str | None = None,
) -> None:
    cov = session.get(models.Coverage, coverage_id)
    if cov is None:
        raise IllegalTransition(None, Action.SET_LEAD, "coverage not found")
    h = session.get(models.House, house)
    ctx = TransitionContext(
        new_house_enabled=bool(h and h.enabled),
        new_house_assignable=bool(h and h.assignable),
        new_house_meta=bool(h and h.meta),
    )
    from_state = cov.state
    sm = _sm_transition(CoverageState(from_state), Action.SET_LEAD, ctx)
    old = cov.lead_house
    cov.lead_house = house
    _record(
        session,
        cov,
        from_state,
        sm,
        Action.SET_LEAD,
        ActorType.PM,
        actor_id,
        run_id,
        rationale,
        None,
    )
    session.add(
        models.CoverageLeadHistory(
            coverage_id=coverage_id,
            from_house=old,
            to_house=house,
            run_id=run_id,
            approved_by=actor_id,
            rationale=rationale,
        )
    )


def set_levels(
    session: Session,
    coverage_id: UUID,
    levels: list[LevelIn],
    set_by: str,
    *,
    run_id: UUID | None = None,
) -> None:
    for lv in levels:
        existing = (
            session.query(models.CoverageLevel)
            .filter_by(coverage_id=coverage_id, kind=lv.kind, active=True)
            .all()
        )
        now = utc_now()
        for e in existing:
            e.active = False
            e.superseded_at = now
        session.add(
            models.CoverageLevel(
                coverage_id=coverage_id,
                kind=lv.kind,
                value=lv.value,
                currency=lv.currency,
                direction=lv.direction,
                set_by=set_by,
                set_by_run_id=run_id,
                active=True,
            )
        )


def resolve_contributors(session: Session, coverage_id: UUID) -> list[str]:
    cov = session.get(models.Coverage, coverage_id)
    explicit = session.query(models.CoverageContributor).filter_by(coverage_id=coverage_id).all()
    if explicit:
        return sorted(c.house for c in explicit)
    houses = session.query(models.House).all()
    infos = [HouseInfo(h.key, h.enabled, h.assignable, h.meta) for h in houses]
    return _resolve_default(cov.lead_house, infos)


def get_by_slug(session: Session, slug: str) -> models.Coverage | None:
    return session.scalar(select(models.Coverage).where(models.Coverage.dossier_slug == slug))


def list_by_state(session: Session, state: CoverageState) -> list[models.Coverage]:
    return list(
        session.scalars(select(models.Coverage).where(models.Coverage.state == state.value))
    )
