"""Input dataclasses for repository calls (SPEC-DOMAIN §8).

Frozen, typed, and the only shape callers pass into the repo layer. ``direction`` and
``pinned_price`` on ``PredictionIn`` are the §12 amendment (SPEC-TRACKREC §2.2): the
orchestrator sets them from the subject price at registration, never the model.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from uuid import UUID


@dataclass(frozen=True)
class LevelIn:
    kind: str  # entry | target | stop | review
    value: Decimal
    currency: str
    direction: str  # below | above
    set_by: str = "pm"


@dataclass(frozen=True)
class StageIn:
    seq: int
    role: str
    house: str
    substrate: str
    max_attempts: int = 3
    depends_on_seq: int | None = None


@dataclass(frozen=True)
class PredictionIn:
    kind: str
    horizon_date: date
    value: Decimal | None = None
    currency: str | None = None
    stance: str | None = None
    scenario_label: str | None = None
    scenario_prob: Decimal | None = None
    scenario_group: UUID | None = None
    confidence: Decimal | None = None
    rationale_ref: str | None = None
    direction: str | None = None
    pinned_price: Decimal | None = None


@dataclass(frozen=True)
class AttemptIn:
    worker_id: str
    model: str | None = None
    transcript_ref: str | None = None
    result: dict | None = None
    validation_errors: list | None = None
    cost_usd: Decimal = Decimal("0")
    cost_source: str = "estimated"
    tokens_in: int | None = None
    tokens_out: int | None = None
    cached_tokens_in: int | None = None
    exit_code: int | None = None
    kill_reason: str | None = None
