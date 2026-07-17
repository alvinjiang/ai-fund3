"""houses.yaml model + validation + provider-key resolution (SPEC-CORE §2.2).

Validation: ``assignable`` and ``meta`` are mutually exclusive; at least one house is
``enabled AND assignable``. Provider keys are not enumerated in code — houses.yaml names
the env var (``api_key_env``); ``resolve_provider_keys`` reads it once into a
``dict[name, SecretStr]``. A missing env var raises naming the variable, never a value.
"""

from __future__ import annotations

from decimal import Decimal

import yaml
from pydantic import BaseModel, ConfigDict, Field, SecretStr, model_validator


class HarnessConfig(BaseModel):
    model_config = ConfigDict(extra="allow")
    type: str
    config: dict = Field(default_factory=dict)


class HouseBudgets(BaseModel):
    daily_usd: Decimal = Decimal("0")
    per_run_usd: Decimal = Decimal("0")


class HouseConfig(BaseModel):
    model_config = ConfigDict(extra="ignore")
    display_name: str
    provider: str
    api_key_env: str
    base_url_env: str | None = None
    models: dict[str, str] = Field(default_factory=dict)
    harness: HarnessConfig | None = None
    budgets: HouseBudgets = Field(default_factory=HouseBudgets)
    assignable: bool = True
    meta: bool = False
    enabled: bool = True

    @model_validator(mode="after")
    def _meta_not_assignable(self) -> HouseConfig:
        if self.assignable and self.meta:
            raise ValueError("a house cannot be both assignable and meta")
        return self


class HousesConfig(BaseModel):
    model_config = ConfigDict(extra="ignore")
    houses: dict[str, HouseConfig]

    @model_validator(mode="after")
    def _at_least_one_assignable(self) -> HousesConfig:
        if not any(h.enabled and h.assignable for h in self.houses.values()):
            raise ValueError("at least one house must be enabled AND assignable")
        return self


def load_houses(yaml_text: str) -> HousesConfig:
    return HousesConfig.model_validate(yaml.safe_load(yaml_text))


def resolve_provider_keys(houses: HousesConfig, *, env: dict[str, str]) -> dict[str, SecretStr]:
    """Resolve each house's ``api_key_env`` to a SecretStr from the environment.

    Raises ``KeyError(name)`` — naming the variable, never a value — if one is missing.
    """
    out: dict[str, SecretStr] = {}
    for h in houses.houses.values():
        name = h.api_key_env
        if name not in env:
            raise KeyError(name)  # name only, per AGENTS.md Rule 6
        out[name] = SecretStr(env[name])
    return out
