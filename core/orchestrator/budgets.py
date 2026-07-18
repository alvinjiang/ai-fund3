"""Budget helpers (SPEC-CORE §3.5 / §8 module layout).

Extracted from the engine so the budget logic is independently testable and the engine
reads cleanly. The per-run cap check + pause-for-budget are here; the per-house daily
cap (house_budget_days reserve/settle) is deferred to the runner/cost port (phase 2/7.2).
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from core.db import models
from core.db.repo import runs as runs_repo
from core.domain.enums import RunStatus
from core.domain.run_sm import RunAction, RunTransitionContext, run_transition


def run_over_budget(run: models.Run) -> bool:
    """True if the run's accumulated cost exceeds its cap."""
    return bool(
        run.budget_cap_usd and run.cost_usd is not None and run.cost_usd > run.budget_cap_usd
    )


def pause_for_budget(session: Session, run: models.Run) -> None:
    """Open a ``budget_cap`` gate and move the run to ``waiting_pm``."""
    runs_repo.open_gate(
        session,
        run.id,
        kind="budget_cap",
        prompt=f"Run has spent {run.cost_usd} of cap {run.budget_cap_usd}; raise or cancel?",
        payload={"cost_usd": str(run.cost_usd), "cap_usd": str(run.budget_cap_usd)},
        allowed_answers=["raise_cap", "cancel"],
    )
    sm = run_transition(
        RunStatus(run.status), RunAction.OPEN_GATE, RunTransitionContext(attempts=0, max_attempts=1)
    )
    run.status = sm.to_state.value
    session.flush()
