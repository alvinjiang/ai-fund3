"""checkconfig — SPEC-CORE §2.6 / §9.3."""

from __future__ import annotations

from decimal import Decimal

from core.config.checkconfig import any_fail, run_checks
from core.config.fund import FundConfig, RunPolicy
from core.config.houses import HouseConfig, HousesConfig
from core.config.pricing import ModelPrice, PricingConfig
from core.config.settings import Settings
from core.config.store import Config


def _config(with_price: bool = True, *, models: dict | None = None) -> Config:
    house_models = models if models is not None else {"heavy": "gpt-x", "light": "gpt-y"}
    return Config(
        houses=HousesConfig(
            houses={
                "gpt": HouseConfig(
                    display_name="GPT",
                    provider="openai",
                    api_key_env="OPENAI_API_KEY",
                    models=house_models,
                )
            }
        ),
        fund=FundConfig(runs={"initiation": RunPolicy(budget_cap_usd=Decimal("40"))}),
        pricing=PricingConfig(
            models={}
            if not with_price
            else {
                "gpt-x": ModelPrice(input_per_mtok=Decimal("1"), output_per_mtok=Decimal("2")),
                "gpt-y": ModelPrice(input_per_mtok=Decimal("1"), output_per_mtok=Decimal("2")),
            }
        ),
    )


def _settings(pm: list[str] | None = None) -> Settings:
    return Settings(
        _env_file=None, core_api_token="t", pm_user_ids=pm if pm is not None else ["pm1"]
    )


def test_valid_config_passes_all_checks():
    results = run_checks(_config(), _settings(), env={"OPENAI_API_KEY": "x"})
    assert not any_fail(results)


def test_unpriced_model_is_fail():
    results = run_checks(_config(with_price=False), _settings(), env={"OPENAI_API_KEY": "x"})
    names = {r[0]: r[1] for r in results}
    assert names["models_priced"] == "FAIL"


def test_missing_api_key_named_without_value():
    results = run_checks(_config(), _settings(), env={})  # OPENAI_API_KEY absent
    detail = next(r[2] for r in results if r[0] == "api_keys_present")
    assert "OPENAI_API_KEY" in detail  # the variable NAME is named
    assert "x" not in detail.split("name only")[-1]  # never a value


def test_empty_pm_allowlist_is_fail():
    results = run_checks(_config(), _settings(pm=[]), env={"OPENAI_API_KEY": "x"})
    names = {r[0]: r[1] for r in results}
    assert names["pm_user_ids"] == "FAIL"
