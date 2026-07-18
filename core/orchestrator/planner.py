"""Run planner — pure stage-graph construction (SPEC-CORE §3.2).

``plan_stages(run_type, ...)`` turns a run type + the resolved house assignments into a
fully-materialized stage list (roles, seq, substrate, dependency chain). The orchestrator
resolves config (verify_count, budgets, house selection) into these arguments; this
function owns only the stage shape. A meta house in an analyst slot raises ``PlanError``.
"""

from __future__ import annotations

from core.db.schemas import StageIn
from core.orchestrator.rotation import order_contributors, pick_verifiers


class PlanError(ValueError):
    """Raised when a run plan is invalid (e.g. a meta house in an analyst role)."""


def plan_stages(
    run_type: str,
    *,
    lead: str | None = None,
    contributors: list[str] | None = None,
    verify_count: int = 2,
    include_data_checker: bool = False,
    monitor_house: str | None = None,
    meta_house: str | None = None,
    meta_houses: set[str] | None = None,
    max_attempts: int = 3,
) -> list[StageIn]:
    contributors = list(contributors or [])
    meta_houses = set(meta_houses or set())

    def chk(house: str | None) -> None:
        if house is not None and house in meta_houses:
            raise PlanError(f"a meta house ({house}) cannot fill an analyst role")

    def stage(
        seq: int, role: str, house: str | None, substrate: str, *, depends_on_seq: int | None = None
    ) -> StageIn:
        return StageIn(
            seq=seq,
            role=role,
            house=house,
            substrate=substrate,
            max_attempts=max_attempts,
            depends_on_seq=depends_on_seq,
        )

    if run_type == "initiation":
        chk(lead)
        stages: list[StageIn] = [stage(1, "author", lead, "harness")]
        verifiers = pick_verifiers(order_contributors(contributors, {}), verify_count)
        seq = 2
        for c in verifiers:
            chk(c)
            stages.append(stage(seq, "verifier", c, "harness", depends_on_seq=seq - 1))
            seq += 1
        if include_data_checker:
            dc = contributors[0] if contributors else lead
            stages.append(stage(seq, "data_checker", dc, "harness", depends_on_seq=seq - 1))
            seq += 1
        stages.append(stage(seq, "finalizer", lead, "harness", depends_on_seq=seq - 1))
        return stages

    if run_type == "deep_review":
        chk(lead)
        stages = [stage(1, "author", lead, "harness")]
        v = pick_verifiers(order_contributors(contributors, {}), 1)
        if v:
            chk(v[0])
            stages.append(stage(2, "verifier", v[0], "harness", depends_on_seq=1))
        return stages

    if run_type == "event_analysis":
        chk(lead)
        return [stage(1, "author", lead, "harness")]

    if run_type == "monitor_tick":
        return [stage(1, "monitor", monitor_house or lead, "api")]

    if run_type == "pm_query":
        chk(lead)
        return [stage(1, "pm_query", lead, "api")]

    if run_type == "lead_review":
        out: list[StageIn] = []
        for i, c in enumerate(contributors, start=1):
            chk(c)
            out.append(stage(i, "lead_review", c, "api"))
        return out

    if run_type == "distillation":
        return [stage(1, "distiller", meta_house, "harness")]

    raise PlanError(f"unknown run type {run_type!r}")
