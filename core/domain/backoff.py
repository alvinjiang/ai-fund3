"""Exponential retry backoff with jitter (SPEC-DOMAIN §6.2).

Pure: the caller owns both the config values (``BackoffPolicy``, resolved from
``fund.queue`` by the orchestrator) and the randomness (``jitter_factor``), so the
curve is deterministic under test. No field has a default — a missing config value is
a ``checkconfig`` failure, never a helpful fallback.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class BackoffPolicy:
    base_s: int
    max_s: int
    jitter: float


def backoff_seconds(attempts: int, policy: BackoffPolicy, *, jitter_factor: float) -> float:
    """Delay before a failed stage's next attempt.

    ``attempts`` is the count already made; ``jitter_factor`` is in [-1, 1].
    """
    raw = min(policy.base_s * 2 ** max(0, attempts - 1), policy.max_s)
    return max(0.0, raw * (1.0 + policy.jitter * jitter_factor))
