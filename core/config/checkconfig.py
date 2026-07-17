"""checkconfig (SPEC-CORE §2.6).

Runs the operator-facing preflight checks and returns a table of OK/WARN/FAIL. ``--strict``
exits non-zero on any FAIL (it is the systemd ``ExecStartPre``). Presence checks never
print a secret value — a missing ``api_key_env`` is reported by variable name only.
"""

from __future__ import annotations

import os

from core.config.settings import Settings
from core.config.store import Config


def run_checks(
    config: Config, settings: Settings, *, env: dict[str, str] | None = None
) -> list[tuple[str, str, str]]:
    env = env if env is not None else dict(os.environ)
    results: list[tuple[str, str, str]] = []

    assignable = [k for k, h in config.houses.houses.items() if h.enabled and h.assignable]
    results.append(
        (
            "assignable_house",
            "OK" if assignable else "FAIL",
            "" if assignable else "no enabled AND assignable house",
        )
    )

    missing_models: list[str] = []
    for key, h in config.houses.houses.items():
        for slot, mid in h.models.items():
            if mid and not config.pricing.has(mid):
                missing_models.append(f"{key}.{slot}={mid}")
    results.append(
        (
            "models_priced",
            "OK" if not missing_models else "FAIL",
            "" if not missing_models else f"unpriced models: {missing_models}",
        )
    )

    missing_keys = [
        h.api_key_env for h in config.houses.houses.values() if h.api_key_env not in env
    ]
    results.append(
        (
            "api_keys_present",
            "OK" if not missing_keys else "FAIL",
            ""
            if not missing_keys
            else f"missing env vars (name only): {sorted(set(missing_keys))}",
        )
    )

    results.append(
        (
            "pm_user_ids",
            "OK" if settings.pm_user_ids else "FAIL",
            "" if settings.pm_user_ids else "pm_user_ids is empty",
        )
    )
    return results


def any_fail(results: list[tuple[str, str, str]]) -> bool:
    return any(status == "FAIL" for _, status, _ in results)


def format_table(results: list[tuple[str, str, str]]) -> str:
    lines = ["CHECK                          STATUS  DETAIL"]
    for name, status, detail in results:
        lines.append(f"{name:<30} {status:<7} {detail}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:  # pragma: no cover (wired by SPEC-CORE CLI later)
    raise SystemExit(
        1 if any_fail(run_checks(_load(), Settings(_env_file=None, core_api_token="x"))) else 0
    )


def _load() -> Config:  # pragma: no cover
    from core.config.store import get_config

    return get_config()
