"""Spec-coverage guard — catches the "missing feature that reads as present" pattern.

Asserts every §4 scheduler job exists in the ``JOBS`` registry (so a deleted/forgotten job
fails CI), and that the calendar-driven jobs + the wiring exist. This is the meta-guard
the design review asked for: a list-checked-against-the-spec, not a signature-exists check.
"""

from __future__ import annotations

from core.scheduler import jobs
from core.scheduler.jobs import JOBS
from core.scheduler.scheduler import build_scheduler, run_job

# The §4 cron-driven job set (session_tick / watch_tick are calendar-driven; the watchdog
# is its own job). If the spec adds a job, this set grows and the registry must follow.
SECTION_4_CRON_JOBS = {
    "earnings_sweep",
    "quarterly_sweep",
    "prediction_scoring",
    "lead_review_candidacy",
    "distillation",
    "cost_rollup",
    "retention",
}


def test_every_section4_cron_job_is_registered():
    assert set(JOBS) == SECTION_4_CRON_JOBS


def test_calendar_driven_and_watchdog_jobs_exist():
    for fn in (jobs.session_tick, jobs.watch_tick, jobs.schedule_watchdog):
        assert callable(fn)


def test_scheduler_wiring_exists_and_is_callable():
    # build_scheduler + run_job are the wiring that was entirely absent; prove they import.
    assert callable(build_scheduler)
    assert callable(run_job)


def test_leader_lock_is_wired_not_default_true():
    """The default ``is_leader`` must be None (real lock), not a ``lambda: True``."""
    import inspect

    for fn in (jobs.session_tick, jobs.watch_tick, jobs.retention, jobs.quarterly_sweep):
        sig = inspect.signature(fn)
        param = sig.parameters["is_leader"]
        assert param.default is None, f"{fn.__name__} defaults is_leader to {param.default!r}"
