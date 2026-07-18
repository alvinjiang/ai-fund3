"""Pure exponential-backoff policy (SPEC-DOMAIN §6.2).

Randomness lives at the caller; ``jitter_factor`` is the injected seam so the curve
itself is deterministic and testable.
"""

from __future__ import annotations

import pytest

from core.domain.backoff import BackoffPolicy, backoff_seconds

POLICY = BackoffPolicy(base_s=30, max_s=3600, jitter=0.2)


@pytest.mark.parametrize(
    "attempts, expected",
    [(1, 30), (2, 60), (3, 120), (4, 240), (5, 480)],
)
def test_doubles_per_attempt(attempts, expected):
    assert backoff_seconds(attempts, POLICY, jitter_factor=0.0) == expected


def test_clamped_at_max():
    assert backoff_seconds(20, POLICY, jitter_factor=0.0) == 3600


def test_jitter_spans_the_configured_band():
    assert backoff_seconds(1, POLICY, jitter_factor=1.0) == pytest.approx(36.0)
    assert backoff_seconds(1, POLICY, jitter_factor=-1.0) == pytest.approx(24.0)


def test_never_negative():
    wild = BackoffPolicy(base_s=30, max_s=3600, jitter=5.0)
    assert backoff_seconds(1, wild, jitter_factor=-1.0) == 0.0


def test_first_attempt_is_the_base_delay():
    """attempts=0 must not invert the exponent into a fractional delay."""
    assert backoff_seconds(0, POLICY, jitter_factor=0.0) == 30
