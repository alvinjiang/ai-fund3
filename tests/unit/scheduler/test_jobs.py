"""Scheduler jobs — SPEC-CORE §4 / §9.5 (jobs invoked directly; APScheduler not started)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from core.config.fund import FundConfig, MonitorCfg, RunPolicy
from core.db import models
from core.scheduler import jobs


def _fund() -> FundConfig:
    return FundConfig(
        runs={"monitor_tick": RunPolicy(max_attempts=2, budget_cap_usd=Decimal("0.10"))},
        monitor=MonitorCfg(house="gpt"),
    )


def _seed(session):
    for k in ("gpt", "gemini"):
        session.add(models.House(key=k, display_name=k.upper(), provider="openai"))
    session.flush()
    # one active + one watch coverage on tse
    from core.domain.dossier_slug import compute_dossier_slug
    from core.domain.enums import CoverageState

    active = models.Coverage(
        ticker="2267",
        exchange="tse",
        name="Yakult",
        currency="JPY",
        state=CoverageState.ACTIVE.value,
        dossier_slug=compute_dossier_slug("tse", "2267"),
        lead_house="gpt",
    )
    watch = models.Coverage(
        ticker="9999",
        exchange="tse",
        name="Other",
        currency="JPY",
        state=CoverageState.WATCH.value,
        dossier_slug=compute_dossier_slug("tse", "9999"),
        lead_house="gpt",
    )
    session.add_all([active, watch])
    session.flush()
    return active, watch


def test_session_tick_creates_monitor_only_for_active_on_exchange(session):
    active, watch = _seed(session)
    n = jobs.session_tick(
        session, exchange="tse", trading_date="20260105", session_name="post", fund=_fund()
    )
    assert n == 1
    runs = session.query(models.Run).filter_by(type="monitor_tick").all()
    assert len(runs) == 1
    assert runs[0].coverage_id == active.id  # not the watch ticker


def test_session_tick_double_fire_is_idempotent(session):
    _seed(session)
    jobs.session_tick(
        session, exchange="tse", trading_date="20260105", session_name="post", fund=_fund()
    )
    n2 = jobs.session_tick(
        session, exchange="tse", trading_date="20260105", session_name="post", fund=_fund()
    )
    assert n2 == 0  # deterministic trigger_ref -> no duplicate
    assert session.query(models.Run).filter_by(type="monitor_tick").count() == 1


def test_watch_tick_covers_watch_coverage(session):
    active, watch = _seed(session)
    n = jobs.watch_tick(session, trading_date="20260105", fund=_fund())
    assert n == 1
    runs = session.query(models.Run).filter_by(type="monitor_tick").all()
    assert runs[0].coverage_id == watch.id


def test_jobs_are_no_op_when_not_leader(session):
    _seed(session)
    n = jobs.session_tick(
        session,
        exchange="tse",
        trading_date="20260105",
        session_name="post",
        fund=_fund(),
        is_leader=lambda: False,
    )
    assert n == 0
    assert session.query(models.Run).count() == 0


def test_schedule_watchdog_flags_stale_jobs(session):
    now = datetime(2026, 1, 10, 12, 0, 0, tzinfo=UTC)
    # a job that last succeeded 2h ago, interval 60s -> stale (last > 120s)
    session.add(
        models.SchedulerJobLog(
            job_name="session_tick:tse",
            status="succeeded",
            created_at=now - timedelta(hours=2),
        )
    )
    # a fresh job -> not stale
    session.add(
        models.SchedulerJobLog(
            job_name="cost_rollup",
            status="succeeded",
            created_at=now - timedelta(seconds=10),
        )
    )
    session.flush()
    stale = jobs.schedule_watchdog(
        session, job_intervals={"session_tick:tse": 60, "cost_rollup": 60}, now=now
    )
    assert "session_tick:tse" in stale
    assert "cost_rollup" not in stale
