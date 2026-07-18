"""Run and stage state machines — pure logic (SPEC-DOMAIN §6).

Mirrors ``coverage_sm`` in shape: ``run_transition(state, action, ctx) -> Transition``
or ``IllegalStateChange``. The orchestrator applies the returned Transition.

Two rules the tests pin down:
* ``running -> queued`` (transient-failure re-entry) is allowed only while
  ``attempts < max_attempts``; at exhaustion the run must go to ``failed``.
* Entering ``waiting_pm`` always carries an ``open_pm_gate`` side effect (a run in
  ``waiting_pm`` has exactly one open gate). Terminal run statuses release the coverage
  lock and emit ``run.finished``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from core.domain.enums import RunStatus, StageStatus


class IllegalStateChange(ValueError):
    def __init__(self, from_state: Any, action: Any, reason: str):
        self.from_state = from_state
        self.action = action
        self.reason = reason
        super().__init__(f"illegal run/stage change {from_state!r} --{action!r}--> {reason}")


# ----------------------------- run -----------------------------


class RunAction(StrEnum):
    START = "start"
    REQUEUE = "requeue"  # transient failure: running -> queued
    FAIL = "fail"
    OPEN_GATE = "open_gate"  # running -> waiting_pm
    SUCCEED = "succeed"
    CANCEL = "cancel"


@dataclass(frozen=True)
class RunTransitionContext:
    attempts: int = 0
    max_attempts: int = 1


@dataclass(frozen=True)
class RunTransition:
    to_state: RunStatus
    cause: str
    side_effects: list[str] = field(default_factory=list)


_RUN_TERMINAL = {RunStatus.SUCCEEDED, RunStatus.FAILED, RunStatus.CANCELLED}


def _terminal_side_effects(to_state: RunStatus) -> list[str]:
    return ["release_lock", "outbox:run.finished"] if to_state in _RUN_TERMINAL else []


def run_transition(state: RunStatus, action: RunAction, ctx: RunTransitionContext) -> RunTransition:
    legal: dict[tuple[RunStatus, RunAction], tuple[RunStatus, str]] = {
        (RunStatus.QUEUED, RunAction.START): (RunStatus.RUNNING, "started"),
        (RunStatus.RUNNING, RunAction.REQUEUE): (RunStatus.QUEUED, "transient_failure"),
        (RunStatus.RUNNING, RunAction.FAIL): (RunStatus.FAILED, "terminal_failure"),
        (RunStatus.RUNNING, RunAction.OPEN_GATE): (RunStatus.WAITING_PM, "reached_pm_gate"),
        (RunStatus.RUNNING, RunAction.SUCCEED): (RunStatus.SUCCEEDED, "succeeded"),
        (RunStatus.RUNNING, RunAction.CANCEL): (RunStatus.CANCELLED, "cancelled"),
        (RunStatus.WAITING_PM, RunAction.SUCCEED): (RunStatus.SUCCEEDED, "succeeded"),
        (RunStatus.WAITING_PM, RunAction.CANCEL): (RunStatus.CANCELLED, "cancelled"),
        (RunStatus.QUEUED, RunAction.CANCEL): (RunStatus.CANCELLED, "cancelled"),
    }

    entry = legal.get((state, action))
    if entry is None:
        raise IllegalStateChange(state, action, "not a legal run transition from this state")
    to_state, cause = entry

    if action is RunAction.REQUEUE and ctx.attempts >= ctx.max_attempts:
        raise IllegalStateChange(
            state, action, "attempts exhausted; the run must go to failed, not requeued"
        )

    side_effects: list[str] = []
    if action is RunAction.OPEN_GATE:
        side_effects.append("open_pm_gate")
    if action is RunAction.REQUEUE:
        side_effects.append("record_transient_error")
    side_effects.extend(_terminal_side_effects(to_state))
    return RunTransition(to_state=to_state, cause=cause, side_effects=side_effects)


# ----------------------------- stage -----------------------------


class StageAction(StrEnum):
    START = "start"
    COMPLETE = "complete"
    FAIL_RETRYABLE = "fail_retryable"  # running -> queued (attempts < max)
    FAIL_TERMINAL = "fail_terminal"
    SKIP = "skip"  # orchestrator marks a stage not needed


@dataclass(frozen=True)
class StageTransitionContext:
    attempts: int = 0
    max_attempts: int = 1


@dataclass(frozen=True)
class StageTransition:
    to_state: StageStatus
    cause: str
    side_effects: list[str] = field(default_factory=list)


def stage_transition(
    state: StageStatus, action: StageAction, ctx: StageTransitionContext
) -> StageTransition:
    legal: dict[tuple[StageStatus, StageAction], tuple[StageStatus, str]] = {
        (StageStatus.QUEUED, StageAction.START): (StageStatus.RUNNING, "claimed"),
        (StageStatus.RUNNING, StageAction.COMPLETE): (StageStatus.SUCCEEDED, "completed"),
        (StageStatus.RUNNING, StageAction.FAIL_RETRYABLE): (
            StageStatus.QUEUED,
            "retryable_failure",
        ),
        (StageStatus.RUNNING, StageAction.FAIL_TERMINAL): (StageStatus.FAILED, "terminal_failure"),
        (StageStatus.QUEUED, StageAction.SKIP): (StageStatus.SKIPPED, "orchestrator_skip"),
    }

    entry = legal.get((state, action))
    if entry is None:
        raise IllegalStateChange(state, action, "not a legal stage transition from this state")
    to_state, cause = entry

    side_effects: list[str] = []
    if action is StageAction.FAIL_RETRYABLE:
        if ctx.attempts >= ctx.max_attempts:
            raise IllegalStateChange(
                state,
                action,
                "attempts exhausted; the stage must go to failed, not requeued",
            )
        side_effects.append("set_available_at_backoff")
    return StageTransition(to_state=to_state, cause=cause, side_effects=side_effects)
