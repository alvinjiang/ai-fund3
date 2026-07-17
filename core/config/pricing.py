"""pricing.yaml model + the single price resolver (SPEC-CORE §2.4).

The ONLY place a model price exists. An unpriced model is a hard error (checkconfig and
cost-attribution time) — never a silently-wrong zero.
"""

from __future__ import annotations

from decimal import Decimal

import yaml
from pydantic import BaseModel, ConfigDict


class ModelPrice(BaseModel):
    model_config = ConfigDict(extra="ignore")
    input_per_mtok: Decimal
    output_per_mtok: Decimal
    cached_input_per_mtok: Decimal = Decimal("0")
    currency: str = "USD"


class PricingConfig(BaseModel):
    model_config = ConfigDict(extra="ignore")
    models: dict[str, ModelPrice]

    def has(self, model_id: str) -> bool:
        return model_id in self.models


def load_pricing(yaml_text: str) -> PricingConfig:
    return PricingConfig.model_validate(yaml.safe_load(yaml_text))


def price_call(
    pricing: PricingConfig,
    model: str,
    input_tokens: int,
    output_tokens: int,
    cached_input_tokens: int = 0,
) -> Decimal:
    """Dollar cost = billable_input*in + cached*cached_in + output*out, per million tokens."""
    if not pricing.has(model):
        raise KeyError(f"unpriced model {model!r}")  # never a silent zero
    p = pricing.models[model]
    billable_input = max(input_tokens - cached_input_tokens, 0)
    return (
        Decimal(billable_input) * p.input_per_mtok / Decimal(1_000_000)
        + Decimal(cached_input_tokens) * p.cached_input_per_mtok / Decimal(1_000_000)
        + Decimal(output_tokens) * p.output_per_mtok / Decimal(1_000_000)
    )
