"""Scheduler jobs (SPEC-CORE §4).

Every job (a) is leader-gated by a real Postgres advisory lock (not a default-True
predicate), (b) writes ``scheduler_job_logs`` rows, (c) creates runs through
``engine.create_run`` with a deterministic ``trigger_ref`` (idempotent double-fire),
(d) never calls a provider directly.

Jobs whose owning spec is not yet built raise ``NotImplementedError`` (a *pending spec*
marker) — they are wired to fire by the scheduler but fail loudly, the opposite of a
silent no-op. ``JOBS`` is the registry the scheduler and the coverage test iterate.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from core.config.fund import FundConfig
from core.db import models
from core.db.types import utc_now
from core.orchestrator import engine
from core.scheduler.leader import SCHEDULER_KEY, pg_try_advisory_lock

IsLeader = Callable[[], bool]


def acquire_leader(session: Session, is_leader: IsLeader | None = None) -> bool:
    """True if this process holds the scheduler advisory lock.

    ``is_leader`` is a test seam only — production callers pass ``None`` and the real
    ``pg_try_advisory_lock`` runs. (The previous ``lambda: True`` default let every process
    self-elect; this is the fix.)
    """
    if is_leader is not None:
        return is_leader()
    return pg_try_advisory_lock(session, SCHEDULER_KEY)


def _pending(spec: str) -> NotImplementedError:
    return NotImplementedError(f"scheduler job pending its owning spec: {spec}")


def _has_run(session: Session, coverage_id, type_: str, trigger_ref: str) -> bool:
    return (
        session.scalar(
            select(models.Run.id)
            .where(
                models.Run.coverage_id == coverage_id,
                models.Run.type == type_,
                models.Run.trigger_ref == trigger_ref,
            )
            .limit(1)
        )
        is not None
    )


def _log(
    session: Session,
    job_name: str,
    status: str,
    *,
    runs_created: int = 0,
    scheduled_for: datetime | None = None,
    detail: str | None = None,
) -> None:
    session.add(
        models.SchedulerJobLog(
            job_name=job_name,
            status=status,
            runs_created=runs_created,
            scheduled_for=scheduled_for,
            detail=detail,
        )
    )
    session.flush()


# ---------------------------------------------------------------- calendar-driven


def session_tick(
    session: Session,
    *,
    exchange: str,
    trading_date: str,
    session_name: str,
    fund: FundConfig,
    is_leader: IsLeader | None = None,
) -> int:
    """One ``monitor_tick`` per ACTIVE coverage on this exchange. Idempotent on trigger_ref."""
    if not acquire_leader(session, is_leader):
        return 0
    _log(session, f"session_tick:{exchange}", "started")
    ref = f"session_tick:{exchange}:{trading_date}:{session_name}"
    n = 0
    for cov in session.scalars(
        select(models.Coverage).where(
            models.Coverage.exchange == exchange, models.Coverage.state == "active"
        )
    ):
        if _has_run(session, cov.id, "monitor_tick", ref):
            continue
        engine.create_run(
            session, "monitor_tick", cov.id, fund=fund, trigger="scheduler", trigger_ref=ref
        )
        n += 1
    _log(session, f"session_tick:{exchange}", "succeeded", runs_created=n)
    return n


def watch_tick(
    session: Session,
    *,
    trading_date: str,
    fund: FundConfig,
    is_leader: IsLeader | None = None,
) -> int:
    """One news-only ``monitor_tick`` per WATCH coverage. Idempotent on trigger_ref."""
    if not acquire_leader(session, is_leader):
        return 0
    ref = f"watch_tick:{trading_date}"
    n = 0
    for cov in session.scalars(select(models.Coverage).where(models.Coverage.state == "watch")):
        if _has_run(session, cov.id, "monitor_tick", ref):
            continue
        engine.create_run(
            session,
            "monitor_tick",
            cov.id,
            fund=fund,
            trigger="scheduler",
            params={"news_only": True},
            trigger_ref=ref,
        )
        n += 1
    _log(session, "watch_tick", "succeeded", runs_created=n)
    return n


# ---------------------------------------------------------------- cron jobs


def retention(
    session: Session,
    *,
    fund: FundConfig,
    is_leader: IsLeader | None = None,
    now: datetime | None = None,
) -> int:
    """Delete expired ``idempotency_keys`` and aged-out ``news_articles`` (§4 / §4.24)."""
    if not acquire_leader(session, is_leader):
        return 0
    now = now or utc_now()
    news_days = int(fund.retention.get("news_days", 180))
    idem_days = int(fund.retention.get("idempotency_days", 30))
    n = 0
    n += (
        session.query(models.IdempotencyKey)
        .where(models.IdempotencyKey.expires_at < now - timedelta(days=idem_days))
        .delete()
    )
    n += (
        session.query(models.NewsArticle)
        .where(models.NewsArticle.created_at < now - timedelta(days=news_days))
        .delete()
    )
    _log(session, "retention", "succeeded", runs_created=0, detail=f"deleted {n} rows")
    return n


def quarterly_sweep(
    session: Session,
    *,
    fund: FundConfig,
    is_leader: IsLeader | None = None,
    now: datetime | None = None,
    stale_days: int = 90,
) -> int:
    """A ``deep_review`` for each ACTIVE coverage with no review in ``stale_days``."""
    if not acquire_leader(session, is_leader):
        return 0
    now = now or utc_now()
    cutoff = now - timedelta(days=stale_days)
    month = now.strftime("%Y-%m")
    n = 0
    for cov in session.scalars(select(models.Coverage).where(models.Coverage.state == "active")):
        recent = session.scalar(
            select(models.Run.id)
            .where(
                models.Run.coverage_id == cov.id,
                models.Run.type == "deep_review",
                models.Run.created_at >= cutoff,
            )
            .limit(1)
        )
        if recent is not None:
            continue
        ref = f"quarterly_sweep:{cov.dossier_slug}:{month}"  # idempotent per month
        if _has_run(session, cov.id, "deep_review", ref):
            continue
        engine.create_run(
            session, "deep_review", cov.id, fund=fund, trigger="scheduler", trigger_ref=ref
        )
        n += 1
    _log(session, "quarterly_sweep", "succeeded", runs_created=n)
    return n


def earnings_sweep(session: Session, *, fund: FundConfig, is_leader: IsLeader | None = None) -> int:
    """``deep_review`` for coverage with an earnings release since the last sweep."""
    raise _pending("SPEC-MARKETDATA earnings calendar / data-source port (design/05 §4)")


def prediction_scoring(
    session: Session, *, fund: FundConfig, is_leader: IsLeader | None = None
) -> int:
    """Score open predictions against realized outcomes."""
    raise _pending("SPEC-TRACKREC (§6.1) — the scoring math owns this")


def lead_review_candidacy(
    session: Session, *, fund: FundConfig, is_leader: IsLeader | None = None
) -> int:
    """Flag leads breaching candidacy thresholds; desk notice (never auto-spawns a heavy run)."""
    raise _pending("SPEC-TRACKREC track-record views (§6.3)")


def distillation(session: Session, *, fund: FundConfig, is_leader: IsLeader | None = None) -> int:
    """One monthly ``distillation`` run (the meta house)."""
    raise _pending("SPEC-DISTILLATION (§7.1) — gates + citation rule own this")


def cost_rollup(session: Session, *, fund: FundConfig, is_leader: IsLeader | None = None) -> int:
    """Reconcile metered vs provider-reported spend; desk alert on drift."""
    raise _pending("cost reconciliation (SPEC-RUNNER usage APIs / phase 7.2 cost port)")


def schedule_watchdog(
    session: Session,
    *,
    job_intervals: dict[str, float],
    now: datetime,
) -> list[str]:
    """Job names whose last success is older than ``interval * 2`` (stale).

    (design/05 §4 / v2's silent-scheduler-death lesson; the caller alerts the desk via
    the outbox.) Read-only — no leader lock, no writes.
    """
    stale: list[str] = []
    for job_name, interval in job_intervals.items():
        last = session.scalar(
            select(models.SchedulerJobLog.created_at)
            .where(
                models.SchedulerJobLog.job_name == job_name,
                models.SchedulerJobLog.status == "succeeded",
            )
            .order_by(models.SchedulerJobLog.created_at.desc())
            .limit(1)
        )
        if last is None:
            stale.append(job_name)
            continue
        # SQLite returns datetimes naive on read-back; all stored times are UTC.
        if last.tzinfo is None:
            last = last.replace(tzinfo=UTC)
        if (now - last).total_seconds() > interval * 2:
            stale.append(job_name)
    return stale


# Registry of cron-driven jobs (session_tick / watch_tick are calendar-driven; the
# scheduler wires each ``fund.scheduler`` entry to ``JOBS[name]``).
JOBS: dict[str, Callable[..., int]] = {
    "earnings_sweep": earnings_sweep,
    "quarterly_sweep": quarterly_sweep,
    "prediction_scoring": prediction_scoring,
    "lead_review_candidacy": lead_review_candidacy,
    "distillation": distillation,
    "cost_rollup": cost_rollup,
    "retention": retention,
}
