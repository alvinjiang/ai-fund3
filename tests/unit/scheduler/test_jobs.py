"""Scheduler jobs — SPEC-CORE §4 / §9.5 (jobs invoked directly; APScheduler not started)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from core.config.fund import FundConfig, MonitorCfg, RunPolicy
from core.db import models
from core.scheduler import jobs


def _fund() -> FundConfig:
    return FundConfig(
        runs={
            "monitor_tick": RunPolicy(max_attempts=2, budget_cap_usd=Decimal("0.10")),
            "deep_review": RunPolicy(max_attempts=3, budget_cap_usd=Decimal("20")),
        },
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


# --- BUGS round-3: the jobs §4 said existed but didn't ---


def test_retention_deletes_aged_idempotency_keys_and_news(session):
    aged = datetime(2020, 1, 1, tzinfo=UTC)
    session.add(models.IdempotencyKey(key="old", route="x", actor_id="pm", expires_at=aged))
    session.add(models.NewsArticle(url="http://old", title="old", created_at=aged))
    session.flush()
    n = jobs.retention(session, fund=_fund(), now=datetime.now(UTC))
    session.commit()
    assert n >= 2
    assert session.query(models.IdempotencyKey).filter_by(key="old").one_or_none() is None
    assert session.query(models.NewsArticle).filter_by(url="http://old").one_or_none() is None


def test_quarterly_sweep_creates_review_for_stale_active_coverage(session):
    active, _watch = _seed(session)
    n = jobs.quarterly_sweep(session, fund=_fund())
    assert n == 1
    runs = session.query(models.Run).filter_by(type="deep_review", coverage_id=active.id).all()
    assert len(runs) == 1


def test_quarterly_sweep_is_idempotent_per_month(session):
    active, _watch = _seed(session)
    jobs.quarterly_sweep(session, fund=_fund())
    n2 = jobs.quarterly_sweep(session, fund=_fund())
    assert n2 == 0


def test_quarterly_sweep_skips_recently_reviewed(session):
    active, _watch = _seed(session)
    # simulate a recent deep_review
    session.add(
        models.Run(
            coverage_id=active.id,
            type="deep_review",
            status="succeeded",
            trigger="scheduler",
            mutates_dossier=True,
            params={},
        )
    )
    session.flush()
    n = jobs.quarterly_sweep(session, fund=_fund())
    assert n == 0


def test_pending_spec_jobs_raise_not_implemented(session):
    """Spec-dependent jobs fail loudly (NotImplementedError), not silently."""
    for name in (
        "earnings_sweep",
        "prediction_scoring",
        "lead_review_candidacy",
        "distillation",
        "cost_rollup",
    ):
        with pytest.raises(NotImplementedError, match="pending"):
            jobs.JOBS[name](session, fund=_fund())


def test_all_jobs_use_real_leader_lock_by_default(session):
    """is_leader defaults to None (real pg_try_advisory_lock), not lambda: True."""
    import inspect

    for fn in (jobs.session_tick, jobs.watch_tick, jobs.retention, jobs.quarterly_sweep):
        assert inspect.signature(fn).parameters["is_leader"].default is None
