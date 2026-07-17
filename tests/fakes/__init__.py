"""Shared fakes for later specs' tests (SPEC-DOMAIN §9.4).

No fake here ever opens a socket — the autouse network guard in tests/unit would catch
it. ``FakeHarnessDriver`` is defined here (not in SPEC-RUNNER) so SPEC-CORE's phase-1.3
orchestrator tests can exercise the full run/gate flow without any real runner existing
yet. ``FakeClock`` lets tests advance time deterministically (leases, backoffs, scoring).
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol
from uuid import UUID, uuid4


class FakeClock:
    """A controllable UTC clock. ``now()`` returns the current fake time."""

    def __init__(self, start: datetime | None = None) -> None:
        self._now = start or datetime(2026, 1, 1, tzinfo=UTC)

    def now(self) -> datetime:
        return self._now

    def advance(self, **delta: float | int) -> None:
        self._now = self._now + timedelta(**delta)  # type: ignore[arg-type]

    def __call__(self) -> datetime:
        return self._now


class HarnessDriver(Protocol):
    """Minimal runner-facing surface (full interface is SPEC-RUNNER's job)."""

    def launch(self, task: dict[str, Any]) -> UUID: ...
    def collect(self, attempt_id: UUID) -> dict[str, Any]: ...


class FakeHarnessDriver:
    """Records every launch and returns a canned ``stage_result.yaml`` per role.

    A real driver runs a sandboxed CLI for minutes-to-hours; this one returns instantly,
    so orchestrator/gate behaviour is fully testable without LLMs. Per-role canned results
    are configurable; the default makes every stage succeed with a trivial result.
    """

    def __init__(
        self,
        results: dict[str, dict[str, Any]] | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._results = results or {}
        self._clock = clock or (lambda: datetime.now(UTC))
        self.launches: list[dict[str, Any]] = []

    def launch(self, task: dict[str, Any]) -> UUID:
        attempt_id = uuid4()
        self.launches.append({"attempt_id": attempt_id, "task": task})
        return attempt_id

    def collect(self, attempt_id: UUID) -> dict[str, Any]:
        # resolve the canned result from the most recent launch's role, else a default
        role = self.launches[-1]["task"].get("role") if self.launches else "default"
        if role in self._results:
            return self._results[role]
        return {
            "status": "completed",
            "changes": [f"fake {role} pass"],
            "predictions": [],
        }


# ---------------------------------------------------------------- factories


def make_house(session, key: str = "gpt", *, provider: str = "openai", **kw):
    from core.db import models

    h = models.House(
        key=key, display_name=kw.get("display_name", key.upper()), provider=provider, **kw
    )
    session.add(h)
    session.flush()
    return h


def make_coverage(
    session,
    *,
    ticker: str = "2267",
    exchange: str = "tse",
    name: str = "Yakult",
    currency: str = "JPY",
    lead_house: str = "gpt",
):
    """Active coverage with a lead — the common precondition for run/stage factories."""
    from core.db import models
    from core.domain.dossier_slug import compute_dossier_slug
    from core.domain.enums import CoverageState

    c = models.Coverage(
        ticker=ticker,
        exchange=exchange,
        name=name,
        currency=currency,
        state=CoverageState.ACTIVE.value,
        dossier_slug=compute_dossier_slug(exchange, ticker),
        lead_house=lead_house,
    )
    session.add(c)
    session.flush()
    return c


def make_run(
    session,
    coverage_id: UUID,
    *,
    type: str = "initiation",
    house: str = "gpt",
    lead_house: str | None = None,
    mutate: bool | None = None,
):
    from core.db import models
    from core.domain.enums import RunType

    t = type.value if hasattr(type, "value") else type
    if mutate is None:
        mutate = t not in {RunType.MONITOR_TICK.value, RunType.PM_QUERY.value}
    r = models.Run(
        coverage_id=coverage_id,
        type=t,
        status="queued",
        trigger="pm",
        mutates_dossier=mutate,
        params={"lead_house": lead_house or house},
    )
    session.add(r)
    session.flush()
    return r


def make_stage(
    session,
    run_id: UUID,
    *,
    seq: int = 1,
    role: str = "author",
    house: str = "gpt",
    substrate: str = "harness",
    max_attempts: int = 3,
):
    from core.db import models
    from core.db.types import utc_now

    s = models.RunStage(
        run_id=run_id,
        seq=seq,
        role=role,
        house=house,
        substrate=substrate,
        status="queued",
        available_at=utc_now(),
        max_attempts=max_attempts,
    )
    session.add(s)
    session.flush()
    return s


__all__ = [
    "FakeClock",
    "FakeHarnessDriver",
    "HarnessDriver",
    "make_coverage",
    "make_house",
    "make_run",
    "make_stage",
]
