"""Scheduler jobs (SPEC-CORE §4).

Every job (a) is leader-gated, (b) writes ``scheduler_job_logs`` start/finish rows, (c)
creates runs through ``engine.create_run`` with a deterministic ``trigger_ref`` so a
double-fire is idempotent, (d) never calls a provider directly. APScheduler wiring is
omitted in unit tests — jobs are invoked function-by-function.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from core.config.fund import FundConfig
from core.db import models
from core.orchestrator import engine

IsLeader = Callable[[], bool]


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
) -> None:
    session.add(
        models.SchedulerJobLog(
            job_name=job_name, status=status, runs_created=runs_created, scheduled_for=scheduled_for
        )
    )
    session.flush()


def session_tick(
    session: Session,
    *,
    exchange: str,
    trading_date: str,
    session_name: str,
    fund: FundConfig,
    is_leader: IsLeader = lambda: True,
) -> int:
    """One ``monitor_tick`` per ACTIVE coverage on this exchange. Idempotent on trigger_ref."""
    if not is_leader():
        return 0
    _log(session, f"session_tick:{exchange}", "started", scheduled_for=None)
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
    is_leader: IsLeader = lambda: True,
) -> int:
    """One news-only ``monitor_tick`` per WATCH coverage. Idempotent on trigger_ref."""
    if not is_leader():
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


def schedule_watchdog(
    session: Session,
    *,
    job_intervals: dict[str, float],
    now: datetime,
) -> list[str]:
    """Return job names whose last success is older than ``interval * 2`` (stale).

    (design/05 §4 / v2's silent-scheduler-death lesson; the desk is alerted by the caller
    via the outbox.)
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
