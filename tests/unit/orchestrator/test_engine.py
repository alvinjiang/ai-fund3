"""Orchestrator engine — SPEC-CORE §3 / §9.2 (the FakeRunner-driven keystone)."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from core.config.fund import AutoCrossCheck, Escalation, FundConfig, MonitorCfg, RunPolicy
from core.db import models
from core.db.repo import coverage as cov_repo
from core.domain.enums import CoverageState
from core.orchestrator import engine
from tests.fakes.runner import FakeRunner


def _fund() -> FundConfig:
    return FundConfig(
        runs={
            "initiation": RunPolicy(
                verify_count=2, max_attempts=3, budget_cap_usd=Decimal("40"), stage_timeout_s=3600
            ),
            "deep_review": RunPolicy(max_attempts=3, budget_cap_usd=Decimal("20")),
            "event_analysis": RunPolicy(max_attempts=2, budget_cap_usd=Decimal("10")),
            "monitor_tick": RunPolicy(max_attempts=2, budget_cap_usd=Decimal("0.10")),
            "pm_query": RunPolicy(max_attempts=2, budget_cap_usd=Decimal("0.50")),
            "lead_review": RunPolicy(max_attempts=2, budget_cap_usd=Decimal("15")),
            "distillation": RunPolicy(max_attempts=2, budget_cap_usd=Decimal("15")),
        },
        monitor=MonitorCfg(house="gpt"),
        escalation=Escalation(auto_cross_check=AutoCrossCheck(tp_change_pct=Decimal("10"))),
    )


def _seed_houses(session):
    for k in ("gpt", "gemini", "deepseek", "glm", "qwen"):
        session.add(models.House(key=k, display_name=k.upper(), provider="openai"))
    session.flush()


def test_initiation_happy_path_reaches_gate(session):
    _seed_houses(session)
    cov = cov_repo.propose(session, "2267", "tse", "Yakult", "JPY", "pm1")
    session.flush()
    run = engine.create_run(session, "initiation", cov.id, fund=_fund(), params={"lead": "gpt"})
    assert engine.promote_run(session, run.id, fund=_fund()) is True

    engine.advance_run(session, run.id, FakeRunner(), fund=_fund())

    session.refresh(run)
    assert run.status == "waiting_pm"
    session.refresh(cov)
    assert cov.state == CoverageState.DECISION_PENDING.value
    gate = session.query(models.PmGate).filter_by(run_id=run.id, state="open").one()
    assert gate.kind == "initiation_decision"
    # author registered a target_price prediction
    assert (
        session.query(models.Prediction).filter_by(coverage_id=cov.id, kind="target_price").count()
        == 1
    )
    # lock held through waiting_pm
    assert (
        session.query(models.CoverageRunLock).filter_by(coverage_id=cov.id).one().run_id == run.id
    )


def test_initiation_terminal_failure_fails_run_and_coverage(session):
    _seed_houses(session)
    cov = cov_repo.propose(session, "2267", "tse", "Yakult", "JPY", "pm1")
    session.flush()
    run = engine.create_run(session, "initiation", cov.id, fund=_fund(), params={"lead": "gpt"})
    engine.promote_run(session, run.id, fund=_fund())
    # verifier always fails -> exhausts max_attempts (3) -> terminal
    engine.advance_run(
        session, run.id, FakeRunner(fail_role="verifier", fail_times=99), fund=_fund()
    )
    session.refresh(run)
    assert run.status == "failed"
    session.refresh(cov)
    assert cov.state == CoverageState.FAILED.value
    # lock released
    assert session.query(models.CoverageRunLock).filter_by(coverage_id=cov.id).one_or_none() is None


def test_event_analysis_appends_cross_check_on_material_tp_move(session):
    from tests.fakes import make_coverage, make_run, make_stage

    _seed_houses(session)
    # active coverage with a PRIOR registered target_price of 3000
    cov = make_coverage(session, lead_house="gpt")
    prior = make_run(session, cov.id, house="gpt", type="initiation")
    prior_stage = make_stage(session, prior.id, role="author", house="gpt")
    session.add(
        models.Prediction(
            coverage_id=cov.id,
            run_id=prior.id,
            stage_id=prior_stage.id,
            house="gpt",
            kind="target_price",
            value=Decimal("3000"),
            currency="JPY",
            horizon_date=date(2027, 1, 1),
            confidence=Decimal("0.6"),
            idempotency_key="prior-tp",
        )
    )
    session.flush()

    # new event_analysis whose author registers 3600 (+20% vs 3000) -> cross-check appended
    author_result = {
        "status": "completed",
        "changes": ["event re-reasoned"],
        "predictions": [
            {
                "kind": "target_price",
                "value": 3600,
                "currency": "JPY",
                "horizon_date": "2027-06-30",
                "confidence": 0.55,
            }
        ],
    }
    ea = engine.create_run(session, "event_analysis", cov.id, fund=_fund(), params={"lead": "gpt"})
    engine.promote_run(session, ea.id, fund=_fund())
    engine.advance_run(session, ea.id, FakeRunner(results={"author": author_result}), fund=_fund())

    cross = session.query(models.RunStage).filter_by(run_id=ea.id, role="cross_check").one_or_none()
    assert cross is not None, "a cross_check stage must be appended on a material TP move"


def test_per_ticker_serialization_monitor_runs_while_initiation_waits(session):
    _seed_houses(session)
    cov = cov_repo.propose(session, "2267", "tse", "Yakult", "JPY", "pm1")
    session.flush()
    init = engine.create_run(session, "initiation", cov.id, fund=_fund(), params={"lead": "gpt"})
    engine.promote_run(session, init.id, fund=_fund())
    engine.advance_run(
        session, init.id, FakeRunner(), fund=_fund()
    )  # initiation -> waiting_pm (holds lock)

    # a second MUTATING run stays queued (lock held through waiting_pm)
    dr = engine.create_run(session, "deep_review", cov.id, fund=_fund(), params={"lead": "gpt"})
    assert engine.promote_run(session, dr.id, fund=_fund()) is False
    session.refresh(dr)
    assert dr.status == "queued"

    # a non-mutating monitor_tick takes no lock and starts immediately
    mt = engine.create_run(session, "monitor_tick", cov.id, fund=_fund())
    assert engine.promote_run(session, mt.id, fund=_fund()) is True
    session.refresh(mt)
    assert mt.status == "running"
