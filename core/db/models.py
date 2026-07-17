"""SQLAlchemy models — SPEC-DOMAIN §4 (amended by §12).

Every constraint listed in the spec is required. The module is intentionally a single
file: it is the schema contract every later spec consumes. No LLM, no HTTP, no business
policy constants live here — only shape and the integrity rules that are independent of
policy (money is Numeric; state strings are CHECK-constrained; identity is Uuid; the
research/audit path is never hard-deleted).
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from core.db.base import Base, TimestampMixin
from core.db.types import Cost, JsonType, Money, Price, Prob

# ============================================================ houses


class House(Base, TimestampMixin):
    __tablename__ = "houses"
    key: Mapped[str] = mapped_column(String(32), primary_key=True)
    display_name: Mapped[str] = mapped_column(String(64), nullable=False)
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    assignable: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    meta: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    harness_type: Mapped[str | None] = mapped_column(String(32))
    config_digest: Mapped[str | None] = mapped_column(String(64))
    __table_args__ = (
        CheckConstraint("NOT (assignable AND meta)", name="ck_houses_meta_not_assignable"),
    )


# ============================================================ coverage


class Coverage(Base, TimestampMixin):
    __tablename__ = "coverage"
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    ticker: Mapped[str] = mapped_column(String(16), nullable=False)
    exchange: Mapped[str] = mapped_column(String(16), nullable=False)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    currency: Mapped[str] = mapped_column(String(8), nullable=False)
    isin: Mapped[str | None] = mapped_column(String(12))
    state: Mapped[str] = mapped_column(String(24), nullable=False)
    lead_house: Mapped[str | None] = mapped_column(ForeignKey("houses.key", ondelete="RESTRICT"))
    dossier_slug: Mapped[str] = mapped_column(String(48), nullable=False)
    earnings_calendar_ref: Mapped[str | None] = mapped_column(String(64))
    exchange_session_ref: Mapped[str | None] = mapped_column(String(32))
    pm_notes: Mapped[str | None] = mapped_column(Text)
    proposed_by: Mapped[str | None] = mapped_column(String(64))
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    exited_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    __table_args__ = (
        UniqueConstraint("exchange", "ticker", name="uq_coverage_exchange_ticker"),
        UniqueConstraint("dossier_slug", name="uq_coverage_dossier_slug"),
        CheckConstraint(
            "state IN ('proposed','initiating','decision_pending','active','watch',"
            "'rejected','exited','failed')",
            name="ck_coverage_state",
        ),
        CheckConstraint(
            "(state IN ('active','watch','decision_pending')) = (lead_house IS NOT NULL) "
            "OR state NOT IN ('active','watch','decision_pending')",
            name="ck_coverage_lead_required_when_live",
        ),
        Index("ix_coverage_state", "state"),
    )


class CoverageContributor(Base):
    __tablename__ = "coverage_contributors"
    coverage_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("coverage.id", ondelete="CASCADE"), primary_key=True
    )
    house: Mapped[str] = mapped_column(
        String(32), ForeignKey("houses.key", ondelete="RESTRICT"), primary_key=True
    )
    added_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    added_by: Mapped[str | None] = mapped_column(String(64))


class CoverageLevel(Base):
    __tablename__ = "coverage_levels"
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    coverage_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("coverage.id", ondelete="CASCADE"), nullable=False
    )
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    value: Mapped[Any] = mapped_column(Price, nullable=False)
    currency: Mapped[str] = mapped_column(String(8), nullable=False)
    direction: Mapped[str] = mapped_column(String(8), nullable=False)
    set_by: Mapped[str] = mapped_column(String(16), nullable=False)
    set_by_run_id: Mapped[UUID | None] = mapped_column(Uuid, ForeignKey("runs.id"))
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    superseded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    __table_args__ = (
        CheckConstraint(
            "kind IN ('entry','target','stop','review')", name="ck_coverage_levels_kind"
        ),
        CheckConstraint("direction IN ('below','above')", name="ck_coverage_levels_direction"),
        CheckConstraint("value > 0", name="ck_coverage_levels_value_positive"),
        Index("ix_coverage_levels_active", "coverage_id", "active"),
    )


class CoverageTransition(Base):
    __tablename__ = "coverage_transitions"
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    coverage_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("coverage.id", ondelete="CASCADE"), nullable=False
    )
    from_state: Mapped[str | None] = mapped_column(String(24))
    to_state: Mapped[str] = mapped_column(String(24), nullable=False)
    actor_type: Mapped[str] = mapped_column(String(16), nullable=False)
    actor_id: Mapped[str | None] = mapped_column(String(64))
    cause: Mapped[str] = mapped_column(String(64), nullable=False)
    run_id: Mapped[UUID | None] = mapped_column(Uuid, ForeignKey("runs.id"))
    notes: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    __table_args__ = (
        Index("ix_coverage_transitions_coverage_created", "coverage_id", "created_at"),
    )


class CoverageLeadHistory(Base):
    __tablename__ = "coverage_lead_history"
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    coverage_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("coverage.id", ondelete="CASCADE"), nullable=False
    )
    from_house: Mapped[str | None] = mapped_column(ForeignKey("houses.key", ondelete="RESTRICT"))
    to_house: Mapped[str] = mapped_column(
        String(32), ForeignKey("houses.key", ondelete="RESTRICT"), nullable=False
    )
    run_id: Mapped[UUID | None] = mapped_column(Uuid, ForeignKey("runs.id"))
    approved_by: Mapped[str] = mapped_column(String(64), nullable=False)
    rationale: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


# ============================================================ runs


class Run(Base, TimestampMixin):
    __tablename__ = "runs"
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    coverage_id: Mapped[UUID | None] = mapped_column(Uuid, ForeignKey("coverage.id"))
    type: Mapped[str] = mapped_column(String(24), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    trigger: Mapped[str] = mapped_column(String(32), nullable=False)
    trigger_ref: Mapped[str | None] = mapped_column(String(128))
    priority: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("100"))
    mutates_dossier: Mapped[bool] = mapped_column(Boolean, nullable=False)
    requested_by: Mapped[str | None] = mapped_column(String(64))
    params: Mapped[Any] = mapped_column(JsonType, nullable=False)
    doctrine_version_id: Mapped[UUID | None] = mapped_column(
        Uuid, ForeignKey("doctrine_versions.id")
    )
    budget_cap_usd: Mapped[Any] = mapped_column(Cost, nullable=True)
    cost_usd: Mapped[Any] = mapped_column(Cost, nullable=False, server_default=text("0"))
    summary: Mapped[str | None] = mapped_column(Text)
    pm_decision: Mapped[str | None] = mapped_column(String(32))
    dossier_commit_before: Mapped[str | None] = mapped_column(String(40))
    dossier_commit_after: Mapped[str | None] = mapped_column(String(40))
    error: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    __table_args__ = (
        CheckConstraint(
            "type IN ('initiation','deep_review','event_analysis','monitor_tick',"
            "'pm_query','lead_review','distillation')",
            name="ck_runs_type",
        ),
        CheckConstraint(
            "status IN ('queued','running','waiting_pm','succeeded','failed','cancelled')",
            name="ck_runs_status",
        ),
        CheckConstraint(
            "(coverage_id IS NOT NULL) OR type = 'distillation'", name="ck_runs_coverage_required"
        ),
        Index("ix_runs_status_priority", "status", "priority", "created_at"),
        Index("ix_runs_coverage_created", "coverage_id", "created_at"),
        # §12 amendment (SPEC-CORE §4): idempotent per (coverage, type, trigger_ref).
        Index(
            "uq_runs_trigger_ref",
            "coverage_id",
            "type",
            "trigger_ref",
            unique=True,
            postgresql_where=text("trigger_ref IS NOT NULL"),
            sqlite_where=text("trigger_ref IS NOT NULL"),
        ),
    )


class RunStage(Base, TimestampMixin):
    __tablename__ = "run_stages"
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    run_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("runs.id", ondelete="CASCADE"), nullable=False
    )
    seq: Mapped[int] = mapped_column(Integer, nullable=False)
    role: Mapped[str] = mapped_column(String(24), nullable=False)
    house: Mapped[str] = mapped_column(
        String(32), ForeignKey("houses.key", ondelete="RESTRICT"), nullable=False
    )
    substrate: Mapped[str] = mapped_column(String(8), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    depends_on_seq: Mapped[int | None] = mapped_column(Integer)
    available_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    claimed_by: Mapped[str | None] = mapped_column(String(64))
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False)
    workspace_ref: Mapped[str | None] = mapped_column(String(256))
    transcript_ref: Mapped[str | None] = mapped_column(String(256))
    artifacts_ref: Mapped[Any] = mapped_column(JsonType, nullable=True)
    result: Mapped[Any] = mapped_column(JsonType, nullable=True)
    cost_usd: Mapped[Any] = mapped_column(Cost, nullable=False, server_default=text("0"))
    tokens_in: Mapped[int | None] = mapped_column(Integer)
    tokens_out: Mapped[int | None] = mapped_column(Integer)
    wall_time_s: Mapped[int | None] = mapped_column(Integer)
    error: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    __table_args__ = (
        UniqueConstraint("run_id", "seq", name="uq_run_stages_run_seq"),
        CheckConstraint(
            "status IN ('queued','running','succeeded','failed','skipped')",
            name="ck_run_stages_status",
        ),
        CheckConstraint("substrate IN ('harness','api')", name="ck_run_stages_substrate"),
        Index("ix_run_stages_claimable", "status", "available_at", "id"),
        Index("ix_run_stages_lease", "status", "lease_expires_at"),
    )


class StageAttempt(Base):
    __tablename__ = "stage_attempts"
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    stage_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("run_stages.id", ondelete="CASCADE"), nullable=False
    )
    attempt_no: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    house: Mapped[str] = mapped_column(
        String(32), ForeignKey("houses.key", ondelete="RESTRICT"), nullable=False
    )
    model: Mapped[str | None] = mapped_column(String(64))
    substrate: Mapped[str] = mapped_column(String(8), nullable=False)
    worker_id: Mapped[str | None] = mapped_column(String(64))
    transcript_ref: Mapped[str | None] = mapped_column(String(256))
    result: Mapped[Any] = mapped_column(JsonType, nullable=True)
    validation_errors: Mapped[Any] = mapped_column(JsonType, nullable=True)
    cost_usd: Mapped[Any] = mapped_column(Cost, nullable=False, server_default=text("0"))
    cost_source: Mapped[str | None] = mapped_column(String(16))
    tokens_in: Mapped[int | None] = mapped_column(Integer)
    tokens_out: Mapped[int | None] = mapped_column(Integer)
    cached_tokens_in: Mapped[int | None] = mapped_column(Integer)
    exit_code: Mapped[int | None] = mapped_column(Integer)
    kill_reason: Mapped[str | None] = mapped_column(String(32))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    __table_args__ = (
        UniqueConstraint("stage_id", "attempt_no", name="uq_stage_attempts_stage_no"),
        CheckConstraint(
            "status IN ('running','succeeded','failed','killed','timeout')",
            name="ck_stage_attempts_status",
        ),
    )


class CoverageRunLock(Base):
    __tablename__ = "coverage_run_locks"
    coverage_id: Mapped[UUID] = mapped_column(Uuid, ForeignKey("coverage.id"), primary_key=True)
    run_id: Mapped[UUID] = mapped_column(Uuid, ForeignKey("runs.id"), nullable=False, unique=True)
    acquired_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class PmGate(Base, TimestampMixin):
    __tablename__ = "pm_gates"
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    run_id: Mapped[UUID] = mapped_column(Uuid, ForeignKey("runs.id"), nullable=False)
    coverage_id: Mapped[UUID | None] = mapped_column(Uuid, ForeignKey("coverage.id"))
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    state: Mapped[str] = mapped_column(String(16), nullable=False)
    prompt: Mapped[str] = mapped_column(Text, nullable=False)
    payload: Mapped[Any] = mapped_column(JsonType, nullable=False)
    allowed_answers: Mapped[Any] = mapped_column(JsonType, nullable=False)
    answer: Mapped[str | None] = mapped_column(String(32))
    answer_notes: Mapped[str | None] = mapped_column(Text)
    answered_by: Mapped[str | None] = mapped_column(String(64))
    answered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    idempotency_key: Mapped[str | None] = mapped_column(String(64), unique=True)
    __table_args__ = (Index("ix_pm_gates_state_created", "state", "created_at"),)


# ============================================================ predictions


class Prediction(Base):
    __tablename__ = "predictions"
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    coverage_id: Mapped[UUID] = mapped_column(Uuid, ForeignKey("coverage.id"), nullable=False)
    run_id: Mapped[UUID] = mapped_column(Uuid, ForeignKey("runs.id"), nullable=False)
    stage_id: Mapped[UUID] = mapped_column(Uuid, ForeignKey("run_stages.id"), nullable=False)
    house: Mapped[str] = mapped_column(
        String(32), ForeignKey("houses.key", ondelete="RESTRICT"), nullable=False
    )
    kind: Mapped[str] = mapped_column(String(24), nullable=False)
    value: Mapped[Any] = mapped_column(Price, nullable=True)
    currency: Mapped[str | None] = mapped_column(String(8))
    stance: Mapped[str | None] = mapped_column(String(16))
    scenario_label: Mapped[str | None] = mapped_column(String(32))
    scenario_prob: Mapped[Any] = mapped_column(Prob, nullable=True)
    scenario_group: Mapped[UUID | None] = mapped_column(Uuid)
    horizon_date: Mapped[date] = mapped_column(Date, nullable=False)
    confidence: Mapped[Any] = mapped_column(Prob, nullable=True)
    rationale_ref: Mapped[str | None] = mapped_column(String(256))
    status: Mapped[str] = mapped_column(String(16), nullable=False, server_default=text("'open'"))
    superseded_by_id: Mapped[UUID | None] = mapped_column(Uuid, ForeignKey("predictions.id"))
    superseded_run_id: Mapped[UUID | None] = mapped_column(Uuid, ForeignKey("runs.id"))
    scored_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    realized_price: Mapped[Any] = mapped_column(Price, nullable=True)
    realized_return: Mapped[Any] = mapped_column(Numeric(10, 6), nullable=True)
    error_pct: Mapped[Any] = mapped_column(Numeric(10, 6), nullable=True)
    outcome_notes: Mapped[str | None] = mapped_column(Text)
    idempotency_key: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    # §12 amendment (SPEC-TRACKREC §2.2): code-written at registration.
    direction: Mapped[str | None] = mapped_column(String(8))
    pinned_price: Mapped[Any] = mapped_column(Price, nullable=True)
    __table_args__ = (
        CheckConstraint(
            "kind IN ('target_price','entry_point','scenario','event_forecast','stance')",
            name="ck_predictions_kind",
        ),
        CheckConstraint(
            "status IN ('open','hit','miss','expired','superseded')",
            name="ck_predictions_status",
        ),
        CheckConstraint(
            "confidence IS NULL OR (confidence >= 0 AND confidence <= 1)",
            name="ck_predictions_confidence",
        ),
        CheckConstraint(
            "scenario_prob IS NULL OR (scenario_prob >= 0 AND scenario_prob <= 1)",
            name="ck_predictions_scenario_prob",
        ),
        CheckConstraint(
            "kind <> 'target_price' OR (value IS NOT NULL AND currency IS NOT NULL)",
            name="ck_predictions_target_price_requires_value",
        ),
        Index("ix_predictions_open_horizon", "status", "horizon_date"),
        Index("ix_predictions_coverage_house", "coverage_id", "house", "created_at"),
    )


class PredictionHit(Base):
    __tablename__ = "prediction_hits"
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    prediction_id: Mapped[UUID] = mapped_column(Uuid, ForeignKey("predictions.id"), nullable=False)
    hit_price: Mapped[Any] = mapped_column(Price, nullable=False)
    hit_on: Mapped[date] = mapped_column(Date, nullable=False)
    source: Mapped[str] = mapped_column(String(32), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    __table_args__ = (UniqueConstraint("prediction_id", "hit_on", name="uq_prediction_hits_day"),)


# ============================================================ corrections / disagreements


class Correction(Base):
    __tablename__ = "corrections"
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    run_id: Mapped[UUID] = mapped_column(Uuid, ForeignKey("runs.id"), nullable=False)
    stage_id: Mapped[UUID] = mapped_column(Uuid, ForeignKey("run_stages.id"), nullable=False)
    correcting_house: Mapped[str] = mapped_column(
        String(32), ForeignKey("houses.key", ondelete="RESTRICT"), nullable=False
    )
    attributed_stage_id: Mapped[UUID | None] = mapped_column(Uuid, ForeignKey("run_stages.id"))
    attributed_house: Mapped[str | None] = mapped_column(String(32))
    target: Mapped[str] = mapped_column(String(256), nullable=False)
    was: Mapped[str] = mapped_column(Text, nullable=False)
    now_value: Mapped[str] = mapped_column("now_value", Text, nullable=False)
    source: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    # §12 amendment (SPEC-INITIATION §6): idempotent per (stage, target, was, now).
    idempotency_key: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    __table_args__ = (Index("ix_corrections_attributed", "attributed_house", "created_at"),)


class Disagreement(Base):
    __tablename__ = "disagreements"
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    run_id: Mapped[UUID] = mapped_column(Uuid, ForeignKey("runs.id"), nullable=False)
    stage_id: Mapped[UUID] = mapped_column(Uuid, ForeignKey("run_stages.id"), nullable=False)
    house: Mapped[str] = mapped_column(
        String(32), ForeignKey("houses.key", ondelete="RESTRICT"), nullable=False
    )
    lead_house: Mapped[str] = mapped_column(
        String(32), ForeignKey("houses.key", ondelete="RESTRICT"), nullable=False
    )
    material: Mapped[bool] = mapped_column(Boolean, nullable=False)
    lead_position: Mapped[str] = mapped_column(Text, nullable=False)
    challenger_position: Mapped[str] = mapped_column(Text, nullable=False)
    key_numbers: Mapped[Any] = mapped_column(JsonType, nullable=True)
    resolves_if: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


# ============================================================ events


class Event(Base):
    __tablename__ = "events"
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    coverage_id: Mapped[UUID | None] = mapped_column(Uuid, ForeignKey("coverage.id"))
    kind: Mapped[str] = mapped_column(String(24), nullable=False)
    severity: Mapped[str] = mapped_column(String(16), nullable=False)
    title: Mapped[str] = mapped_column(String(256), nullable=False)
    detail: Mapped[str | None] = mapped_column(Text)
    payload: Mapped[Any] = mapped_column(JsonType, nullable=False)
    news_article_id: Mapped[UUID | None] = mapped_column(Uuid, ForeignKey("news_articles.id"))
    detected_by_run_id: Mapped[UUID | None] = mapped_column(Uuid, ForeignKey("runs.id"))
    handled_by_run_id: Mapped[UUID | None] = mapped_column(Uuid, ForeignKey("runs.id"))
    action: Mapped[str | None] = mapped_column(String(24))
    dedupe_key: Mapped[str] = mapped_column(String(128), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    # §12 amendment (SPEC-TRACKREC §8): PM 👍/👎 rating on event notes.
    pm_rating: Mapped[int | None] = mapped_column(SmallInteger)
    pm_rated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    pm_rated_by: Mapped[str | None] = mapped_column(String(64))
    __table_args__ = (
        UniqueConstraint("dedupe_key", name="uq_events_dedupe_key"),
        Index("ix_events_coverage_created", "coverage_id", "created_at"),
        Index("ix_events_unhandled", "action", "handled_by_run_id"),
    )


# ============================================================ dossier index


class DossierIndex(Base):
    __tablename__ = "dossier_index"
    coverage_id: Mapped[UUID] = mapped_column(Uuid, ForeignKey("coverage.id"), primary_key=True)
    slug: Mapped[str] = mapped_column(String(48), nullable=False, unique=True)
    main_commit: Mapped[str | None] = mapped_column(String(40))
    updated_by_run_id: Mapped[UUID | None] = mapped_column(Uuid, ForeignKey("runs.id"))
    stance: Mapped[str | None] = mapped_column(String(16))
    conviction: Mapped[str | None] = mapped_column(String(16))
    as_of: Mapped[date | None] = mapped_column(Date)
    target_price: Mapped[Any] = mapped_column(Price, nullable=True)
    tripwire_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    files: Mapped[Any] = mapped_column(JsonType, nullable=True)
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), onupdate=func.now()
    )


class DossierCommit(Base):
    __tablename__ = "dossier_commits"
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    coverage_id: Mapped[UUID] = mapped_column(Uuid, ForeignKey("coverage.id"), nullable=False)
    run_id: Mapped[UUID | None] = mapped_column(Uuid, ForeignKey("runs.id"))
    stage_id: Mapped[UUID | None] = mapped_column(Uuid, ForeignKey("run_stages.id"))
    commit_sha: Mapped[str] = mapped_column(String(40), nullable=False)
    branch: Mapped[str] = mapped_column(String(64), nullable=False)
    parent_sha: Mapped[str | None] = mapped_column(String(40))
    message: Mapped[str] = mapped_column(Text, nullable=False)
    files_changed: Mapped[Any] = mapped_column(JsonType, nullable=True)
    merged_to_main: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    __table_args__ = (UniqueConstraint("commit_sha", name="uq_dossier_commits_sha"),)


# ============================================================ doctrine / artifacts / cost


class DoctrineVersion(Base):
    __tablename__ = "doctrine_versions"
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    commit_sha: Mapped[str] = mapped_column(String(40), nullable=False, unique=True)
    label: Mapped[str] = mapped_column(String(32), nullable=False)
    approved_by: Mapped[str | None] = mapped_column(String(64))
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    distillation_run_id: Mapped[UUID | None] = mapped_column(Uuid, ForeignKey("runs.id"))
    notes: Mapped[str | None] = mapped_column(Text)
    is_current: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    __table_args__ = (
        Index(
            "ux_doctrine_current",
            "is_current",
            unique=True,
            postgresql_where=text("is_current"),
            sqlite_where=text("is_current"),
        ),
    )


class Artifact(Base):
    __tablename__ = "artifacts"
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    kind: Mapped[str] = mapped_column(String(24), nullable=False)
    media_type: Mapped[str | None] = mapped_column(String(64))
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    path: Mapped[str] = mapped_column(String(512), nullable=False)
    run_id: Mapped[UUID | None] = mapped_column(Uuid, ForeignKey("runs.id"))
    stage_id: Mapped[UUID | None] = mapped_column(Uuid, ForeignKey("run_stages.id"))
    coverage_id: Mapped[UUID | None] = mapped_column(Uuid, ForeignKey("coverage.id"))
    filename: Mapped[str | None] = mapped_column(String(256))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    __table_args__ = (
        UniqueConstraint("sha256", "kind", name="uq_artifacts_sha_kind"),
        Index("ix_artifacts_run", "run_id"),
    )


class LlmUsage(Base):
    __tablename__ = "llm_usage"
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    run_id: Mapped[UUID | None] = mapped_column(Uuid, ForeignKey("runs.id"))
    stage_id: Mapped[UUID | None] = mapped_column(Uuid, ForeignKey("run_stages.id"))
    attempt_id: Mapped[UUID | None] = mapped_column(Uuid, ForeignKey("stage_attempts.id"))
    house: Mapped[str | None] = mapped_column(String(32), ForeignKey("houses.key"))
    coverage_id: Mapped[UUID | None] = mapped_column(Uuid, ForeignKey("coverage.id"))
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    model: Mapped[str] = mapped_column(String(64), nullable=False)
    substrate: Mapped[str | None] = mapped_column(String(8))
    purpose: Mapped[str] = mapped_column(String(32), nullable=False)
    input_tokens: Mapped[int | None] = mapped_column(Integer)
    output_tokens: Mapped[int | None] = mapped_column(Integer)
    cached_input_tokens: Mapped[int | None] = mapped_column(Integer)
    cost_usd: Mapped[Any] = mapped_column(Cost, nullable=True)
    cost_source: Mapped[str] = mapped_column(String(16), nullable=False)
    request_id: Mapped[str | None] = mapped_column(String(64))
    reconciled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reconciled_delta_usd: Mapped[Any] = mapped_column(Cost, nullable=True)
    error: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    __table_args__ = (
        Index("ix_llm_usage_house_completed", "house", "completed_at"),
        Index("ix_llm_usage_run", "run_id"),
    )


class HouseBudgetDay(Base):
    __tablename__ = "house_budget_days"
    house: Mapped[str] = mapped_column(
        String(32), ForeignKey("houses.key", ondelete="RESTRICT"), primary_key=True
    )
    day: Mapped[date] = mapped_column(Date, primary_key=True)
    reserved_usd: Mapped[Any] = mapped_column(Cost, nullable=False, server_default=text("0"))
    spent_usd: Mapped[Any] = mapped_column(Cost, nullable=False, server_default=text("0"))
    cap_usd: Mapped[Any] = mapped_column(Cost, nullable=True)
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), onupdate=func.now()
    )


class HouseMetricSnapshot(Base):
    # §12 amendment (SPEC-TRACKREC §3): per-house metric views.
    __tablename__ = "house_metric_snapshots"
    house: Mapped[str] = mapped_column(
        String(32), ForeignKey("houses.key", ondelete="RESTRICT"), primary_key=True
    )
    as_of: Mapped[date] = mapped_column(Date, primary_key=True)
    scope: Mapped[str] = mapped_column(String(32), primary_key=True)
    metrics: Mapped[Any] = mapped_column(JsonType, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    __table_args__ = (
        UniqueConstraint("house", "as_of", "scope", name="uq_house_metric_snapshots"),
    )


# ============================================================ outbox / audit / idempotency


class OutboxEvent(Base):
    __tablename__ = "outbox_events"
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    kind: Mapped[str] = mapped_column(String(48), nullable=False)
    coverage_id: Mapped[UUID | None] = mapped_column(Uuid, ForeignKey("coverage.id"))
    run_id: Mapped[UUID | None] = mapped_column(Uuid, ForeignKey("runs.id"))
    payload: Mapped[Any] = mapped_column(JsonType, nullable=False)
    dedupe_key: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)
    available_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    claimed_by: Mapped[str | None] = mapped_column(String(64))
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    __table_args__ = (Index("ix_outbox_pending", "delivered_at", "available_at"),)


class AuditLog(Base):
    __tablename__ = "audit_log"
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    actor_type: Mapped[str] = mapped_column(String(16), nullable=False)
    actor_id: Mapped[str] = mapped_column(String(64), nullable=False)
    target: Mapped[str | None] = mapped_column(String(256))
    before_state: Mapped[Any] = mapped_column(JsonType, nullable=True)
    after_state: Mapped[Any] = mapped_column(JsonType, nullable=True)
    request_id: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    __table_args__ = (Index("ix_audit_log_created", "created_at"),)


class IdempotencyKey(Base):
    __tablename__ = "idempotency_keys"
    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    route: Mapped[str] = mapped_column(String(64), nullable=False)
    actor_id: Mapped[str] = mapped_column(String(64), nullable=False)
    response: Mapped[Any] = mapped_column(JsonType, nullable=True)
    status_code: Mapped[int | None] = mapped_column(Integer)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class SchedulerJobLog(Base):
    __tablename__ = "scheduler_job_logs"
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    job_name: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    scheduled_for: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    detail: Mapped[str | None] = mapped_column(Text)
    runs_created: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    __table_args__ = (Index("ix_scheduler_job_logs_job_created", "job_name", "created_at"),)


# ============================================================ news


class NewsArticle(Base):
    __tablename__ = "news_articles"
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    url: Mapped[str | None] = mapped_column(String(2048), unique=True)
    normalised_url: Mapped[str | None] = mapped_column(String(2048))
    content_hash: Mapped[str | None] = mapped_column(String(64))
    cluster_id: Mapped[UUID | None] = mapped_column(Uuid)
    title: Mapped[str | None] = mapped_column(String(512))
    source: Mapped[str | None] = mapped_column(String(64))
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    full_text: Mapped[str | None] = mapped_column(Text)
    summary: Mapped[str | None] = mapped_column(Text)
    tickers: Mapped[Any] = mapped_column(JsonType, nullable=True)
    scored_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    scores: Mapped[Any] = mapped_column(JsonType, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    __table_args__ = (
        Index("ix_news_normalised_url", "normalised_url"),
        Index("ix_news_content_hash", "content_hash"),
        Index("ix_news_published", "published_at"),
    )


# ============================================================ Mattermost registry (ADAPTER §3.2)


class MmChannel(Base, TimestampMixin):
    __tablename__ = "mm_channels"
    coverage_id: Mapped[UUID] = mapped_column(Uuid, ForeignKey("coverage.id"), primary_key=True)
    team_id: Mapped[str] = mapped_column(String(64), nullable=False)
    channel_id: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    channel_name: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class MmPost(Base):
    __tablename__ = "mm_posts"
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    ref_type: Mapped[str] = mapped_column(String(24), nullable=False)
    ref_id: Mapped[str] = mapped_column(String(64), nullable=False)
    channel_id: Mapped[str] = mapped_column(String(64), nullable=False)
    post_id: Mapped[str] = mapped_column(String(64), nullable=False)
    root_id: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    __table_args__ = (UniqueConstraint("ref_type", "ref_id", name="uq_mm_posts_ref"),)


# ============================================================ ported deterministic tables (§4.25)


class Position(Base):
    __tablename__ = "positions"
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    ticker: Mapped[str] = mapped_column(String(16), nullable=False)
    net_quantity: Mapped[Any] = mapped_column(
        Numeric(20, 6), nullable=False, server_default=text("0")
    )
    avg_cost: Mapped[Any] = mapped_column(Price, nullable=True)
    book_id: Mapped[str] = mapped_column(String(32), nullable=False)
    currency: Mapped[str] = mapped_column(String(8), nullable=False, server_default=text("'USD'"))
    last_trade_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), onupdate=func.now()
    )
    __table_args__ = (UniqueConstraint("ticker", "book_id", name="uq_positions_ticker_book"),)


class Trade(Base):
    __tablename__ = "trades"
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    source_ref: Mapped[str | None] = mapped_column(String(64), unique=True)  # was post_id
    raw_text: Mapped[str] = mapped_column(Text, nullable=False)
    ticker: Mapped[str] = mapped_column(String(16), nullable=False)
    direction: Mapped[str] = mapped_column(String(4), nullable=False)
    quantity: Mapped[Any] = mapped_column(Numeric(20, 6), nullable=False)
    price: Mapped[Any] = mapped_column(Price, nullable=False)
    currency: Mapped[str] = mapped_column(String(8), nullable=False, server_default=text("'USD'"))
    notes: Mapped[str | None] = mapped_column(Text)
    parse_confidence: Mapped[Any] = mapped_column(Prob, nullable=True)
    book_id: Mapped[str] = mapped_column(String(32), nullable=False)
    posted_by_user_id: Mapped[str] = mapped_column(String(64), nullable=False)
    posted_by_username: Mapped[str | None] = mapped_column(String(128))
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    traded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    __table_args__ = (
        CheckConstraint("direction IN ('buy','sell')", name="ck_trades_direction"),
        Index("ix_trades_traded_at", "traded_at"),
        Index("ix_trades_book_traded_at", "book_id", "traded_at"),
    )


class PendingTradeConfirmation(Base):
    __tablename__ = "pending_trade_confirmations"
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    source_ref: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    confirmation_post_id: Mapped[str | None] = mapped_column(String(64))
    user_id: Mapped[str] = mapped_column(String(64), nullable=False)
    username: Mapped[str | None] = mapped_column(String(128))
    channel_id: Mapped[str | None] = mapped_column(String(128))
    trade_json: Mapped[Any] = mapped_column(JsonType, nullable=False)
    raw_text: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default=text("'pending'")
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    resolved_by_user_id: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), onupdate=func.now()
    )
    __table_args__ = (Index("ix_trade_confirmations_status_expires", "status", "expires_at"),)


class BookCash(Base):
    __tablename__ = "book_cash"
    book_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    cash_usd: Mapped[Any] = mapped_column(Money, nullable=False, server_default=text("0"))
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), onupdate=func.now()
    )


class CashEvent(Base):
    __tablename__ = "cash_events"
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    book_id: Mapped[str] = mapped_column(String(32), nullable=False)
    amount_usd: Mapped[Any] = mapped_column(Money, nullable=False)
    event_type: Mapped[str] = mapped_column(String(32), nullable=False)
    notes: Mapped[str | None] = mapped_column(Text)
    posted_by_user_id: Mapped[str] = mapped_column(String(64), nullable=False)
    posted_by_username: Mapped[str | None] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class RiskConfig(Base):
    __tablename__ = "risk_configs"
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    book: Mapped[str] = mapped_column(String(32), unique=True, nullable=False)
    max_position_pct: Mapped[Any] = mapped_column(Prob, nullable=True)
    stop_loss_pct: Mapped[Any] = mapped_column(Prob, nullable=True)
    var_confidence: Mapped[Any] = mapped_column(Prob, nullable=True)
    var_limit_pct: Mapped[Any] = mapped_column(Prob, nullable=True)
    rebalance_threshold_pct: Mapped[Any] = mapped_column(Prob, nullable=True)
    max_sector_pct: Mapped[Any] = mapped_column(Prob, nullable=True)
    sizing_method: Mapped[str | None] = mapped_column(String(32))
    kelly_fraction: Mapped[Any] = mapped_column(Prob, nullable=True)
    max_positions: Mapped[int | None] = mapped_column(Integer)
    historical_lookback_days: Mapped[int | None] = mapped_column(Integer)
    parametric_window_days: Mapped[int | None] = mapped_column(Integer)
    parametric_ewma_lambda: Mapped[Any] = mapped_column(Prob, nullable=True)
    mc_covariance_window_days: Mapped[int | None] = mapped_column(Integer)
    mc_distribution: Mapped[str | None] = mapped_column(String(16))
    mc_t_dof: Mapped[int | None] = mapped_column(Integer)
    target_weights: Mapped[Any] = mapped_column(JsonType, nullable=True)
    scenario_shocks: Mapped[Any] = mapped_column(JsonType, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), onupdate=func.now()
    )


class RiskEvent(Base):
    __tablename__ = "risk_events"
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    book_id: Mapped[str] = mapped_column(String(32), nullable=False)
    ticker: Mapped[str | None] = mapped_column(String(16))
    event_type: Mapped[str] = mapped_column(String(32), nullable=False)
    severity: Mapped[str] = mapped_column(String(16), nullable=False)
    detail: Mapped[str | None] = mapped_column(Text)
    instruction: Mapped[Any] = mapped_column(JsonType, nullable=True)
    snapshot_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    acknowledged: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    acknowledged_by_user_id: Mapped[str | None] = mapped_column(String(64))
    acknowledged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    acknowledged_via_post_id: Mapped[str | None] = mapped_column(String(64))
    post_id: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    __table_args__ = (
        UniqueConstraint(
            "book_id", "ticker", "event_type", "snapshot_at", name="uq_risk_event_snapshot"
        ),
        Index("ix_risk_events_created_at", "created_at"),
        Index("ix_risk_events_book_severity_ack", "book_id", "severity", "acknowledged"),
    )


class PortfolioSnapshot(Base):
    __tablename__ = "portfolio_snapshots"
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    book: Mapped[str] = mapped_column(String(32), nullable=False)
    total_nav_usd: Mapped[Any] = mapped_column(Money, nullable=True)
    positions_json: Mapped[Any] = mapped_column(JsonType, nullable=True)
    var_results: Mapped[Any] = mapped_column(JsonType, nullable=True)
    portfolio_beta: Mapped[Any] = mapped_column(Prob, nullable=True)
    benchmark_correlation: Mapped[Any] = mapped_column(Prob, nullable=True)
    herfindahl_index: Mapped[Any] = mapped_column(Prob, nullable=True)
    top1_weight: Mapped[Any] = mapped_column(Prob, nullable=True)
    top5_weight: Mapped[Any] = mapped_column(Prob, nullable=True)
    limit_breaches: Mapped[Any] = mapped_column(JsonType, nullable=True)
    sizing_recommendations: Mapped[Any] = mapped_column(JsonType, nullable=True)
    snapshot_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    __table_args__ = (Index("ix_portfolio_book_snapshot", "book", "snapshot_at"),)
