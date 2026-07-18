"""Coverage state machine — pure transition logic (SPEC-DOMAIN §5).

``transition(state, action, ctx) -> Transition`` or ``IllegalTransition``. The
repository (core/db/repo/coverage.py) applies the returned Transition inside one
transaction: it writes the ``coverage.state`` UPDATE, a ``coverage_transitions`` row,
an ``audit_log`` row, an ``outbox_events`` row, and the action-specific ``side_effects``.

This module is pure: no DB, no HTTP, no LLM. Guards that depend on external state
(open position, note provided, gate open, house assignability) are passed in via
``TransitionContext`` so the SM stays deterministic and unit-testable with no fixtures.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from core.domain.enums import CoverageState


class IllegalTransition(ValueError):
    """Raised when a (state, action) pair is not legal or a guard fails.

    Carrying the pair on the exception keeps the failure queryable for API 409 mapping.
    """

    def __init__(self, from_state: Any, action: Any, reason: str):
        self.from_state = from_state
        self.action = action
        self.reason = reason
        super().__init__(f"illegal transition {from_state!r} --{action!r}--> {reason}")


class Action(StrEnum):
    PROPOSE = "propose"
    START_INITIATION = "start_initiation"
    INITIATION_DELIVERED = "initiation_delivered"
    INITIATION_FAILED = "initiation_failed"
    INITIATION_CANCELLED = "initiation_cancelled"
    RETRY_INITIATION = "retry_initiation"
    WITHDRAW = "withdraw"
    DECIDE_ACTIVE = "decide:active"
    DECIDE_WATCH = "decide:watch"
    DECIDE_REJECT = "decide:reject"
    PROMOTE = "promote"
    DEMOTE = "demote"
    EXIT = "exit"
    RE_PROPOSE = "re_propose"
    SET_LEAD = "set_lead"


@dataclass(frozen=True)
class TransitionContext:
    """Guard inputs. Defaults are the happy path; tests override to violate a guard."""

    has_open_position: bool = False
    force: bool = False
    note_provided: bool = False
    new_house_enabled: bool = True
    new_house_assignable: bool = True
    new_house_meta: bool = False
    gate_open: bool = True
    answer_in_allowed: bool = True


@dataclass(frozen=True)
class Transition:
    to_state: CoverageState
    cause: str
    side_effects: list[str] = field(default_factory=list)


# (from_state | None, action) -> (to_state, cause, specific side effects).
# The universal side effects (transition row + audit_log + outbox coverage.state_changed)
# are applied by the repository for every transition; the lists below are the
# action-specific ones the repo must also perform.
_ENTRIES: dict[tuple[Any, Action], tuple[CoverageState, str, list[str]]] = {
    (None, Action.PROPOSE): (
        CoverageState.PROPOSED,
        "propose",
        ["create_coverage_row", "compute_dossier_slug"],
    ),
    (CoverageState.PROPOSED, Action.START_INITIATION): (
        CoverageState.INITIATING,
        "start_initiation",
        ["set_lead_house"],
    ),
    (CoverageState.INITIATING, Action.INITIATION_DELIVERED): (
        CoverageState.DECISION_PENDING,
        "initiation_delivered",
        ["open_gate:initiation_decision", "outbox:gate.opened"],
    ),
    (CoverageState.INITIATING, Action.INITIATION_FAILED): (
        CoverageState.FAILED,
        "initiation_failed",
        ["outbox:run.finished"],
    ),
    (CoverageState.DECISION_PENDING, Action.INITIATION_CANCELLED): (
        CoverageState.FAILED,
        "initiation_cancelled",
        ["cancel_open_gate", "retain_branch", "outbox:run.finished"],
    ),
    (CoverageState.FAILED, Action.RETRY_INITIATION): (
        CoverageState.INITIATING,
        "retry_initiation",
        ["new_initiation_run"],
    ),
    (CoverageState.PROPOSED, Action.WITHDRAW): (
        CoverageState.REJECTED,
        "withdraw",
        ["no_branch_merge"],
    ),
    (CoverageState.FAILED, Action.WITHDRAW): (
        CoverageState.REJECTED,
        "withdraw",
        ["no_branch_merge"],
    ),
    (CoverageState.DECISION_PENDING, Action.DECIDE_ACTIVE): (
        CoverageState.ACTIVE,
        "pm_decision",
        [
            "answer_gate",
            "merge_branch",
            "predictions_open",
            "write_coverage_levels",
            "set_decided_at",
            "outbox:coverage.state_changed",
        ],
    ),
    (CoverageState.DECISION_PENDING, Action.DECIDE_WATCH): (
        CoverageState.WATCH,
        "pm_decision",
        [
            "answer_gate",
            "merge_branch",
            "predictions_open",
            "write_coverage_levels",
            "set_decided_at",
            "outbox:coverage.state_changed",
        ],
    ),
    (CoverageState.DECISION_PENDING, Action.DECIDE_REJECT): (
        CoverageState.REJECTED,
        "pm_decision",
        ["answer_gate", "no_branch_merge", "supersede_predictions"],
    ),
    (CoverageState.WATCH, Action.PROMOTE): (
        CoverageState.ACTIVE,
        "promote",
        ["spawn_deep_review", "outbox:coverage.state_changed"],
    ),
    (CoverageState.ACTIVE, Action.DEMOTE): (
        CoverageState.WATCH,
        "demote",
        ["outbox:coverage.state_changed"],
    ),
    (CoverageState.ACTIVE, Action.EXIT): (
        CoverageState.EXITED,
        "exit",
        ["expire_open_predictions", "set_exited_at", "outbox:coverage.state_changed"],
    ),
    (CoverageState.WATCH, Action.EXIT): (
        CoverageState.EXITED,
        "exit",
        ["expire_open_predictions", "set_exited_at", "outbox:coverage.state_changed"],
    ),
    (CoverageState.EXITED, Action.RE_PROPOSE): (
        CoverageState.PROPOSED,
        "re_propose",
        ["reuse_dossier_history"],
    ),
    (CoverageState.REJECTED, Action.RE_PROPOSE): (
        CoverageState.PROPOSED,
        "re_propose",
        ["reuse_dossier_history"],
    ),
    (CoverageState.ACTIVE, Action.SET_LEAD): (
        CoverageState.ACTIVE,
        "lead_change",
        ["write_lead_history", "open_predictions_keep_house", "outbox:coverage.state_changed"],
    ),
    (CoverageState.WATCH, Action.SET_LEAD): (
        CoverageState.WATCH,
        "lead_change",
        ["write_lead_history", "open_predictions_keep_house", "outbox:coverage.state_changed"],
    ),
}

DECIDE_ACTIONS = {Action.DECIDE_ACTIVE, Action.DECIDE_WATCH, Action.DECIDE_REJECT}


def _check_guards(from_state: Any, action: Action, ctx: TransitionContext) -> None:
    if action == Action.EXIT:
        # Row 11/12 guard: no open position unless force=True (which then requires a note).
        if ctx.has_open_position and not ctx.force:
            raise IllegalTransition(
                from_state, action, "exit with an open position requires force=True"
            )
        if ctx.force and not ctx.note_provided:
            raise IllegalTransition(from_state, action, "forced exit requires a mandatory note")
    elif action == Action.WITHDRAW:
        if not ctx.note_provided:
            raise IllegalTransition(from_state, action, "withdraw requires a mandatory note")
    elif action == Action.SET_LEAD:
        # Row 14 guard: new house must be enabled AND assignable AND not meta. (set_lead
        # during initiating/decision_pending is already an illegal pair — not in _ENTRIES
        # — so it raises before guards run.)
        if not ctx.new_house_enabled:
            raise IllegalTransition(from_state, action, "lead house must be enabled")
        if ctx.new_house_meta:
            raise IllegalTransition(from_state, action, "a meta house cannot be lead")
        if not ctx.new_house_assignable:
            raise IllegalTransition(from_state, action, "lead house must be assignable")
    elif action in DECIDE_ACTIONS:
        if not ctx.gate_open:
            raise IllegalTransition(from_state, action, "decide requires an open gate")
        if not ctx.answer_in_allowed:
            raise IllegalTransition(from_state, action, "answer is not in allowed_answers")


def transition(
    from_state: CoverageState | None, action: Action, ctx: TransitionContext
) -> Transition:
    """Return the Transition for a legal (state, action), or raise IllegalTransition.

    ``from_state`` is ``None`` only for ``Action.PROPOSE`` (coverage creation).
    """
    key = (from_state, action)
    entry = _ENTRIES.get(key)
    if entry is None:
        raise IllegalTransition(from_state, action, "not a legal transition from this state")
    _check_guards(from_state, action, ctx)
    to_state, cause, side_effects = entry
    return Transition(to_state=to_state, cause=cause, side_effects=list(side_effects))


def legal_actions(from_state: CoverageState | None) -> frozenset[Action]:
    """The actions legal from ``from_state`` (handy for API option enumeration)."""
    return frozenset(action for (state, action) in _ENTRIES if state == from_state)
