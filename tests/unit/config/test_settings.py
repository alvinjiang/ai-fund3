"""Settings — SPEC-CORE §2.1 / §9.3.

A missing required secret raises naming the *variable*, never its value. Tests construct
``Settings(_env_file=None, ...)`` so nothing on disk is read.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from core.config.settings import Settings


def test_settings_construct_with_overrides_no_file():
    s = Settings(_env_file=None, core_api_token="tok-abc", database_url="postgresql://x/x")
    assert s.core_api_token.get_secret_value() == "tok-abc"
    assert s.env == "dev"


def test_missing_required_secret_raises_naming_the_variable_not_value():
    # core_api_token is required; omitting it must raise, and the message must name the
    # variable name (CORE_API_TOKEN) — never any value.
    with pytest.raises(ValidationError) as exc:
        Settings(_env_file=None, database_url="postgresql://x/x")
    msg = str(exc.value)
    assert "core_api_token" in msg.lower() or "core_api_token" in repr(exc.value).lower()


def test_settings_is_frozen():
    from pydantic import ValidationError

    s = Settings(_env_file=None, core_api_token="t")
    with pytest.raises(ValidationError):
        s.core_api_token = "other"  # type: ignore[misc]
