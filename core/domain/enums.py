"""Domain enums — the single source of truth for DB CHECK constraint values.

SPEC-DOMAIN §3 (amended by §12: ``StageRole.PM_QUERY`` from SPEC-CORE). Stored as
``String(n)`` + ``CHECK`` in Postgres (never native ENUM types), so adding a value is an
ordinary migration. Python-side these are ``StrEnum`` so member == value compares cleanly
against stored strings.
"""

from __future__ import annotations

from enum import StrEnum


class CoverageState(StrEnum):
    PROPOSED = "proposed"
    INITIATING = "initiating"
    DECISION_PENDING = "decision_pending"
    ACTIVE = "active"
    WATCH = "watch"
    REJECTED = "rejected"
    EXITED = "exited"
    FAILED = "failed"


class RunType(StrEnum):
    INITIATION = "initiation"
    DEEP_REVIEW = "deep_review"
    EVENT_ANALYSIS = "event_analysis"
    MONITOR_TICK = "monitor_tick"
    PM_QUERY = "pm_query"
    LEAD_REVIEW = "lead_review"
    DISTILLATION = "distillation"


class RunStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    WAITING_PM = "waiting_pm"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


class StageStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    SKIPPED = "skipped"


class Substrate(StrEnum):
    HARNESS = "harness"
    API = "api"


class StageRole(StrEnum):
    AUTHOR = "author"
    VERIFIER = "verifier"
    DATA_CHECKER = "data_checker"
    FINALIZER = "finalizer"
    MONITOR = "monitor"
    CROSS_CHECK = "cross_check"
    LEAD_REVIEW = "lead_review"
    DISTILLER = "distiller"
    PM_QUERY = "pm_query"  # §12 amendment (SPEC-CORE §3.2/§10)


class PredictionKind(StrEnum):
    TARGET_PRICE = "target_price"
    ENTRY_POINT = "entry_point"
    SCENARIO = "scenario"
    EVENT_FORECAST = "event_forecast"
    STANCE = "stance"


class PredictionStatus(StrEnum):
    OPEN = "open"
    HIT = "hit"
    MISS = "miss"
    EXPIRED = "expired"
    SUPERSEDED = "superseded"


class EventKind(StrEnum):
    NEWS = "news"
    FILING = "filing"
    EARNINGS = "earnings"
    PRICE_LEVEL = "price_level"
    TRIPWIRE = "tripwire"
    PM_INSTRUCTION = "pm_instruction"
    RISK = "risk"


class Severity(StrEnum):
    # §3: the single severity vocabulary. SPEC-MONITORING maps the monitor role's
    # materiality (high|medium|low|none) onto it; the DB never stores materiality.
    THESIS = "thesis"
    VALUATION = "valuation"
    INFO = "info"


class GateKind(StrEnum):
    INITIATION_DECISION = "initiation_decision"
    LEAD_CHANGE = "lead_change"
    DOCTRINE_AMENDMENT = "doctrine_amendment"
    BUDGET_CAP = "budget_cap"


class ActorType(StrEnum):
    PM = "pm"
    RUN = "run"
    SCHEDULER = "scheduler"
    SYSTEM = "system"
