"""Contributor rotation (SPEC-CORE §3.2).

Rotation state is *derived* from prior stage rows, never stored as a cursor. Contributors
are ordered by ``(last_used_at ASC, key ASC)`` so verify pairs do not calcify.
"""

from __future__ import annotations

from datetime import UTC, datetime

_EPOCH = datetime.min.replace(tzinfo=UTC)


def order_contributors(
    contributors: list[str], last_used_at: dict[str, datetime | None]
) -> list[str]:
    """Least-recently-used first (unused = epoch), key breaks ties."""
    return sorted(contributors, key=lambda k: (last_used_at.get(k) or _EPOCH, k))


def pick_verifiers(ordered: list[str], n: int) -> list[str]:
    return list(ordered[: max(n, 0)])
