"""Core API routes (SPEC-CORE §5.1).

Coverage / runs / gates / health. Every mutating route depends on ``require_pm`` +
``idempotency`` and writes an ``audit_log`` row; replays return the stored response and
write no second audit row. Illegal coverage transitions surface as a 409 structured error.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from core.api.deps import Idempotency, get_session, idempotency, require_pm, write_audit
from core.api.errors import ApiError
from core.db import models
from core.db.repo import coverage as cov_repo
from core.db.repo import runs as runs_repo
from core.db.schemas import LevelIn
from core.db.types import utc_now
from core.domain.coverage_sm import Action, IllegalTransition
from core.domain.enums import RunStatus
from core.orchestrator import engine

router = APIRouter()


def _conflict(message: str) -> None:
    raise ApiError("conflict", message, status=409)


def _transition(session, coverage_id, action, actor_id, *, run_id=None, force=False, notes=None):
    try:
        return cov_repo.transition(
            session, coverage_id, action, "pm", actor_id, run_id=run_id, force=force, notes=notes
        )
    except IllegalTransition as exc:
        _conflict(str(exc))


# ----------------------------- request bodies -----------------------------


class ProposeBody(BaseModel):
    ticker: str
    exchange: str
    name: str
    currency: str
    isin: str | None = None
    lead: str | None = None
    notes: str | None = None


class InitiateBody(BaseModel):
    lead: str | None = None
    verify_count: int | None = None


class DecideBody(BaseModel):
    decision: str
    notes: str | None = None


class SimpleNotes(BaseModel):
    notes: str | None = None
    force: bool = False


class LeadBody(BaseModel):
    house: str
    rationale: str | None = None


class LevelItem(BaseModel):
    kind: str
    value: float
    currency: str
    direction: str


class LevelsBody(BaseModel):
    levels: list[LevelItem]


class RunBody(BaseModel):
    type: str
    coverage: str | None = None  # dossier slug
    params: dict[str, Any] = {}


class AnswerBody(BaseModel):
    answer: str
    notes: str | None = None


# ----------------------------- coverage -----------------------------


def _cov_dict(c: models.Coverage) -> dict:
    return {
        "id": str(c.id),
        "ticker": c.ticker,
        "exchange": c.exchange,
        "name": c.name,
        "currency": c.currency,
        "state": c.state,
        "lead_house": c.lead_house,
        "dossier_slug": c.dossier_slug,
    }


@router.post("/coverage")
def propose(
    body: ProposeBody,
    request: Request,
    session: Session = Depends(get_session),
    pm: str = Depends(require_pm),
    idem: Idempotency = Depends(idempotency),
):
    if idem.replay is not None:
        return idem.replay
    cov = cov_repo.propose(
        session,
        body.ticker,
        body.exchange,
        body.name,
        body.currency,
        pm,
        notes=body.notes,
        isin=body.isin,
    )
    response = _cov_dict(cov)
    write_audit(
        session,
        action="coverage.propose",
        actor_id=pm,
        target=f"coverage:{cov.id}",
        after=response,
        request_id=request.state.request_id,
    )
    idem.store(session, response, 201, "coverage.propose", pm)
    return response


@router.get("/coverage")
def list_coverage(
    state: str | None = Query(default=None),
    session: Session = Depends(get_session),
):
    q = select(models.Coverage)
    if state:
        q = q.where(models.Coverage.state == state)
    return [_cov_dict(c) for c in session.scalars(q)]


@router.get("/coverage/{slug}")
def show_coverage(slug: str, session: Session = Depends(get_session)):
    cov = cov_repo.get_by_slug(session, slug)
    if cov is None:
        raise ApiError("not_found", f"no coverage for slug {slug!r}", status=404)
    return _cov_dict(cov)


def _require_coverage(session: Session, slug: str) -> models.Coverage:
    cov = cov_repo.get_by_slug(session, slug)
    if cov is None:
        raise ApiError("not_found", f"no coverage for slug {slug!r}", status=404)
    return cov


@router.post("/coverage/{slug}/initiate")
def initiate(
    slug: str,
    body: InitiateBody,
    request: Request,
    session: Session = Depends(get_session),
    pm: str = Depends(require_pm),
    idem: Idempotency = Depends(idempotency),
):
    if idem.replay is not None:
        return idem.replay
    cov = _require_coverage(session, slug)
    params: dict[str, Any] = {}
    if body.lead:
        params["lead"] = body.lead
    if body.verify_count is not None:
        params["verify_count"] = body.verify_count
    run = engine.create_run(
        session,
        "initiation",
        cov.id,
        fund=request.app.state.fund,
        trigger="pm",
        params=params,
        requested_by=pm,
        meta_houses=request.app.state.meta_houses,
    )
    response = {"run_id": str(run.id), "status": run.status}
    write_audit(
        session,
        action="coverage.initiate",
        actor_id=pm,
        target=f"run:{run.id}",
        after=response,
        request_id=request.state.request_id,
    )
    idem.store(session, response, 201, "coverage.initiate", pm)
    return response


@router.post("/coverage/{slug}/decide")
def decide(
    slug: str,
    body: DecideBody,
    request: Request,
    session: Session = Depends(get_session),
    pm: str = Depends(require_pm),
    idem: Idempotency = Depends(idempotency),
):
    if idem.replay is not None:
        return idem.replay
    cov = _require_coverage(session, slug)
    action_map = {
        "active": Action.DECIDE_ACTIVE,
        "watch": Action.DECIDE_WATCH,
        "reject": Action.DECIDE_REJECT,
    }
    if body.decision not in action_map:
        raise ApiError("bad_decision", "decision must be active|watch|reject", status=400)
    # resolve the open initiation gate's run for the side-effects + run completion
    gate = session.scalar(
        select(models.PmGate).where(
            models.PmGate.coverage_id == cov.id, models.PmGate.state == "open"
        )
    )
    run_id = gate.run_id if gate is not None else None
    _transition(session, cov.id, action_map[body.decision], pm, run_id=run_id, notes=body.notes)
    if run_id is not None:
        runs_repo.finish_run(session, run_id, status=RunStatus.SUCCEEDED.value)
    response = {"slug": slug, "state": cov.state}
    write_audit(
        session,
        action="coverage.decide",
        actor_id=pm,
        target=f"coverage:{cov.id}",
        after={"decision": body.decision},
        request_id=request.state.request_id,
    )
    idem.store(session, response, 200, "coverage.decide", pm)
    return response


@router.post("/coverage/{slug}/promote")
def promote(
    slug: str,
    body: SimpleNotes,
    request: Request,
    session: Session = Depends(get_session),
    pm: str = Depends(require_pm),
    idem: Idempotency = Depends(idempotency),
):
    if idem.replay is not None:
        return idem.replay
    cov = _require_coverage(session, slug)
    _transition(session, cov.id, Action.PROMOTE, pm, notes=body.notes)
    response = {"slug": slug, "state": cov.state}
    write_audit(
        session,
        action="coverage.promote",
        actor_id=pm,
        target=f"coverage:{cov.id}",
        request_id=request.state.request_id,
    )
    idem.store(session, response, 200, "coverage.promote", pm)
    return response


@router.post("/coverage/{slug}/demote")
def demote(
    slug: str,
    body: SimpleNotes,
    request: Request,
    session: Session = Depends(get_session),
    pm: str = Depends(require_pm),
    idem: Idempotency = Depends(idempotency),
):
    if idem.replay is not None:
        return idem.replay
    cov = _require_coverage(session, slug)
    _transition(session, cov.id, Action.DEMOTE, pm, notes=body.notes)
    response = {"slug": slug, "state": cov.state}
    write_audit(
        session,
        action="coverage.demote",
        actor_id=pm,
        target=f"coverage:{cov.id}",
        request_id=request.state.request_id,
    )
    idem.store(session, response, 200, "coverage.demote", pm)
    return response


@router.post("/coverage/{slug}/exit")
def exit_coverage(
    slug: str,
    body: SimpleNotes,
    request: Request,
    session: Session = Depends(get_session),
    pm: str = Depends(require_pm),
    idem: Idempotency = Depends(idempotency),
):
    if idem.replay is not None:
        return idem.replay
    cov = _require_coverage(session, slug)
    _transition(session, cov.id, Action.EXIT, pm, force=body.force, notes=body.notes)
    response = {"slug": slug, "state": cov.state}
    write_audit(
        session,
        action="coverage.exit",
        actor_id=pm,
        target=f"coverage:{cov.id}",
        request_id=request.state.request_id,
    )
    idem.store(session, response, 200, "coverage.exit", pm)
    return response


@router.post("/coverage/{slug}/re-propose")
def re_propose(
    slug: str,
    body: SimpleNotes,
    request: Request,
    session: Session = Depends(get_session),
    pm: str = Depends(require_pm),
    idem: Idempotency = Depends(idempotency),
):
    if idem.replay is not None:
        return idem.replay
    cov = _require_coverage(session, slug)
    _transition(session, cov.id, Action.RE_PROPOSE, pm, notes=body.notes)
    response = {"slug": slug, "state": cov.state}
    write_audit(
        session,
        action="coverage.re_propose",
        actor_id=pm,
        target=f"coverage:{cov.id}",
        request_id=request.state.request_id,
    )
    idem.store(session, response, 200, "coverage.re_propose", pm)
    return response


@router.post("/coverage/{slug}/lead")
def set_lead(
    slug: str,
    body: LeadBody,
    request: Request,
    session: Session = Depends(get_session),
    pm: str = Depends(require_pm),
    idem: Idempotency = Depends(idempotency),
):
    if idem.replay is not None:
        return idem.replay
    cov = _require_coverage(session, slug)
    try:
        cov_repo.set_lead(session, cov.id, body.house, pm, rationale=body.rationale)
    except IllegalTransition as exc:
        _conflict(str(exc))
    response = {"slug": slug, "lead_house": body.house}
    write_audit(
        session,
        action="coverage.set_lead",
        actor_id=pm,
        target=f"coverage:{cov.id}",
        request_id=request.state.request_id,
    )
    idem.store(session, response, 200, "coverage.set_lead", pm)
    return response


@router.post("/coverage/{slug}/levels")
def set_levels(
    slug: str,
    body: LevelsBody,
    request: Request,
    session: Session = Depends(get_session),
    pm: str = Depends(require_pm),
    idem: Idempotency = Depends(idempotency),
):
    if idem.replay is not None:
        return idem.replay
    cov = _require_coverage(session, slug)
    cov_repo.set_levels(
        session,
        cov.id,
        [
            LevelIn(
                kind=level.kind,
                value=Decimal(str(level.value)),
                currency=level.currency,
                direction=level.direction,
            )
            for level in body.levels
        ],
        set_by="pm",
    )
    response = {"slug": slug, "set": len(body.levels)}
    write_audit(
        session,
        action="coverage.set_levels",
        actor_id=pm,
        target=f"coverage:{cov.id}",
        request_id=request.state.request_id,
    )
    idem.store(session, response, 200, "coverage.set_levels", pm)
    return response


# ----------------------------- runs -----------------------------


@router.post("/runs")
def new_run(
    body: RunBody,
    request: Request,
    session: Session = Depends(get_session),
    pm: str = Depends(require_pm),
    idem: Idempotency = Depends(idempotency),
):
    if idem.replay is not None:
        return idem.replay
    cov = _require_coverage(session, body.coverage) if body.coverage else None
    coverage_id = cov.id if cov else None
    run = engine.create_run(
        session,
        body.type,
        coverage_id,
        fund=request.app.state.fund,
        trigger="pm",
        params=body.params,
        requested_by=pm,
        meta_houses=request.app.state.meta_houses,
    )
    response = {"run_id": str(run.id), "status": run.status}
    write_audit(
        session,
        action="run.create",
        actor_id=pm,
        target=f"run:{run.id}",
        after=response,
        request_id=request.state.request_id,
    )
    idem.store(session, response, 201, "run.create", pm)
    return response


@router.get("/runs")
def list_runs(
    status_: str | None = Query(default=None, alias="status"), session=Depends(get_session)
):
    q = select(models.Run)
    if status_:
        q = q.where(models.Run.status == status_)
    return [
        {
            "id": str(r.id),
            "type": r.type,
            "status": r.status,
            "coverage_id": str(r.coverage_id) if r.coverage_id else None,
        }
        for r in session.scalars(q)
    ]


@router.post("/runs/{run_id}/cancel")
def cancel_run(
    run_id: UUID,
    body: SimpleNotes,
    request: Request,
    session: Session = Depends(get_session),
    pm: str = Depends(require_pm),
    idem: Idempotency = Depends(idempotency),
):
    if idem.replay is not None:
        return idem.replay
    runs_repo.finish_run(session, run_id, status=RunStatus.CANCELLED.value, error=body.notes)
    response = {"run_id": str(run_id), "status": "cancelled"}
    write_audit(
        session,
        action="run.cancel",
        actor_id=pm,
        target=f"run:{run_id}",
        request_id=request.state.request_id,
    )
    idem.store(session, response, 200, "run.cancel", pm)
    return response


# ----------------------------- gates -----------------------------


@router.get("/gates")
def list_gates(state_: str = Query(default="open"), session=Depends(get_session)):
    return [
        {
            "id": str(g.id),
            "run_id": str(g.run_id),
            "kind": g.kind,
            "state": g.state,
            "allowed_answers": g.allowed_answers,
        }
        for g in session.scalars(select(models.PmGate).where(models.PmGate.state == state_))
    ]


@router.post("/gates/{gate_id}/answer")
def answer_gate(
    gate_id: UUID,
    body: AnswerBody,
    request: Request,
    session: Session = Depends(get_session),
    pm: str = Depends(require_pm),
    idem: Idempotency = Depends(idempotency),
):
    if idem.replay is not None:
        return idem.replay
    g = runs_repo.answer_gate(session, gate_id, body.answer, pm, body.notes, idem.key)
    response = {"gate_id": str(g.id), "state": g.state, "answer": g.answer}
    write_audit(
        session,
        action="gate.answer",
        actor_id=pm,
        target=f"gate:{gate_id}",
        after={"answer": body.answer},
        request_id=request.state.request_id,
    )
    idem.store(session, response, 200, "gate.answer", pm)
    return response


# ----------------------------- health -----------------------------


@router.get("/health")
def health(session=Depends(get_session)):
    queue_depth = session.scalar(
        select(models.RunStage.id).where(models.RunStage.status == "queued").limit(1)
    )
    return {"status": "ok", "queue_pending": queue_depth is not None}


# ----------------------------- research read models -----------------------------


@router.get("/runs/{run_id}")
def show_run(run_id: UUID, session: Session = Depends(get_session)):
    run = session.get(models.Run, run_id)
    if run is None:
        raise ApiError("not_found", "run not found", status=404)
    stages = session.scalars(
        select(models.RunStage)
        .where(models.RunStage.run_id == run_id)
        .order_by(models.RunStage.seq)
    ).all()
    return {
        "id": str(run.id),
        "type": run.type,
        "status": run.status,
        "coverage_id": str(run.coverage_id) if run.coverage_id else None,
        "cost_usd": str(run.cost_usd),
        "budget_cap_usd": str(run.budget_cap_usd) if run.budget_cap_usd else None,
        "trigger": run.trigger,
        "error": run.error,
        "stages": [
            {
                "seq": s.seq,
                "role": s.role,
                "house": s.house,
                "status": s.status,
                "attempts": s.attempts,
                "cost_usd": str(s.cost_usd),
            }
            for s in stages
        ],
    }


@router.post("/runs/{run_id}/retry")
def retry_run(
    run_id: UUID,
    request: Request,
    session: Session = Depends(get_session),
    pm: str = Depends(require_pm),
    idem: Idempotency = Depends(idempotency),
):
    if idem.replay is not None:
        return idem.replay
    old = session.get(models.Run, run_id)
    if old is None:
        raise ApiError("not_found", "run not found", status=404)
    if old.status != RunStatus.FAILED.value:
        raise ApiError("not_retryable", "only failed runs can be retried", status=400)
    new = engine.create_run(
        session,
        old.type,
        old.coverage_id,
        fund=request.app.state.fund,
        trigger="pm",
        params=dict(old.params or {}),
        requested_by=pm,
        meta_houses=request.app.state.meta_houses,
    )
    response = {"run_id": str(new.id), "status": new.status, "retried_from": str(run_id)}
    write_audit(
        session,
        action="run.retry",
        actor_id=pm,
        target=f"run:{new.id}",
        after=response,
        request_id=request.state.request_id,
    )
    idem.store(session, response, 201, "run.retry", pm)
    return response


@router.get("/coverage/{slug}/dossier")
def show_dossier(slug: str, session: Session = Depends(get_session)):
    cov = _require_coverage(session, slug)
    idx = session.get(models.DossierIndex, cov.id)
    if idx is None:
        raise ApiError("not_found", "no dossier index for this coverage", status=404)
    return {
        "slug": idx.slug,
        "stance": idx.stance,
        "conviction": idx.conviction,
        "target_price": str(idx.target_price) if idx.target_price else None,
        "as_of": str(idx.as_of) if idx.as_of else None,
        "tripwire_count": idx.tripwire_count,
    }


@router.get("/predictions")
def list_predictions(
    coverage: UUID | None = Query(default=None),
    house: str | None = Query(default=None),
    status_: str | None = Query(default=None, alias="status"),
    session: Session = Depends(get_session),
):
    q = select(models.Prediction)
    if coverage:
        q = q.where(models.Prediction.coverage_id == coverage)
    if house:
        q = q.where(models.Prediction.house == house)
    if status_:
        q = q.where(models.Prediction.status == status_)
    return [
        {
            "id": str(p.id),
            "kind": p.kind,
            "value": str(p.value) if p.value else None,
            "currency": p.currency,
            "house": p.house,
            "status": p.status,
            "horizon_date": str(p.horizon_date),
            "confidence": str(p.confidence) if p.confidence else None,
        }
        for p in session.scalars(q.limit(200))
    ]


@router.get("/events")
def list_events(
    coverage: UUID | None = Query(default=None),
    severity: str | None = Query(default=None),
    session: Session = Depends(get_session),
):
    q = select(models.Event)
    if coverage:
        q = q.where(models.Event.coverage_id == coverage)
    if severity:
        q = q.where(models.Event.severity == severity)
    return [
        {
            "id": str(e.id),
            "kind": e.kind,
            "severity": e.severity,
            "title": e.title,
            "action": e.action,
            "occurred_at": str(e.occurred_at),
        }
        for e in session.scalars(q.order_by(models.Event.occurred_at.desc()).limit(100))
    ]


class RateBody(BaseModel):
    rating: int  # 1 = up, -1 = down


@router.post("/events/{event_id}/rate")
def rate_event(
    event_id: UUID,
    body: RateBody,
    request: Request,
    session: Session = Depends(get_session),
    pm: str = Depends(require_pm),
    idem: Idempotency = Depends(idempotency),
):
    if idem.replay is not None:
        return idem.replay
    ev = session.get(models.Event, event_id)
    if ev is None:
        raise ApiError("not_found", "event not found", status=404)
    ev.pm_rating = body.rating
    ev.pm_rated_at = utc_now()
    ev.pm_rated_by = pm
    response = {"event_id": str(event_id), "rating": body.rating}
    write_audit(
        session,
        action="event.rate",
        actor_id=pm,
        target=f"event:{event_id}",
        after={"rating": body.rating},
        request_id=request.state.request_id,
    )
    idem.store(session, response, 200, "event.rate", pm)
    return response


@router.get("/costs")
def costs(
    by: str = Query(default="house"),
    session: Session = Depends(get_session),
):
    """Aggregate ``llm_usage`` by house, run, or day. Simple sum; the full reconciliation
    (metered vs provider-reported) is deferred to the cost port (phase 7.2)."""
    from sqlalchemy import func as sa_func

    if by == "house":
        col = models.LlmUsage.house
    elif by == "run":
        col = models.LlmUsage.run_id
    else:
        raise ApiError("bad_param", "by must be 'house' or 'run'", status=400)
    rows = session.execute(
        select(col, sa_func.coalesce(sa_func.sum(models.LlmUsage.cost_usd), 0))
        .where(col.isnot(None))
        .group_by(col)
    ).all()
    return [{"key": str(r[0]), "cost_usd": str(r[1])} for r in rows]


class QueryBody(BaseModel):
    coverage: str  # dossier slug
    question: str


@router.post("/queries")
def new_query(
    body: QueryBody,
    request: Request,
    session: Session = Depends(get_session),
    pm: str = Depends(require_pm),
    idem: Idempotency = Depends(idempotency),
):
    """Create a ``pm_query`` run (light model, API substrate, no lock)."""
    if idem.replay is not None:
        return idem.replay
    cov = _require_coverage(session, body.coverage)
    run = engine.create_run(
        session,
        "pm_query",
        cov.id,
        fund=request.app.state.fund,
        trigger="pm",
        params={"question": body.question},
        requested_by=pm,
        meta_houses=request.app.state.meta_houses,
    )
    response = {"run_id": str(run.id), "status": run.status}
    write_audit(
        session,
        action="query.create",
        actor_id=pm,
        target=f"run:{run.id}",
        after=response,
        request_id=request.state.request_id,
    )
    idem.store(session, response, 201, "query.create", pm)
    return response


@router.get("/config/check")
def config_check(session: Session = Depends(get_session)):
    """Run checkconfig; return the result table. Never includes a secret value."""
    from core.config.checkconfig import run_checks
    from core.config.settings import get_settings

    try:
        from core.config.store import get_config

        cfg = get_config()
    except Exception:
        return {"status": "FAIL", "detail": "config not loaded"}
    results = run_checks(cfg, get_settings())
    return {"results": [{"check": n, "status": s, "detail": d} for n, s, d in results]}
