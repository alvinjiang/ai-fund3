"""APScheduler wiring — SPEC-CORE §4. Proves the scheduler actually registers jobs and
that ``run_job`` dispatches + records outcomes (including pending-spec skips). The
scheduler is never ``start()``ed here (no wall-clock/loop); we assert registration and
dispatch, which is the wiring that was entirely absent."""

from __future__ import annotations

from decimal import Decimal

import pytest
from sqlalchemy.orm import sessionmaker

from core.config.fund import FundConfig, MonitorCfg, RunPolicy
from core.db import models
from core.scheduler import scheduler as sched_mod


def _fund(crons: dict[str, str]) -> FundConfig:
    return FundConfig(
        runs={
            "monitor_tick": RunPolicy(max_attempts=2, budget_cap_usd=Decimal("0.10")),
            "deep_review": RunPolicy(max_attempts=3, budget_cap_usd=Decimal("20")),
            "distillation": RunPolicy(max_attempts=2, budget_cap_usd=Decimal("15")),
        },
        monitor=MonitorCfg(house="gpt"),
        scheduler=crons,
    )


def test_build_scheduler_registers_every_fund_cron_job(engine):
    factory = sessionmaker(bind=engine)
    fund = _fund({"retention": "0 5 * * 0", "prediction_scoring": "15 2 * * *"})
    bg = sched_mod.build_scheduler(fund, factory)
    # not started — get_jobs() works on a built scheduler; shutdown requires .start()
    ids = {j.id for j in bg.get_jobs()}
    assert ids == {"retention", "prediction_scoring"}


def test_build_scheduler_rejects_unknown_job(engine):
    factory = sessionmaker(bind=engine)
    fund = _fund({"bogus_job": "0 0 * * *"})
    with pytest.raises(ValueError, match="unknown job"):
        sched_mod.build_scheduler(fund, factory)


def test_run_job_records_pending_spec_as_skipped(engine):
    """A pending-spec job (NotImplementedError) fires, is recorded 'skipped', does not raise."""
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    fund = _fund({})
    sched_mod.run_job("prediction_scoring", fund, factory)
    rows = (
        factory()
        .query(models.SchedulerJobLog)
        .filter_by(job_name="prediction_scoring", status="skipped")
        .count()
    )
    assert rows == 1


def test_run_job_runs_a_real_job_and_records_succeeded(engine):
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    fund = _fund({})
    # retention is fully implemented (deletes aged rows); must record 'succeeded'
    sched_mod.run_job("retention", fund, factory)
    rows = (
        factory()
        .query(models.SchedulerJobLog)
        .filter_by(job_name="retention", status="succeeded")
        .count()
    )
    assert rows == 1
