"""Settings — the single reader of secrets/config (SPEC-CORE §2.1; AGENTS.md Rule 4).

Operator config and secrets live outside the deploy tree; the systemd unit loads
``/etc/ai-fund/.env`` into the environment and pydantic-settings reads it. Code references
``settings.foo`` / ``SecretStr``, never ``os.getenv`` for a secret, never ``open(.env)``.
``core_api_token`` is required (the §9.3 "missing secret" check targets it); every other
field has a dev default so a test can construct ``Settings(_env_file=None, ...)``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore", frozen=True
    )

    config_dir: Path = Path("/etc/ai-fund")
    database_url: str = "postgresql://ai_fund:replace_me@localhost:5432/ai_fund"
    artifacts_dir: Path = Path("/var/lib/ai-fund/artifacts")
    dossier_repo_path: Path = Path("/var/lib/ai-fund/dossiers")
    doctrine_dir: Path = Path("/srv/ai-fund/doctrine")
    core_api_url: str = "http://127.0.0.1:8080"
    core_api_token: SecretStr  # required — the one secret every deployment must set
    pm_user_ids: list[str] = []
    market_data_url: str = "http://127.0.0.1:8090"
    search_endpoint: str | None = None
    provider_keys: dict[str, SecretStr] = {}  # resolved from houses.yaml api_key_env at load
    log_level: str = "INFO"
    env: Literal["dev", "staging", "prod"] = "dev"


_settings: Settings | None = None


def get_settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings


def reset_settings() -> None:
    """Test hook: drop the cached Settings so the next read picks up env changes."""
    global _settings
    _settings = None
