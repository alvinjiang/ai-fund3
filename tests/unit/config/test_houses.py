"""houses.yaml validation + provider-key resolution — SPEC-CORE §2.2 / §9.3."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from core.config.houses import HouseConfig, HousesConfig, load_houses, resolve_provider_keys


def _house(**kw):
    base = {"display_name": "X", "provider": "openai", "api_key_env": "OPENAI_API_KEY"}
    base.update(kw)
    return base


def test_assignable_and_meta_mutually_exclusive():
    with pytest.raises(ValidationError):
        HouseConfig(**_house(assignable=True, meta=True))


def test_zero_assignable_houses_rejected():
    with pytest.raises(ValidationError):
        HousesConfig(
            houses={
                "claude": HouseConfig(**_house(assignable=False, meta=True)),
            }
        )


def test_load_houses_from_yaml_text():
    yaml_text = """
houses:
  gpt:
    display_name: GPT
    provider: openai
    api_key_env: OPENAI_API_KEY
    models: {heavy: gpt-x, light: gpt-y}
    harness: {type: codex-cli}
    budgets: {daily_usd: 25, per_run_usd: 40}
    assignable: true
    enabled: true
  claude:
    display_name: Claude
    provider: anthropic
    api_key_env: ANTHROPIC_API_KEY
    assignable: false
    meta: true
    enabled: true
"""
    cfg = load_houses(yaml_text)
    assert "gpt" in cfg.houses and "claude" in cfg.houses
    assert cfg.houses["claude"].meta is True
    assert cfg.houses["gpt"].harness.type == "codex-cli"


def test_resolve_provider_keys_names_missing_variable_not_value():
    cfg = HousesConfig(houses={"gpt": HouseConfig(**_house(api_key_env="OPENAI_API_KEY"))})
    # env lacks OPENAI_API_KEY -> raises naming the variable, never a value
    with pytest.raises(KeyError) as exc:
        resolve_provider_keys(cfg, env={})
    assert "OPENAI_API_KEY" in str(exc.value)


def test_resolve_provider_keys_returns_secretstrs():
    cfg = HousesConfig(houses={"gpt": HouseConfig(**_house(api_key_env="OPENAI_API_KEY"))})
    keys = resolve_provider_keys(cfg, env={"OPENAI_API_KEY": "sk-test"})
    assert keys["OPENAI_API_KEY"].get_secret_value() == "sk-test"
