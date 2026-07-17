"""Orchestrator planner — SPEC-CORE §3.2 / §9.2.

Pure: turn a run type + house assignments into a fully-materialized stage graph. The
orchestrator resolves config → these arguments; this function owns only the stage shape
(roles, seq, substrate, dependency chain).
"""

from __future__ import annotations

import pytest

from core.db.schemas import StageIn
from core.orchestrator.planner import PlanError, plan_stages


def _roles_substrates(stages: list[StageIn]) -> list[tuple[str, str, str]]:
    return [(s.role, s.substrate, s.house) for s in stages]


def test_initiation_verify_count_2_is_author_two_verifiers_finalizer():
    stages = plan_stages(
        "initiation",
        lead="gpt",
        contributors=["gemini", "deepseek", "glm"],
        verify_count=2,
    )
    assert [(s.role, s.substrate) for s in stages] == [
        ("author", "harness"),
        ("verifier", "harness"),
        ("verifier", "harness"),
        ("finalizer", "harness"),
    ]
    # verifiers are contributors (not the lead); author + finalizer are the lead
    assert stages[0].house == "gpt" and stages[-1].house == "gpt"
    assert {stages[1].house, stages[2].house} == {"gemini", "deepseek"}
    # sequential dependency chain
    assert stages[0].depends_on_seq is None
    assert stages[1].depends_on_seq == 1
    assert stages[2].depends_on_seq == 2
    assert stages[3].depends_on_seq == 3


def test_initiation_verify_count_1_is_three_stages():
    stages = plan_stages("initiation", lead="gpt", contributors=["gemini"], verify_count=1)
    assert [s.role for s in stages] == ["author", "verifier", "finalizer"]


def test_initiation_include_data_checker_inserts_before_finalizer():
    stages = plan_stages(
        "initiation",
        lead="gpt",
        contributors=["gemini", "deepseek"],
        verify_count=2,
        include_data_checker=True,
    )
    assert [s.role for s in stages] == [
        "author",
        "verifier",
        "verifier",
        "data_checker",
        "finalizer",
    ]
    assert stages[-2].depends_on_seq == stages[-3].seq  # data_checker after v2
    assert stages[-1].depends_on_seq == stages[-2].seq  # finalizer after data_checker


def test_deep_review_is_author_then_verifier():
    stages = plan_stages("deep_review", lead="gpt", contributors=["gemini"])
    assert [(s.role, s.house) for s in stages] == [("author", "gpt"), ("verifier", "gemini")]
    assert all(s.substrate == "harness" for s in stages)


def test_event_analysis_is_single_author_harness():
    stages = plan_stages("event_analysis", lead="gpt")
    assert _roles_substrates(stages) == [("author", "harness", "gpt")]


def test_monitor_tick_uses_config_house_on_api():
    stages = plan_stages("monitor_tick", monitor_house="gpt")
    assert _roles_substrates(stages) == [("monitor", "api", "gpt")]


def test_pm_query_uses_lead_on_api():
    stages = plan_stages("pm_query", lead="gpt")
    assert _roles_substrates(stages) == [("pm_query", "api", "gpt")]


def test_lead_review_is_one_stage_per_contributor():
    stages = plan_stages("lead_review", lead="gpt", contributors=["gemini", "deepseek", "glm"])
    assert [s.role for s in stages] == ["lead_review", "lead_review", "lead_review"]
    assert {s.house for s in stages} == {"gemini", "deepseek", "glm"}
    assert all(s.substrate == "api" for s in stages)


def test_distillation_is_the_meta_house_only():
    stages = plan_stages("distillation", meta_house="claude")
    assert _roles_substrates(stages) == [("distiller", "harness", "claude")]


def test_assigning_a_meta_house_to_an_analyst_role_raises():
    with pytest.raises(PlanError):
        plan_stages("initiation", lead="claude", contributors=["gpt"], meta_houses={"claude"})
