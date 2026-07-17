"""Contributor resolution — SPEC-DOMAIN §4.3.

When a coverage has no explicit ``coverage_contributors`` rows, contributors are all
houses where ``enabled AND assignable AND key != lead_house`` (evaluated at run-creation
time, then materialized into the run's stage rows). A meta house is never a contributor.
"""

from core.domain.contributors import HouseInfo, resolve_contributors


def test_default_contributors_are_all_enabled_assignable_except_lead():
    houses = [
        HouseInfo("gpt", enabled=True, assignable=True),
        HouseInfo("gemini", enabled=True, assignable=True),
        HouseInfo("deepseek", enabled=True, assignable=True),
        HouseInfo("glm", enabled=True, assignable=True),
        HouseInfo("qwen", enabled=True, assignable=True),
    ]
    assert resolve_contributors("gpt", houses) == ["deepseek", "gemini", "glm", "qwen"]


def test_disabled_house_excluded():
    houses = [
        HouseInfo("gpt", enabled=True, assignable=True),
        HouseInfo("gemini", enabled=False, assignable=True),
        HouseInfo("deepseek", enabled=True, assignable=True),
    ]
    assert resolve_contributors("gpt", houses) == ["deepseek"]


def test_non_assignable_house_excluded():
    houses = [
        HouseInfo("gpt", enabled=True, assignable=True),
        HouseInfo("claude", enabled=True, assignable=False, meta=True),
        HouseInfo("deepseek", enabled=True, assignable=True),
    ]
    # the meta house (Claude) is never a contributor even if mis-set assignable
    assert resolve_contributors("gpt", houses) == ["deepseek"]


def test_result_is_sorted_for_stable_materialization():
    houses = [
        HouseInfo("qwen", enabled=True, assignable=True),
        HouseInfo("deepseek", enabled=True, assignable=True),
        HouseInfo("gemini", enabled=True, assignable=True),
    ]
    assert resolve_contributors("gpt", houses) == ["deepseek", "gemini", "qwen"]


def test_no_other_houses_returns_empty():
    houses = [HouseInfo("gpt", enabled=True, assignable=True)]
    assert resolve_contributors("gpt", houses) == []
