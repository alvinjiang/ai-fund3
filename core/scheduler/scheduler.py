"""APScheduler wiring (SPEC-CORE §4) — the piece that was missing entirely.

``build_scheduler`` registers every ``fund.scheduler`` cron entry to a wrapper that opens a
session, runs the job through ``JOBS``, and records the outcome in ``scheduler_job_logs``.
Pending-spec jobs (``NotImplementedError``) are logged ``skipped`` with the reason — they
fire and fail loudly, never silently. ``start()``/``shutdown()`` are the process lifecycle
hooks (systemd ``ai-fund-core`` calls them).
"""

from __future__ import annotations

from typing import Any

import structlog
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from sqlalchemy.orm import sessionmaker

from core.config.fund import FundConfig
from core.db import models
from core.scheduler.jobs import JOBS

_log = structlog.get_logger()


def run_job(job_name: str, fund: FundConfig, session_factory: sessionmaker) -> None:
    """Execute ``JOBS[job_name]`` in its own session; record the outcome.

    ``NotImplementedError`` (a pending-spec job) is recorded ``skipped`` with the reason
    and warned — the scheduler keeps running. Any other exception is recorded ``failed``
    and re-raised so APScheduler's own error handling sees it.
    """
    with session_factory() as session:
        try:
            JOBS[job_name](session, fund=fund)
            session.commit()
        except NotImplementedError as exc:
            session.rollback()
            session.add(
                models.SchedulerJobLog(job_name=job_name, status="skipped", detail=str(exc))
            )
            session.commit()
            _log.warning("scheduler.job.pending_spec", job=job_name, detail=str(exc))
        except Exception:
            session.rollback()
            session.add(models.SchedulerJobLog(job_name=job_name, status="failed"))
            session.commit()
            raise


def build_scheduler(
    fund: FundConfig,
    session_factory: sessionmaker,
    *,
    timezone: str = "UTC",
) -> BackgroundScheduler:
    """Register every ``fund.scheduler`` cron entry. Raises on an unknown job name
    (a wiring guard — a typo'd job in fund.yaml fails loud at startup, not silently)."""
    sched = BackgroundScheduler(timezone=timezone)
    for job_name, cron_expr in (fund.scheduler or {}).items():
        if job_name not in JOBS:
            raise ValueError(
                f"fund.scheduler references unknown job {job_name!r}; " f"known: {sorted(JOBS)}"
            )
        sched.add_job(
            run_job,
            CronTrigger.from_crontab(cron_expr, timezone=timezone),
            id=job_name,
            args=[job_name, fund, session_factory],
            name=job_name,
        )
    return sched


_ = Any
