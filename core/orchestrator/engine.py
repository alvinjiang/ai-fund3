"""Run orchestrator engine (SPEC-CORE §3).

Drives runs through their stage graphs. ``create_run`` resolves config + plans stages;
``promote_run`` starts a queued run (and transitions coverage for initiations);
``advance_run`` drains claimable stages via a runner (``FakeRunner`` in phase 1.3), then
finalizes (open the terminal gate / succeed) or fails the run. The cross-check rule
appends a contributor stage to an ``event_analysis`` run on a material change.

Stage *execution* is out of scope (SPEC-RUNNER): the runner the engine is given only
claims and completes stages; core never calls a provider. Coverage transitions for the
initiation flow (proposed→initiating→decision_pending / →failed) are applied here.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from core.config.fund import FundConfig
from core.db import models
from core.db.repo import coverage as cov_repo
from core.db.repo import predictions as pred_repo
from core.db.repo import runs as runs_repo
from core.db.schemas import PredictionIn, StageIn  # noqa: F401  (StageIn re-exported for callers)
from core.db.types import utc_now
from core.domain.coverage_sm import Action
from core.domain.enums import ActorType, RunStatus, StageStatus
from core.domain.run_sm import RunAction, RunTransitionContext, run_transition
from core.orchestrator.cross_check import auto_cross_check
from core.orchestrator.planner import plan_stages


class EngineError(ValueError):
    pass


def create_run(
    session: Session,
    run_type: str,
    coverage_id: UUID | None,
    *,
    fund: FundConfig,
    meta_houses: set[str] | None = None,
    meta_house: str | None = None,
    trigger: str = "pm",
    params: dict | None = None,
    requested_by: str | None = None,
    doctrine_version_id: UUID | None = None,
    trigger_ref: str | None = None,
) -> models.Run:
    params = dict(params or {})
    meta_houses = set(meta_houses or set())
    policy = fund.policy(run_type)
    coverage = session.get(models.Coverage, coverage_id) if coverage_id else None
    lead = params.get("lead") or (coverage.lead_house if coverage else None)
    contributors = cov_repo.resolve_contributors(session, coverage_id) if coverage else []

    common = {
        "lead": lead,
        "contributors": contributors,
        "meta_houses": meta_houses,
        "max_attempts": policy.max_attempts,
    }
    if run_type == "initiation":
        stages = plan_stages(
            "initiation",
            verify_count=params.get("verify_count", policy.verify_count),
            include_data_checker=policy.include_data_checker,
            **common,
        )
    elif run_type == "deep_review":
        stages = plan_stages("deep_review", **common)
    elif run_type == "event_analysis":
        stages = plan_stages(
            "event_analysis", lead=lead, meta_houses=meta_houses, max_attempts=policy.max_attempts
        )
    elif run_type == "monitor_tick":
        stages = plan_stages(
            "monitor_tick", monitor_house=fund.monitor.house, max_attempts=policy.max_attempts
        )
    elif run_type == "pm_query":
        stages = plan_stages(
            "pm_query", lead=lead, meta_houses=meta_houses, max_attempts=policy.max_attempts
        )
    elif run_type == "lead_review":
        stages = plan_stages("lead_review", lead=lead, contributors=contributors)
    elif run_type == "distillation":
        stages = plan_stages("distillation", meta_house=meta_house)
    else:
        raise EngineError(f"unknown run type {run_type!r}")

    budget_cap = policy.budget_cap_usd
    run_params = dict(params)
    if lead:
        run_params.setdefault("lead_house", lead)
    run = runs_repo.create_run(
        session,
        coverage_id=coverage_id,
        type=run_type,
        trigger=trigger,
        params=run_params,
        priority=params.get("priority", 100),
        budget_cap_usd=budget_cap,
        doctrine_version_id=doctrine_version_id,
        requested_by=requested_by,
        trigger_ref=trigger_ref,
    )
    runs_repo.add_stages(session, run.id, stages)
    return run


def promote_run(session: Session, run_id: UUID, *, fund: FundConfig) -> bool:
    """queued → running (acquire the coverage lock); for initiations, proposed → initiating."""
    run = session.get(models.Run, run_id)
    if run.status != RunStatus.QUEUED.value:
        return False
    if not runs_repo.start_run(session, run_id):
        return False  # lock held by another mutating run; retry next tick
    coverage = session.get(models.Coverage, run.coverage_id) if run.coverage_id else None
    if run.type == "initiation" and coverage is not None and coverage.state == "proposed":
        cov_repo.transition(
            session, coverage.id, Action.START_INITIATION, ActorType.RUN, str(run_id), run_id=run_id
        )
    return True


def _register_predictions(session: Session, stage: models.RunStage) -> None:
    result = stage.result or {}
    entries: list[PredictionIn] = []
    for p in result.get("predictions", []) or []:
        if not p.get("kind"):
            continue
        entries.append(
            PredictionIn(
                kind=p["kind"],
                horizon_date=date.fromisoformat(str(p["horizon_date"])),
                value=Decimal(str(p["value"])) if p.get("value") is not None else None,
                currency=p.get("currency"),
                stance=p.get("stance"),
                scenario_label=p.get("scenario_label"),
                scenario_prob=Decimal(str(p["scenario_prob"]))
                if p.get("scenario_prob") is not None
                else None,
                confidence=Decimal(str(p["confidence"]))
                if p.get("confidence") is not None
                else None,
                direction=p.get("direction"),
                pinned_price=Decimal(str(p["pinned_price"]))
                if p.get("pinned_price") is not None
                else None,
            )
        )
    if entries:
        pred_repo.register_from_stage(session, stage.id, entries)


def _last_target_price(session: Session, coverage_id: UUID) -> Decimal | None:
    row = session.scalar(
        select(models.Prediction)
        .where(
            models.Prediction.coverage_id == coverage_id,
            models.Prediction.kind == "target_price",
        )
        .order_by(models.Prediction.created_at.desc())
        .limit(1)
    )
    return row.value if row is not None and row.value is not None else None


def _maybe_append_cross_check(
    session: Session,
    run: models.Run,
    stage: models.RunStage,
    fund: FundConfig,
    *,
    last_tp: Decimal | None,
) -> None:
    if run.type != "event_analysis" or stage.role != "author":
        return
    rule = fund.escalation.auto_cross_check
    if auto_cross_check(
        stage.result or {},
        last_tp=last_tp,
        last_stance=None,
        event_severity=None,
        tp_change_pct=rule.tp_change_pct,
        stance_change=rule.stance_change,
        thesis_tripwire=rule.thesis_tripwire,
    ):
        # one cross_check per run (idempotent: skip if one already exists)
        exists = session.scalar(
            select(models.RunStage.id).where(
                models.RunStage.run_id == run.id, models.RunStage.role == "cross_check"
            )
        )
        if exists:
            return
        contributors = cov_repo.resolve_contributors(session, run.coverage_id)
        house = contributors[0] if contributors else stage.house
        max_seq = (
            session.scalar(
                select(func.max(models.RunStage.seq)).where(models.RunStage.run_id == run.id)
            )
            or 0
        )
        session.add(
            models.RunStage(
                run_id=run.id,
                seq=max_seq + 1,
                role="cross_check",
                house=house,
                substrate="harness",
                status="queued",
                depends_on_seq=stage.seq,
                available_at=utc_now(),
                max_attempts=3,
            )
        )


def finalize_run(session: Session, run_id: UUID, *, fund: FundConfig) -> None:
    run = session.get(models.Run, run_id)
    coverage = session.get(models.Coverage, run.coverage_id) if run.coverage_id else None
    if run.type == "initiation" and coverage is not None:
        cov_repo.transition(
            session,
            coverage.id,
            Action.INITIATION_DELIVERED,
            ActorType.RUN,
            str(run_id),
            run_id=run_id,
        )
        sm = run_transition(
            RunStatus(run.status),
            RunAction.OPEN_GATE,
            RunTransitionContext(attempts=0, max_attempts=1),
        )
        run.status = sm.to_state.value
        session.flush()
    else:
        runs_repo.finish_run(session, run_id, status=RunStatus.SUCCEEDED.value)


def fail_run(session: Session, run_id: UUID, *, error: str, fund: FundConfig) -> None:
    run = session.get(models.Run, run_id)
    coverage = session.get(models.Coverage, run.coverage_id) if run.coverage_id else None
    if run.type == "initiation" and coverage is not None and coverage.state == "initiating":
        cov_repo.transition(
            session,
            coverage.id,
            Action.INITIATION_FAILED,
            ActorType.RUN,
            str(run_id),
            run_id=run_id,
        )
    runs_repo.finish_run(session, run_id, status=RunStatus.FAILED.value, error=error)


_TERMINAL = {StageStatus.SUCCEEDED.value, StageStatus.FAILED.value, StageStatus.SKIPPED.value}


def advance_run(session: Session, run_id: UUID, runner: Any, *, fund: FundConfig) -> None:
    """Drain claimable stages via ``runner``; finalize/fail when all stages are terminal."""
    run = session.get(models.Run, run_id)
    if run.status != RunStatus.RUNNING.value:
        return
    runner.drain(session)

    # register predictions + maybe append a cross_check for each newly-succeeded stage.
    # For an event_analysis author, capture last_tp BEFORE registering so the new target
    # does not become "last" and suppress its own cross-check.
    for stage in session.scalars(select(models.RunStage).where(models.RunStage.run_id == run_id)):
        if stage.status == StageStatus.SUCCEEDED.value and stage.result:
            is_ea_author = run.type == "event_analysis" and stage.role == "author"
            last_tp = _last_target_price(session, run.coverage_id) if is_ea_author else None
            _register_predictions(session, stage)
            if is_ea_author:
                _maybe_append_cross_check(session, run, stage, fund, last_tp=last_tp)

    stages = list(session.scalars(select(models.RunStage).where(models.RunStage.run_id == run_id)))
    # A terminally-failed stage fails the run immediately (its dependents would otherwise
    # wait forever on a dependency that can never succeed).
    if any(s.status == StageStatus.FAILED.value for s in stages):
        last_error = next((s.error for s in stages if s.error), "stage failed")
        fail_run(session, run_id, error=last_error, fund=fund)
        return
    # Per-run budget cap: if spend has crossed the cap, pause at a budget_cap gate instead
    # of continuing (design/05 §9: a run never silently degrades on cost). The PM raises
    # the cap (audited into runs.params) or cancels.
    if run.budget_cap_usd and run.cost_usd is not None and run.cost_usd > run.budget_cap_usd:
        _pause_for_budget(session, run)
        return
    if not all(s.status in _TERMINAL for s in stages):
        return  # more claimable work (e.g. an appended cross_check, or retries pending)

    finalize_run(session, run_id, fund=fund)


def _pause_for_budget(session: Session, run: models.Run) -> None:
    runs_repo.open_gate(
        session,
        run.id,
        kind="budget_cap",
        prompt=f"Run has spent {run.cost_usd} of cap {run.budget_cap_usd}; raise or cancel?",
        payload={"cost_usd": str(run.cost_usd), "cap_usd": str(run.budget_cap_usd)},
        allowed_answers=["raise_cap", "cancel"],
    )
    sm = run_transition(
        RunStatus(run.status), RunAction.OPEN_GATE, RunTransitionContext(attempts=0, max_attempts=1)
    )
    run.status = sm.to_state.value
    session.flush()


def tick(session: Session, runner: Any, *, fund: FundConfig) -> None:
    """One orchestrator tick: promote queued runs, advance running ones."""
    for run in session.scalars(
        select(models.Run).where(models.Run.status == RunStatus.QUEUED.value)
    ):
        promote_run(session, run.id, fund=fund)
    for run in session.scalars(
        select(models.Run).where(models.Run.status == RunStatus.RUNNING.value)
    ):
        advance_run(session, run.id, runner, fund=fund)
