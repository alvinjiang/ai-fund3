"""Contributor rotation — SPEC-CORE §3.2 / §9.2.

Rotation state is *derived* (from prior stage rows), never stored as a cursor. Contributors
are ordered by ``(last_used_at ASC, key ASC)`` so verify pairs do not calcify.
"""

from __future__ import annotations

from datetime import UTC, datetime

from core.orchestrator.rotation import order_contributors, pick_verifiers


def test_unused_contributors_first_then_by_key():
    out = order_contributors(
        ["gemini", "deepseek", "glm"],
        last_used_at={"gemini": datetime(2026, 1, 2, tzinfo=UTC)},
    )
    # deepseek & glm unused (epoch) -> first, by key; gemini used -> last
    assert out == ["deepseek", "glm", "gemini"]


def test_two_consecutive_runs_pick_different_pairs_with_three_contributors():
    contributors = ["gemini", "deepseek", "glm"]
    # run 1: nobody used yet
    ordered = order_contributors(contributors, last_used_at={})
    run1 = pick_verifiers(ordered, 2)
    now = datetime(2026, 1, 3, tzinfo=UTC)
    used = {k: now for k in run1}
    # run 2: the unused one comes first
    ordered2 = order_contributors(contributors, last_used_at=used)
    run2 = pick_verifiers(ordered2, 2)
    assert set(run1) != set(run2)  # different verifier pair


def test_two_contributors_both_always_used():
    contributors = ["gemini", "deepseek"]
    ordered = order_contributors(contributors, last_used_at={})
    assert set(pick_verifiers(ordered, 2)) == {"gemini", "deepseek"}
