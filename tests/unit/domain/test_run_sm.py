"""Run and stage state machines — SPEC-DOMAIN §6 / §9.1.

Both are pure. The run SM owns the ``running -> queued`` transient-failure re-entry
(only while ``attempts < max_attempts``) and the rule that entering ``waiting_pm`` must
carry a gate-opening side effect. Terminal run statuses release the coverage lock and
emit ``run.finished``.
"""

import pytest

from core.domain.enums import RunStatus, StageStatus
from core.domain.run_sm import (
    IllegalStateChange,
    RunAction,
    RunTransitionContext,
    StageAction,
    StageTransitionContext,
    run_transition,
    stage_transition,
)

RUN_LEGAL = [
    (RunStatus.QUEUED, RunAction.START, RunStatus.RUNNING),
    (RunStatus.RUNNING, RunAction.REQUEUE, RunStatus.QUEUED),
    (RunStatus.RUNNING, RunAction.FAIL, RunStatus.FAILED),
    (RunStatus.RUNNING, RunAction.OPEN_GATE, RunStatus.WAITING_PM),
    (RunStatus.RUNNING, RunAction.SUCCEED, RunStatus.SUCCEEDED),
    (RunStatus.RUNNING, RunAction.CANCEL, RunStatus.CANCELLED),
    (RunStatus.WAITING_PM, RunAction.SUCCEED, RunStatus.SUCCEEDED),
    (RunStatus.WAITING_PM, RunAction.CANCEL, RunStatus.CANCELLED),
    (RunStatus.QUEUED, RunAction.CANCEL, RunStatus.CANCELLED),
]
RUN_LEGAL_PAIRS = {(f, a) for (f, a, _) in RUN_LEGAL}


@pytest.mark.parametrize("from_status, action, to_status", RUN_LEGAL)
def test_run_legal_transition(from_status, action, to_status):
    ctx = RunTransitionContext(attempts=0, max_attempts=3)
    t = run_transition(from_status, action, ctx)
    assert t.to_state == to_status


@pytest.mark.parametrize("status", list(RunStatus))
@pytest.mark.parametrize("action", list(RunAction))
def test_run_illegal_pair_raises(status, action):
    if (status, action) in RUN_LEGAL_PAIRS:
        pytest.skip("legal pair")
    with pytest.raises(IllegalStateChange):
        run_transition(status, action, RunTransitionContext())


def test_run_requeue_allowed_under_max_attempts():
    t = run_transition(
        RunStatus.RUNNING, RunAction.REQUEUE, RunTransitionContext(attempts=2, max_attempts=3)
    )
    assert t.to_state == RunStatus.QUEUED


def test_run_requeue_forbidden_at_exhaustion():
    """Exhausted attempts must go to failed, not back to queued."""
    with pytest.raises(IllegalStateChange):
        run_transition(
            RunStatus.RUNNING, RunAction.REQUEUE, RunTransitionContext(attempts=3, max_attempts=3)
        )


def test_run_open_gate_carries_gate_side_effect():
    t = run_transition(
        RunStatus.RUNNING, RunAction.OPEN_GATE, RunTransitionContext(attempts=0, max_attempts=3)
    )
    assert "open_pm_gate" in t.side_effects


@pytest.mark.parametrize("action", [RunAction.SUCCEED, RunAction.FAIL, RunAction.CANCEL])
def test_run_terminal_releases_lock_and_finishes(action):
    from_status = RunStatus.RUNNING
    if (from_status, action) not in RUN_LEGAL_PAIRS:
        pytest.skip("not legal from running")
    t = run_transition(from_status, action, RunTransitionContext(attempts=0, max_attempts=3))
    assert "release_lock" in t.side_effects
    assert "outbox:run.finished" in t.side_effects


# --- Stage SM ---


STAGE_LEGAL = [
    (StageStatus.QUEUED, StageAction.START, StageStatus.RUNNING),
    (StageStatus.RUNNING, StageAction.COMPLETE, StageStatus.SUCCEEDED),
    (StageStatus.RUNNING, StageAction.FAIL_RETRYABLE, StageStatus.QUEUED),
    (StageStatus.RUNNING, StageAction.FAIL_TERMINAL, StageStatus.FAILED),
    (StageStatus.QUEUED, StageAction.SKIP, StageStatus.SKIPPED),
]
STAGE_LEGAL_PAIRS = {(f, a) for (f, a, _) in STAGE_LEGAL}


@pytest.mark.parametrize("from_status, action, to_status", STAGE_LEGAL)
def test_stage_legal_transition(from_status, action, to_status):
    ctx = StageTransitionContext(attempts=0, max_attempts=3)
    t = stage_transition(from_status, action, ctx)
    assert t.to_state == to_status


@pytest.mark.parametrize("status", list(StageStatus))
@pytest.mark.parametrize("action", list(StageAction))
def test_stage_illegal_pair_raises(status, action):
    if (status, action) in STAGE_LEGAL_PAIRS:
        pytest.skip("legal pair")
    with pytest.raises(IllegalStateChange):
        stage_transition(status, action, StageTransitionContext())


def test_stage_retryable_allowed_under_max_attempts():
    t = stage_transition(
        StageStatus.RUNNING,
        StageAction.FAIL_RETRYABLE,
        StageTransitionContext(attempts=1, max_attempts=3),
    )
    assert t.to_state == StageStatus.QUEUED


def test_stage_retryable_forbidden_at_exhaustion():
    with pytest.raises(IllegalStateChange):
        stage_transition(
            StageStatus.RUNNING,
            StageAction.FAIL_RETRYABLE,
            StageTransitionContext(attempts=3, max_attempts=3),
        )


def test_stage_retryable_sets_backoff_side_effect():
    t = stage_transition(
        StageStatus.RUNNING,
        StageAction.FAIL_RETRYABLE,
        StageTransitionContext(attempts=1, max_attempts=3),
    )
    assert "set_available_at_backoff" in t.side_effects
