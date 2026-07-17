"""Back-compat shim: the full settings live in core.config.settings (SPEC-CORE §2.1).

SPEC-DOMAIN's session.py and alembic env.py import from ``core.settings``; keep that path
working while the authoritative module moves under ``core/config/``.
"""

from core.config.settings import Settings, get_settings, reset_settings

__all__ = ["Settings", "get_settings", "reset_settings"]
