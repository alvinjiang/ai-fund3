"""FakeRunner — the phase-1.3 keystone (SPEC-CORE §9.1).

Stands in for the real runner (SPEC-RUNNER, phase 2) so the orchestrator/gate/budget flow
is fully testable with zero LLM calls. Claims stages through the REAL ``queue.claim_stage``,
writes a canned ``stage_result`` per role, and completes or fails them. Configurable to:
fail a role a fixed number of times (then succeed) — set ``fail_times`` large for terminal
exhaustion; hang a role (leave it running so its lease expires). No socket is opened.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from core.db import queue
from core.db.schemas import AttemptIn
from core.domain.backoff import BackoffPolicy

# The fake drives stages back-to-back; real backoff would make a retried stage
# unclaimable for the rest of the loop. Orchestrator tests that care about the
# delay itself should call queue.fail_stage directly with a real policy.
NO_DELAY = BackoffPolicy(base_s=0, max_s=0, jitter=0.0)


def _default_result(role: str) -> dict[str, Any]:
    if role == "author":
        return {
            "status": "completed",
            "changes": ["author pass"],
            "predictions": [
                {
                    "kind": "target_price",
                    "value": 2900,
                    "currency": "JPY",
                    "horizon_date": "2027-01-01",
                    "confidence": 0.6,
                }
            ],
        }
    if role == "verifier":
        return {"status": "completed", "changes": ["verifier pass"], "corrections": []}
    if role == "finalizer":
        return {"status": "completed", "changes": ["finalized"], "predictions": []}
    return {"status": "completed", "changes": [f"{role} pass"]}


class FakeRunner:
    def __init__(
        self,
        *,
        results: dict[str, dict[str, Any]] | None = None,
        fail_role: str | None = None,
        fail_times: int = 0,
        hang_roles: set[str] | None = None,
        cost_usd: Decimal = Decimal("0.01"),
    ) -> None:
        self.results = results or {}
        self.fail_role = fail_role
        self.fail_times = fail_times
        self.hang_roles = set(hang_roles or ())
        self.cost_usd = cost_usd
        self._fail_counts: dict[str, int] = {}
        self.claimed: list = []

    def drain(self, session, worker_id: str = "fake-runner", lease_seconds: int = 300) -> int:
        """Claim and process every currently-claimable stage. Returns the number claimed."""
        n = 0
        while True:
            stage = queue.claim_stage(session, worker_id, lease_seconds=lease_seconds)
            if stage is None:
                break
            if stage.role in self.hang_roles:
                session.flush()  # leave it running — lease will expire
                break
            self.claimed.append(stage.id)
            attempt = AttemptIn(worker_id=worker_id, cost_usd=self.cost_usd)
            if (
                stage.role == self.fail_role
                and self._fail_counts.get(stage.role, 0) < self.fail_times
            ):
                self._fail_counts[stage.role] = self._fail_counts.get(stage.role, 0) + 1
                queue.fail_stage(session, stage.id, attempt, retryable=True, backoff=NO_DELAY)
            else:
                result = self.results.get(stage.role) or _default_result(stage.role)
                queue.complete_stage(session, stage.id, attempt, result)
            n += 1
        return n
