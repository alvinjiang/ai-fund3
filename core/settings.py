"""Settings — the single reader of secrets/config (AGENTS.md Rule 4).

Minimal for SPEC-DOMAIN (database URL + artifacts dir + log level); SPEC-CORE extends
this with houses/fund/pricing config loading. Code references ``settings.foo``, never
``os.getenv``, never an ``open(".env")``. The operator's ``/etc/ai-fund/.env`` is loaded
into the process environment by systemd (EnvironmentFile); pydantic-settings reads it.
"""

from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    database_url: str = "postgresql://ai_fund:replace_me@localhost:5432/ai_fund"
    artifacts_dir: str = "/var/lib/ai-fund/artifacts"
    log_level: str = "INFO"


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
