"""Coverage state machine — SPEC-DOMAIN §5 / §9.1.

The SM is pure: ``transition(state, action, ctx) -> Transition`` or raises
``IllegalTransition``. The repository applies the returned Transition inside one
transaction (transition row + audit + outbox + the specific side effects).
"""

import pytest

from core.domain.coverage_sm import (
    Action,
    IllegalTransition,
    Transition,
    TransitionContext,
    transition,
)
from core.domain.enums import CoverageState

# (from_state | None, action, expected_to_state, expected_cause) — the §5 legal table.
LEGAL = [
    (None, Action.PROPOSE, CoverageState.PROPOSED, "propose"),
    (CoverageState.PROPOSED, Action.START_INITIATION, CoverageState.INITIATING, "start_initiation"),
    (
        CoverageState.INITIATING,
        Action.INITIATION_DELIVERED,
        CoverageState.DECISION_PENDING,
        "initiation_delivered",
    ),
    (CoverageState.INITIATING, Action.INITIATION_FAILED, CoverageState.FAILED, "initiation_failed"),
    (
        CoverageState.DECISION_PENDING,
        Action.INITIATION_CANCELLED,
        CoverageState.FAILED,
        "initiation_cancelled",
    ),
    (CoverageState.FAILED, Action.RETRY_INITIATION, CoverageState.INITIATING, "retry_initiation"),
    (CoverageState.PROPOSED, Action.WITHDRAW, CoverageState.REJECTED, "withdraw"),
    (CoverageState.FAILED, Action.WITHDRAW, CoverageState.REJECTED, "withdraw"),
    (CoverageState.DECISION_PENDING, Action.DECIDE_ACTIVE, CoverageState.ACTIVE, "pm_decision"),
    (CoverageState.DECISION_PENDING, Action.DECIDE_WATCH, CoverageState.WATCH, "pm_decision"),
    (CoverageState.DECISION_PENDING, Action.DECIDE_REJECT, CoverageState.REJECTED, "pm_decision"),
    (CoverageState.WATCH, Action.PROMOTE, CoverageState.ACTIVE, "promote"),
    (CoverageState.ACTIVE, Action.DEMOTE, CoverageState.WATCH, "demote"),
    (CoverageState.ACTIVE, Action.EXIT, CoverageState.EXITED, "exit"),
    (CoverageState.WATCH, Action.EXIT, CoverageState.EXITED, "exit"),
    (CoverageState.EXITED, Action.RE_PROPOSE, CoverageState.PROPOSED, "re_propose"),
    (CoverageState.REJECTED, Action.RE_PROPOSE, CoverageState.PROPOSED, "re_propose"),
    (CoverageState.ACTIVE, Action.SET_LEAD, CoverageState.ACTIVE, "lead_change"),
    (CoverageState.WATCH, Action.SET_LEAD, CoverageState.WATCH, "lead_change"),
]

# A context that satisfies every guard (note provided, gate open, no open position, …).
HAPPY = TransitionContext(note_provided=True)


@pytest.mark.parametrize("from_state, action, to_state, cause", LEGAL)
def test_legal_transition(from_state, action, to_state, cause):
    t = transition(from_state, action, HAPPY)
    assert isinstance(t, Transition)
    assert t.to_state == to_state
    assert t.cause == cause
    assert isinstance(t.side_effects, list) and t.side_effects


LEGAL_PAIRS = {(f, a) for (f, a, _, _) in LEGAL if f is not None}


@pytest.mark.parametrize("state", list(CoverageState))
@pytest.mark.parametrize("action", list(Action))
def test_illegal_pair_raises(state, action):
    """Exhaustive CoverageState x Action minus the legal table must raise.

    Catches a builder silently allowing e.g. watch -> decision_pending.
    """
    if (state, action) in LEGAL_PAIRS:
        pytest.skip("legal pair")
    with pytest.raises(IllegalTransition):
        transition(state, action, HAPPY)


# --- Guards (§5 guard column, §9.1) ---


def test_exit_active_with_open_position_requires_force():
    with pytest.raises(IllegalTransition):
        transition(
            CoverageState.ACTIVE,
            Action.EXIT,
            TransitionContext(has_open_position=True, force=False),
        )


def test_exit_active_force_without_note_raises():
    with pytest.raises(IllegalTransition):
        transition(
            CoverageState.ACTIVE,
            Action.EXIT,
            TransitionContext(has_open_position=True, force=True, note_provided=False),
        )


def test_exit_active_force_with_note_succeeds():
    t = transition(
        CoverageState.ACTIVE,
        Action.EXIT,
        TransitionContext(has_open_position=True, force=True, note_provided=True),
    )
    assert t.to_state == CoverageState.EXITED


def test_set_lead_to_meta_house_raises():
    with pytest.raises(IllegalTransition):
        transition(CoverageState.ACTIVE, Action.SET_LEAD, TransitionContext(new_house_meta=True))


def test_set_lead_to_non_assignable_house_raises():
    with pytest.raises(IllegalTransition):
        transition(
            CoverageState.ACTIVE,
            Action.SET_LEAD,
            TransitionContext(new_house_assignable=False),
        )


def test_withdraw_without_note_raises():
    with pytest.raises(IllegalTransition):
        transition(CoverageState.PROPOSED, Action.WITHDRAW, TransitionContext(note_provided=False))


# --- Side effects (§9.1 called-out contracts) ---


def test_promote_spawns_deep_review():
    t = transition(CoverageState.WATCH, Action.PROMOTE, HAPPY)
    assert "spawn_deep_review" in t.side_effects


def test_decide_reject_supersedes_predictions_and_skips_merge():
    t = transition(CoverageState.DECISION_PENDING, Action.DECIDE_REJECT, HAPPY)
    assert "supersede_predictions" in t.side_effects
    assert "no_branch_merge" in t.side_effects


def test_decide_active_merges_branch_and_opens_predictions():
    t = transition(CoverageState.DECISION_PENDING, Action.DECIDE_ACTIVE, HAPPY)
    assert "merge_branch" in t.side_effects
    assert "predictions_open" in t.side_effects


def test_initiation_cancelled_cancels_gate_and_retains_branch():
    # Lock release is a run-lifecycle concern (runs_repo.finish_run), not a coverage-SM
    # side effect, so it is intentionally not asserted here.
    t = transition(CoverageState.DECISION_PENDING, Action.INITIATION_CANCELLED, HAPPY)
    assert "cancel_open_gate" in t.side_effects
    assert "retain_branch" in t.side_effects


def test_start_initiation_sets_lead():
    t = transition(CoverageState.PROPOSED, Action.START_INITIATION, HAPPY)
    assert "set_lead_house" in t.side_effects
