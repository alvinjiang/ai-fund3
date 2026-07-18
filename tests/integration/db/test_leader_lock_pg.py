from __future__ import annotations

from decimal import Decimal

from core.config.fund import FundConfig, MonitorCfg, RunPolicy
from core.db import models
from core.db.repo import coverage as cov_repo
from core.db.repo import runs as runs_repo
from core.db.schemas import StageIn
from core.domain.coverage_sm import Action
from core.domain.enums import ActorType, CoverageState
from core.scheduler import jobs
from core.scheduler.leader import SCHEDULER_KEY, pg_try_advisory_lock


def _seed(session):
    for k in ("gpt",):
        session.add(models.House(key=k, display_name=k.upper(), provider="openai"))
    session.flush()
    c = cov_repo.propose(session, "2267", "tse", "Yakult", "JPY", "pm1")
    r = runs_repo.create_run(
        session,
        coverage_id=c.id,
        type="initiation",
        trigger="pm",
        params={"lead_house": "gpt"},
        priority=100,
        budget_cap_usd=None,
        doctrine_version_id=None,
    )
    runs_repo.add_stages(
        session,
        r.id,
        [StageIn(seq=1, role="author", house="gpt", substrate="harness", max_attempts=3)],
    )
    runs_repo.start_run(session, r.id)
    # walk to active: proposed -> initiating -> decision_pending -> active
    cov_repo.transition(
        session, c.id, Action.START_INITIATION, ActorType.RUN, str(r.id), run_id=r.id
    )
    cov_repo.transition(
        session, c.id, Action.INITIATION_DELIVERED, ActorType.RUN, str(r.id), run_id=r.id
    )
    cov_repo.transition(session, c.id, Action.DECIDE_ACTIVE, ActorType.PM, "pm1", run_id=r.id)
    runs_repo.finish_run(session, r.id, status="succeeded")
    session.commit()
    assert c.state == CoverageState.ACTIVE.value
    return c


def test_non_leader_session_noops_when_lock_held(factory):
    """The lock FIRES: a second session's job returns 0 and creates no runs."""
    leader = factory()
    challenger = factory()
    try:
        _seed(leader)  # seeds an active coverage on tse
        # leader takes the scheduler advisory lock (held for the session, not the transaction)
        assert pg_try_advisory_lock(leader, SCHEDULER_KEY) is True

        fund = FundConfig(
            runs={"monitor_tick": RunPolicy(max_attempts=2, budget_cap_usd=Decimal("0.10"))},
            monitor=MonitorCfg(house="gpt"),
        )
        # another session — is_leader=None means the real lock check runs
        n = jobs.session_tick(
            challenger,
            exchange="tse",
            trading_date="20260105",
            session_name="post",
            fund=fund,
            is_leader=None,
        )
        assert n == 0  # non-leader -> no monitor_tick runs created
        assert challenger.query(models.Run).filter_by(type="monitor_tick").count() == 0

        # the leader session CAN run the job (it holds the lock)
        n2 = jobs.session_tick(
            leader,
            exchange="tse",
            trading_date="20260105",
            session_name="post",
            fund=fund,
            is_leader=None,
        )
        assert n2 >= 1
    finally:
        # release the advisory lock + close both sessions so the engine fixture's
        # drop_all does not block on open transactions (which hung teardown).
        leader.rollback()
        challenger.rollback()
        leader.close()
        challenger.close()
