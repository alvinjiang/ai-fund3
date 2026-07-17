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
