# SPEC-DOMAIN — Domain schema, coverage state machine, run queue

**Phase:** 1.1 (SPEC) · **Implements:** PROMPTS.md 1.1 · **Branch:** `spec-domain`
**Status:** ready for BUILD
**Depends on:** nothing (this is the base layer)
**Consumed by:** SPEC-CORE (orchestrator/API), SPEC-RUNNER (queue + stage rows),
SPEC-INITIATION, SPEC-MONITORING, SPEC-ADAPTER, SPEC-TRACKREC, SPEC-DISTILLATION

This spec is self-contained. A builder implements it without reading `design/`.

---

## 1. Purpose and scope

Define the persistence layer of ai-fund v3: SQLAlchemy 2.0 models, the alembic
baseline migration, the **coverage state machine** (every transition with actor,
cause, guard, side effects, audit), the **Postgres-backed run queue** (claiming with
`SELECT … FOR UPDATE SKIP LOCKED`, leases, reaper), the **dossier-repo index**, the
**predictions ledger**, and the **transactional outbox** the adapter consumes.

The fund is a long-only research system: a human PM decides the portfolio, AI analyst
"houses" (model providers) research equities. Each covered equity has exactly one
accountable **lead house**; other houses contribute verification passes. All agent
work executes as durable, typed **runs** composed of **stages**. Per-ticker knowledge
lives in a git-versioned **dossier**; Postgres holds the index and every fact the
system reasons about deterministically.

### In scope

- `core/db/` — models, session factory, queue functions, alembic baseline.
- `core/domain/` — enums, the coverage state machine, the run/stage state machine,
  transition guards. Pure functions plus a repository layer; no HTTP, no LLM calls.

### Out of scope (owned by later specs)

- Orchestrator, scheduler, API, CLI, config loading (SPEC-CORE, phase 1.2).
- Stage execution, sandboxes, harness drivers (SPEC-RUNNER, phase 2.1).
- Metric views and scoring math (SPEC-TRACKREC, phase 6.1) — this spec provides the
  base tables and the immutability rules those views depend on.
- News fetching/dedup algorithm and tripwire scoring (SPEC-MONITORING, phase 4.1) —
  this spec provides `news_articles` and `events`.

### Non-goals

- No LLM calls anywhere in this layer. Nothing in `core/db` or `core/domain` imports a
  provider SDK.
- No business policy constants (budgets, cadences, thresholds, model names, prices)
  in code — those live in operator config (`/etc/ai-fund/*.yaml`, SPEC-CORE §config).
  This layer stores *values*, never *defaults*.

---

## 2. Conventions (binding on every table)

| Concern | Decision |
|---|---|
| SQLAlchemy | 2.0 declarative, `Mapped[...]` + `mapped_column`, one `Base` in `core/db/base.py` |
| Primary keys | `sqlalchemy.Uuid` (native `uuid` on Postgres, `CHAR(32)` elsewhere), default `uuid4`. Exceptions: natural-key tables (`houses.key`, `book_cash.book_id`, `idempotency_keys.key`) |
| Time | `DateTime(timezone=True)` (`timestamptz`) everywhere, UTC only. `created_at` uses `server_default=func.now()`; app code never writes naive datetimes |
| Money / prices | `Numeric` — never `float`. Prices/values `Numeric(20, 6)`; cash `Numeric(20, 2)`; costs `Numeric(12, 6)`; probabilities/confidence `Numeric(5, 4)` in `[0,1]`. (v2 used `Float` for prices; v3 does not — principle: the money path is deterministic. Phase 7.3 casts on import.) |
| Enums | `String(n)` + `CHECK` constraint + a Python `StrEnum` in `core/domain/enums.py`. **Not** Postgres `ENUM` types (alembic ALTER TYPE pain; also keeps SQLite unit tests working) |
| JSON | `JSONB().with_variant(JSON(), "sqlite")` as `JsonType` |
| Booleans | `nullable=False` with explicit `server_default` |
| Deletes | Nothing in the research/audit path is ever hard-deleted. Retention jobs may delete `news_articles` and expired `idempotency_keys` only |
| FKs | `ondelete="RESTRICT"` by default; `CASCADE` only from a run to its own children (`run_stages`, `stage_attempts`) |
| Naming | tables plural snake_case; indexes `ix_<table>_<cols>`; unique `uq_…`; check `ck_…` |

`core/db/types.py` exports `JsonType`, `Money = Numeric(20, 2)`, `Price = Numeric(20, 6)`,
`Cost = Numeric(12, 6)`, `Prob = Numeric(5, 4)`, and `utc_now()`.

### Module layout

```
core/db/__init__.py
core/db/base.py           # Base, naming convention, TimestampMixin
core/db/types.py          # JsonType, Money/Price/Cost/Prob, utc_now
core/db/models.py         # every table below (single module; ~1200 lines)
core/db/session.py        # engine + sessionmaker factory from settings
core/db/queue.py          # claim_stage / heartbeat / release / reap; outbox consume
core/db/repo/*.py         # thin repositories: coverage, runs, predictions, events, dossier
core/domain/enums.py      # StrEnums (single source of truth for the CHECK values)
core/domain/coverage_sm.py# coverage state machine (pure)
core/domain/run_sm.py     # run + stage state machines (pure)
core/db/migrations/       # alembic env.py + versions/0001_baseline.py
```

---

## 3. Enums (`core/domain/enums.py`)

```python
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

class StageRole(StrEnum):          # 1:1 with doctrine/roles/<role>.md
    AUTHOR = "author"
    VERIFIER = "verifier"
    DATA_CHECKER = "data_checker"
    FINALIZER = "finalizer"
    MONITOR = "monitor"
    CROSS_CHECK = "cross_check"
    LEAD_REVIEW = "lead_review"
    DISTILLER = "distiller"

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
    PRICE_LEVEL = "price_level"      # coverage_levels crossing (PM-set)
    TRIPWIRE = "tripwire"            # tripwires.yaml condition (agent-authored)
    PM_INSTRUCTION = "pm_instruction"
    RISK = "risk"

class Severity(StrEnum):             # tripwire severity + event severity, one vocabulary
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
```

`Severity` is the **single** severity vocabulary. SPEC-MONITORING maps the monitor
role's `materiality: high|medium|low|none` output onto it; the DB never stores
`materiality` as a state.

---

## 4. Tables

Presented as SQLAlchemy; every constraint listed is required. `TimestampMixin` adds
`created_at` (server default `now()`) and `updated_at` (nullable, `onupdate=utc_now`).

### 4.1 `houses` — model-house registry anchor

Operator config (`/etc/ai-fund/houses.yaml`, SPEC-CORE) is the **source of truth** for
provider, models, harness, and budgets. This table exists only so other tables can FK
to a stable house identity and so track records survive config edits. Core upserts it
from `houses.yaml` at startup (`checkconfig`/bootstrap): insert new keys, update the
mirrored columns, and set `enabled=false` for keys no longer in config — **never
delete** (track-record rows reference them).

```python
class House(Base):
    __tablename__ = "houses"
    key: Mapped[str] = mapped_column(String(32), primary_key=True)   # "gpt", "gemini", …
    display_name: Mapped[str] = mapped_column(String(64), nullable=False)
    provider: Mapped[str] = mapped_column(String(32), nullable=False)  # openai|google|…
    assignable: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    meta: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    harness_type: Mapped[str | None] = mapped_column(String(32))      # mirror, informational
    config_digest: Mapped[str | None] = mapped_column(String(64))     # sha256 of the yaml block
    created_at, updated_at
    __table_args__ = (
        CheckConstraint("NOT (assignable AND meta)", name="ck_houses_meta_not_assignable"),
    )
```

- **No model names or prices in this table.** `heavy_model` / `light_model` / prices
  live in `houses.yaml` + `pricing.yaml` and are resolved at run time. Which model a
  stage actually used is recorded *after the fact* on `stage_attempts.model` and
  `llm_usage.model` (observed values, not defaults).
- `meta=true, assignable=false` is Claude's row: meta runs may never be assigned as
  lead or contributor, and may never write dossiers or predictions (enforced in
  SPEC-DISTILLATION and by the FK-level guard in §6.4).

### 4.2 `coverage` — one row per equity the fund pays attention to

```python
class Coverage(Base):
    __tablename__ = "coverage"
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    ticker: Mapped[str] = mapped_column(String(16), nullable=False)     # exchange-local symbol
    exchange: Mapped[str] = mapped_column(String(16), nullable=False)   # MIC-ish code, e.g. "tse"
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    currency: Mapped[str] = mapped_column(String(8), nullable=False)    # ISO-4217, trading ccy
    isin: Mapped[str | None] = mapped_column(String(12))
    state: Mapped[str] = mapped_column(String(24), nullable=False)      # CoverageState
    lead_house: Mapped[str | None] = mapped_column(ForeignKey("houses.key", ondelete="RESTRICT"))
    dossier_slug: Mapped[str] = mapped_column(String(48), nullable=False)  # "tse_2267"
    earnings_calendar_ref: Mapped[str | None] = mapped_column(String(64))
    exchange_session_ref: Mapped[str | None] = mapped_column(String(32))   # session profile key
    pm_notes: Mapped[str | None] = mapped_column(Text)
    proposed_by: Mapped[str | None] = mapped_column(String(64))         # PM user id
    decided_at: Mapped[datetime | None]
    exited_at: Mapped[datetime | None]
    created_at, updated_at
    __table_args__ = (
        UniqueConstraint("exchange", "ticker", name="uq_coverage_exchange_ticker"),
        UniqueConstraint("dossier_slug", name="uq_coverage_dossier_slug"),
        CheckConstraint("state IN (…8 values…)", name="ck_coverage_state"),
        CheckConstraint(
            "(state IN ('active','watch','decision_pending')) = (lead_house IS NOT NULL)"
            " OR state NOT IN ('active','watch','decision_pending')",
            name="ck_coverage_lead_required_when_live",
        ),
        Index("ix_coverage_state", "state"),
    )
```

- **There is no `tier` column.** `active` and `watch` *are* the tiers (they imply the
  monitoring cadence). `exited`/`rejected` have no tier and no monitoring.
- `dossier_slug` = `f"{exchange}_{ticker}".lower()` with non-alphanumerics collapsed to
  `_`. Computed **once**, at coverage creation, by `core/domain/dossier_slug.py`, and
  stored. It is this module's key (checklist item 4): the dossier repo, the workspace
  path, and the Mattermost channel name are all derived from it, and no other module
  may re-derive it from `(exchange, ticker)` — they read the column.
- A re-proposed ticker (from `exited`/`rejected`) reuses the same row and slug, so the
  old dossier directory (and its git history) becomes the new run's context.

### 4.3 `coverage_contributors`

```python
class CoverageContributor(Base):
    __tablename__ = "coverage_contributors"
    coverage_id: FK coverage.id, ondelete="CASCADE", pk
    house: FK houses.key, pk
    added_at, added_by (String(64))
```

Resolution rule (implemented in `core/domain/contributors.py`, used by SPEC-INITIATION
and SPEC-MONITORING): if a coverage has **no** rows here, contributors = *all houses
where `enabled AND assignable AND key != lead_house`*, evaluated at run-creation time
and **materialized into the run's stage rows**. If it has rows, they are the
contributor set (PM override). Materializing at run creation means a later config
change cannot retroactively alter a completed run's attribution.

### 4.4 `coverage_levels` — PM-facing price levels (deterministic monitoring)

```python
class CoverageLevel(Base):
    __tablename__ = "coverage_levels"
    id, coverage_id (FK)
    kind: String(16)          # entry | target | stop | review
    value: Price NOT NULL
    currency: String(8) NOT NULL
    direction: String(8)      # below | above  (crossing direction that fires)
    set_by: String(16)        # pm | run
    set_by_run_id: FK runs.id nullable
    active: Boolean NOT NULL default true
    superseded_at: datetime | None
    created_at
    __table_args__ = (
        CheckConstraint("kind IN ('entry','target','stop','review')"),
        CheckConstraint("direction IN ('below','above')"),
        CheckConstraint("value > 0"),
        Index("ix_coverage_levels_active", "coverage_id", "active"),
    )
```

Levels are **PM/pipeline-set numeric lines** (design/02 `levels:` field) and are
evaluated in pure code by the monitor. They are distinct from `tripwires.yaml`
(agent-authored, thesis-derived, lives in the dossier, may be a news/filing condition).
Both are monitored (SPEC-MONITORING) but they emit different `events.kind`
(`price_level` vs `tripwire`), so a PM entry line and a thesis tripwire that happen to
name the same price do not double-notify.

Levels are append-only: a change inserts a new row and sets `active=false`,
`superseded_at=now()` on the old one.

### 4.5 `coverage_transitions` — state-machine audit (queryable history)

```python
class CoverageTransition(Base):
    __tablename__ = "coverage_transitions"
    id, coverage_id (FK)
    from_state: String(24) | None    # None on creation
    to_state: String(24) NOT NULL
    actor_type: String(16)           # ActorType
    actor_id: String(64) | None      # PM user id, run id, job name
    cause: String(64) NOT NULL       # machine-readable, e.g. "pm_decision", "run_failed"
    run_id: FK runs.id | None
    notes: Text | None
    created_at
    Index("ix_coverage_transitions_coverage_created", "coverage_id", "created_at")
```

Written in the **same transaction** as the `coverage.state` UPDATE, together with an
`audit_log` row and (for PM-visible transitions) an `outbox_events` row. A state change
committed without its transition row is a bug the tests must catch (§9).

### 4.6 `coverage_lead_history`

```python
class CoverageLeadHistory(Base):
    __tablename__ = "coverage_lead_history"
    id, coverage_id (FK)
    from_house: FK houses.key | None
    to_house: FK houses.key NOT NULL
    run_id: FK runs.id | None        # the lead_review run that proposed it, if any
    approved_by: String(64) NOT NULL # PM user id — a lead change is ALWAYS PM-approved
    rationale: Text | None
    created_at
```

Open predictions stay attributed to the house that made them (`predictions.house` is
never rewritten by a handover — see §6.4).

### 4.7 `runs`

```python
class Run(Base):
    __tablename__ = "runs"
    id: Uuid pk
    coverage_id: FK coverage.id | None        # NULL for meta runs (distillation)
    type: String(24) NOT NULL                 # RunType
    status: String(16) NOT NULL               # RunStatus
    trigger: String(32) NOT NULL              # pm | scheduler | tripwire | event | track_record
    trigger_ref: String(128) | None           # event id, job name, PM post id
    priority: Integer NOT NULL server_default '100'   # higher runs first
    mutates_dossier: Boolean NOT NULL          # true for all types except monitor_tick/pm_query
    requested_by: String(64) | None            # PM user id when trigger='pm'
    params: JsonType                           # run-type-specific (e.g. {"question": "...", "verify_count": 2})
    doctrine_version_id: FK doctrine_versions.id | None   # pinned at run start
    budget_cap_usd: Cost | None                # resolved from config at creation; NULL = no cap
    cost_usd: Cost NOT NULL server_default '0' # rollup of stage_attempts
    summary: Text | None
    pm_decision: String(32) | None             # denormalized answer of the terminal gate
    dossier_commit_before: String(40) | None
    dossier_commit_after: String(40) | None
    error: Text | None                         # terminal failure reason
    created_at, started_at, finished_at, updated_at
    __table_args__ = (
        CheckConstraint("type IN (…7…)"), CheckConstraint("status IN (…6…)"),
        CheckConstraint("(coverage_id IS NOT NULL) OR type = 'distillation'",
                        name="ck_runs_coverage_required"),
        Index("ix_runs_status_priority", "status", "priority", "created_at"),
        Index("ix_runs_coverage_created", "coverage_id", "created_at"),
    )
```

`params` never carries model names or budgets *chosen by code*; it carries **resolved**
values read from config at creation (so a config edit mid-run cannot change the run's
contract), and the resolution is recorded — SPEC-CORE §config.

### 4.8 `run_stages` — the queue

```python
class RunStage(Base):
    __tablename__ = "run_stages"
    id: Uuid pk
    run_id: FK runs.id ondelete="CASCADE"
    seq: Integer NOT NULL                      # 1-based order within the run
    role: String(24) NOT NULL                  # StageRole
    house: FK houses.key NOT NULL
    substrate: String(8) NOT NULL              # harness | api
    status: String(16) NOT NULL                # StageStatus
    depends_on_seq: Integer | None             # NULL = ready when run starts
    # --- queue mechanics ---
    available_at: datetime NOT NULL server_default now()   # backoff / scheduling
    claimed_by: String(64) | None              # runner worker id
    claimed_at: datetime | None
    lease_expires_at: datetime | None
    attempts: Integer NOT NULL server_default '0'
    max_attempts: Integer NOT NULL             # from config at creation
    # --- results ---
    workspace_ref: String(256) | None          # worktree path / branch
    transcript_ref: String(256) | None         # artifact id of the winning attempt
    artifacts_ref: JsonType | None             # {kind: artifact_id}
    result: JsonType | None                    # validated stage_result.yaml (winning attempt)
    cost_usd: Cost NOT NULL server_default '0' # sum over attempts (incl. failed ones)
    tokens_in, tokens_out: Integer | None
    wall_time_s: Integer | None
    error: Text | None
    created_at, started_at, finished_at, updated_at
    __table_args__ = (
        UniqueConstraint("run_id", "seq", name="uq_run_stages_run_seq"),
        CheckConstraint("status IN (…5…)"), CheckConstraint("substrate IN ('harness','api')"),
        Index("ix_run_stages_claimable", "status", "available_at", "id"),   # queue scan
        Index("ix_run_stages_lease", "status", "lease_expires_at"),         # reaper scan
    )
```

### 4.9 `stage_attempts` — one row per execution attempt (cost is per attempt)

A failed attempt still spends real money. Cost, transcript, and the validation errors
that caused a retry are therefore per attempt, not per stage.

```python
class StageAttempt(Base):
    __tablename__ = "stage_attempts"
    id: Uuid pk
    stage_id: FK run_stages.id ondelete="CASCADE"
    attempt_no: Integer NOT NULL               # 1-based
    status: String(16) NOT NULL                # running | succeeded | failed | killed | timeout
    house: FK houses.key NOT NULL              # copied from stage (attribution survives config edits)
    model: String(64) | None                   # OBSERVED model id, resolved from config at launch
    substrate: String(8) NOT NULL
    worker_id: String(64) | None
    transcript_ref: String(256) | None          # artifact id
    result: JsonType | None                     # raw parsed stage_result.yaml (pre-validation)
    validation_errors: JsonType | None          # list of gate failures (drives the retry prompt)
    cost_usd: Cost NOT NULL server_default '0'
    cost_source: String(16) | None              # provider_api | transcript | estimated
    tokens_in, tokens_out, cached_tokens_in: Integer | None
    exit_code: Integer | None
    kill_reason: String(32) | None              # wall_clock | budget | operator | lease_expired
    started_at, finished_at
    created_at
    __table_args__ = (
        UniqueConstraint("stage_id", "attempt_no", name="uq_stage_attempts_stage_no"),
        CheckConstraint("status IN ('running','succeeded','failed','killed','timeout')"),
    )
```

`run_stages.cost_usd = SUM(stage_attempts.cost_usd)` and
`runs.cost_usd = SUM(run_stages.cost_usd)`; both rollups are maintained by the
orchestrator in the transaction that closes an attempt (not by triggers, so the logic
is testable in Python).

### 4.10 `coverage_run_locks` — per-ticker serialization

design/05 §9: concurrent mutating runs on one ticker would collide on the dossier
branch merge. One mutating run per coverage at a time.

```python
class CoverageRunLock(Base):
    __tablename__ = "coverage_run_locks"
    coverage_id: FK coverage.id, PRIMARY KEY          # one row max per coverage
    run_id: FK runs.id NOT NULL UNIQUE
    acquired_at: datetime NOT NULL
```

- Acquired by the orchestrator (`INSERT … ON CONFLICT DO NOTHING`) when it moves a run
  with `mutates_dossier=true` from `queued` → `running`. Failure to acquire is not an
  error: the run stays `queued` and is retried on the next orchestrator tick.
- **Held across `waiting_pm`** (the dossier branch is unmerged; another mutating run
  would fork from a stale main). Released on `succeeded | failed | cancelled`.
- `monitor_tick` and `pm_query` (`mutates_dossier=false`) never take the lock and are
  read-only against the dossier (they may write DB rows: events, llm_usage).
- The PM can always `cancel` a run to release a stuck lock (SPEC-CORE exposes it).

### 4.11 `pm_gates` — every PM decision point

```python
class PmGate(Base):
    __tablename__ = "pm_gates"
    id: Uuid pk
    run_id: FK runs.id NOT NULL
    coverage_id: FK coverage.id | None
    kind: String(32) NOT NULL                  # GateKind
    state: String(16) NOT NULL                 # open | answered | cancelled
    prompt: Text NOT NULL                      # what the PM is being asked
    payload: JsonType                          # options, summary, diffs, artifact refs
    allowed_answers: JsonType NOT NULL         # e.g. ["active","watch","reject"]
    answer: String(32) | None
    answer_notes: Text | None
    answered_by: String(64) | None
    answered_at: datetime | None
    idempotency_key: String(64) | None UNIQUE  # answer-once
    created_at, updated_at
    Index("ix_pm_gates_state_created", "state", "created_at")
```

A run in `waiting_pm` **must** have exactly one `open` gate (invariant checked in
tests). Answering a gate is the only way out of `waiting_pm` other than `cancel`.

### 4.12 `predictions` — immutable ledger

Adapted from v2's `docs/SPEC_PREDICTION_TRACKING.md` (never implemented there): the
extraction path is gone (v3 predictions arrive **structured** in `stage_result.yaml`,
so there is no LLM extraction call and no `<!--PREDICTION-->` comment parsing), and
the identity moves from `agent_id` to `house` + `run_id`.

```python
class Prediction(Base):
    __tablename__ = "predictions"
    id: Uuid pk
    coverage_id: FK coverage.id NOT NULL
    run_id: FK runs.id NOT NULL
    stage_id: FK run_stages.id NOT NULL        # which stage registered it
    house: FK houses.key NOT NULL              # WHO IS ON THE HOOK — never rewritten
    kind: String(24) NOT NULL                  # PredictionKind
    value: Price | None                        # target/entry price; NULL for stance
    currency: String(8) | None
    stance: String(16) | None                  # for kind='stance': buy|hold|sell|avoid
    scenario_label: String(32) | None          # bull|base|bear (kind='scenario')
    scenario_prob: Prob | None
    scenario_group: Uuid | None                # groups sibling bull/base/bear rows
    horizon_date: Date NOT NULL
    confidence: Prob | None                    # 0..1, MODEL-STATED; code never invents one
    rationale_ref: String(256) | None          # dossier path / report anchor
    status: String(16) NOT NULL default 'open'
    superseded_by_id: FK predictions.id | None
    superseded_run_id: FK runs.id | None
    scored_at: datetime | None
    realized_price: Price | None
    realized_return: Numeric(10,6) | None
    error_pct: Numeric(10,6) | None            # signed (realized - target)/target
    outcome_notes: Text | None
    idempotency_key: String(64) NOT NULL UNIQUE
    created_at
    __table_args__ = (
        CheckConstraint("kind IN (…5…)"), CheckConstraint("status IN (…5…)"),
        CheckConstraint("confidence IS NULL OR (confidence >= 0 AND confidence <= 1)"),
        CheckConstraint("scenario_prob IS NULL OR (scenario_prob >= 0 AND scenario_prob <= 1)"),
        CheckConstraint("kind <> 'target_price' OR (value IS NOT NULL AND currency IS NOT NULL)"),
        Index("ix_predictions_open_horizon", "status", "horizon_date"),
        Index("ix_predictions_coverage_house", "coverage_id", "house", "created_at"),
    )
```

**Immutability rules (enforced in the repository, tested in §9):**

1. The only columns any code may UPDATE after insert are: `status`,
   `superseded_by_id`, `superseded_run_id`, `scored_at`, `realized_*`, `error_pct`,
   `outcome_notes`. A repository `update_prediction()` that touches anything else
   raises. Nothing is ever deleted.
2. A revision inserts a **new row** and sets the old row's `status='superseded'` +
   `superseded_by_id`. Already-scored rows keep their score (history preserved).
3. `idempotency_key = sha256(f"{stage_id}|{kind}|{scenario_label or ''}|{value or ''}|{horizon_date}")`
   — computed by **this module** from the *stage row's own id*, not from any value the
   model produced (checklist item 4). Re-registering the same stage's results (retry,
   resumed run) is a no-op via `ON CONFLICT DO NOTHING`.
4. `confidence` is `NULL` if the model did not state one. Code must never synthesize a
   value (v2's fabricated "Neutral/50%" footer bug). The stage gate in SPEC-RUNNER
   fails a stage whose `stage_result.yaml` omits confidence where the role requires it;
   it does not fill it in.

### 4.13 `prediction_hits`

```python
class PredictionHit(Base):
    __tablename__ = "prediction_hits"
    id, prediction_id: FK predictions.id NOT NULL
    hit_price: Price NOT NULL
    hit_on: Date NOT NULL                     # trading date of the close that hit
    source: String(32) NOT NULL               # market-data provider id
    created_at
    UniqueConstraint("prediction_id", "hit_on", name="uq_prediction_hits_day")
```

Evidence rows written by the scoring job (SPEC-TRACKREC). The scoring *math* is that
spec's; the table and the uniqueness guarantee are here.

### 4.14 `corrections` — the novel track-record signal

```python
class Correction(Base):
    __tablename__ = "corrections"
    id: Uuid pk
    run_id: FK runs.id NOT NULL
    stage_id: FK run_stages.id NOT NULL          # the correcting stage (verifier/data_checker)
    correcting_house: FK houses.key NOT NULL
    attributed_stage_id: FK run_stages.id | None # whose output contained the error
    attributed_house: FK houses.key | None       # denormalized for metric views
    target: String(256) NOT NULL                 # "report-content.md#comps"
    was: Text NOT NULL
    now_: Text NOT NULL  # column name "now" is reserved-ish; map attribute now_ -> column "now_value"
    source: Text | None                          # citation for the corrected value
    created_at
    Index("ix_corrections_attributed", "attributed_house", "created_at")
```

`attributed_stage_id` comes from `stage_result.yaml`'s `corrections[].attributed_stage`
(a **stage seq within this run**, which the runner resolves to a stage id). If the seq
does not resolve to a stage of the same run, the value is dropped to `NULL` and a
warning is logged — a model must not be able to attribute an error to an arbitrary
stage id it invented (checklist items 3 and 4).

`attributed_house` is copied from the resolved stage row, not from model output.

### 4.15 `disagreements` — structured disagreement (replaces v2's debate engine)

```python
class Disagreement(Base):
    __tablename__ = "disagreements"
    id, run_id (FK), stage_id (FK)     # the cross-check stage that filed it
    house: FK houses.key NOT NULL       # the disagreeing contributor
    lead_house: FK houses.key NOT NULL
    material: Boolean NOT NULL
    lead_position: Text NOT NULL
    challenger_position: Text NOT NULL
    key_numbers: JsonType | None
    resolves_if: Text | None            # what evidence would settle it
    created_at
    # NOTE: no pm_rating here — v1's PM 👍/👎 rating lives on events (SPEC-TRACKREC §8);
    # rating disagreements directly would be an additive migration if ever wanted.
```

### 4.16 `events` — normalized observations

```python
class Event(Base):
    __tablename__ = "events"
    id: Uuid pk
    coverage_id: FK coverage.id | None
    kind: String(24) NOT NULL                 # EventKind
    severity: String(16) NOT NULL             # Severity
    title: String(256) NOT NULL
    detail: Text | None
    payload: JsonType                         # {tripwire_id, level_id, price, article_id, …}
    news_article_id: FK news_articles.id | None
    detected_by_run_id: FK runs.id | None      # the monitor_tick that saw it
    handled_by_run_id: FK runs.id | None       # the event_analysis it spawned
    action: String(24) | None                  # event_analysis | digest | notify_pm | silence
    dedupe_key: String(128) NOT NULL           # see below
    occurred_at: datetime NOT NULL
    created_at
    __table_args__ = (
        UniqueConstraint("dedupe_key", name="uq_events_dedupe_key"),
        Index("ix_events_coverage_created", "coverage_id", "created_at"),
        Index("ix_events_unhandled", "action", "handled_by_run_id"),
    )
```

`dedupe_key` is **owned by the detecting module** and must be deterministic and
self-describing, e.g.
`price_level:{coverage_id}:{level_id}:{trading_date}`,
`tripwire:{coverage_id}:{tripwire_id}:{trading_date}`,
`news:{coverage_id}:{news_article_id}:{tripwire_id|none}`.
The unique constraint makes escalation idempotent: a re-run monitor tick on the same
trading date cannot spawn a second `event_analysis` for the same crossing.

### 4.17 Dossier index (`dossier_index`, `dossier_commits`)

git is the audit trail; the DB is an index for fast reads and for the runner's
concurrency checks.

```python
class DossierIndex(Base):
    __tablename__ = "dossier_index"
    coverage_id: FK coverage.id PRIMARY KEY
    slug: String(48) NOT NULL UNIQUE           # == coverage.dossier_slug
    main_commit: String(40) | None             # HEAD of dossiers main for this dir
    updated_by_run_id: FK runs.id | None
    stance: String(16) | None                  # mirrored from dossier.md frontmatter
    conviction: String(16) | None
    as_of: Date | None
    target_price: Price | None                 # mirrored from valuation.md (display only)
    tripwire_count: Integer NOT NULL server_default '0'
    files: JsonType | None                     # {path: blob_sha} of the tracked contract files
    updated_at

class DossierCommit(Base):
    __tablename__ = "dossier_commits"
    id, coverage_id (FK), run_id (FK), stage_id (FK | None)
    commit_sha: String(40) NOT NULL
    branch: String(64) NOT NULL                # "run/<run_id>" or "main"
    parent_sha: String(40) | None
    message: Text NOT NULL                     # "run:<id> stage:<seq> <role>@<house>"
    files_changed: JsonType
    merged_to_main: Boolean NOT NULL server_default 'false'
    created_at
    UniqueConstraint("commit_sha", name="uq_dossier_commits_sha")
```

The mirrored fields (`stance`, `target_price`, …) are **display/query conveniences**;
the dossier file is the truth. Any code that must *act* on stance or TP reads the
dossier file (or the predictions ledger for TP), never these columns.

### 4.18 `doctrine_versions`

```python
class DoctrineVersion(Base):
    __tablename__ = "doctrine_versions"
    id: Uuid pk
    commit_sha: String(40) NOT NULL UNIQUE     # commit in the main repo's doctrine/ tree
    label: String(32) NOT NULL                 # "v1", "v2" … bumped on approval
    approved_by: String(64) | None             # PM user id (NULL for the seeded baseline)
    approved_at: datetime | None
    distillation_run_id: FK runs.id | None
    notes: Text | None
    is_current: Boolean NOT NULL server_default 'false'
    created_at
    Index("ux_doctrine_current", "is_current", unique=True,
          postgresql_where=text("is_current"))   # at most one current version
```

Every run pins `runs.doctrine_version_id` at start. "Which rules was this report
written under?" is answerable forever.

### 4.19 `artifacts` — content-addressed files

```python
class Artifact(Base):
    __tablename__ = "artifacts"
    id: Uuid pk
    sha256: String(64) NOT NULL
    kind: String(24) NOT NULL       # report_pdf | report_html | workbook | transcript | memo | diff
    media_type: String(64) | None
    size_bytes: BigInteger NOT NULL
    path: String(512) NOT NULL      # relative to ARTIFACTS_DIR
    run_id: FK runs.id | None
    stage_id: FK run_stages.id | None
    coverage_id: FK coverage.id | None
    filename: String(256) | None     # doctrine naming: NAME_SYM_EXCH-YYYYMMDD.pdf
    created_at
    UniqueConstraint("sha256", "kind", name="uq_artifacts_sha_kind")
    Index("ix_artifacts_run", "run_id")
```

Transcripts are artifacts too (`kind='transcript'`), so retention and redaction apply
uniformly. **Transcripts are written through the secret redactor before hashing**
(AGENTS.md Rule 2) — SPEC-RUNNER owns the redaction call; this spec owns the storage
contract.

### 4.20 `llm_usage` (ported from v2, extended)

```python
class LlmUsage(Base):
    __tablename__ = "llm_usage"
    id: Uuid pk
    run_id: FK runs.id | None
    stage_id: FK run_stages.id | None
    attempt_id: FK stage_attempts.id | None
    house: FK houses.key | None
    coverage_id: FK coverage.id | None
    provider: String(32) NOT NULL
    model: String(64) NOT NULL            # OBSERVED, from the provider/transcript
    substrate: String(8) | None
    purpose: String(32) NOT NULL          # stage | monitor_scoring | pm_query | reconcile
    input_tokens, output_tokens, cached_input_tokens: Integer | None
    cost_usd: Cost | None
    cost_source: String(16) NOT NULL      # provider_api | transcript | estimated
    request_id: String(64) | None
    reconciled_at: datetime | None        # nightly metered-vs-provider reconciliation
    reconciled_delta_usd: Cost | None
    error: Text | None
    started_at, completed_at, created_at
    Index("ix_llm_usage_house_completed", "house", "completed_at")
    Index("ix_llm_usage_run", "run_id")
```

### 4.21 `house_budget_days` — atomic daily cap accounting

Per-house daily caps (design/03 §4) cannot be enforced by `SELECT SUM(...)` under
concurrency (two runners both read "under cap", both proceed). A per-(house, day) row
with `SELECT … FOR UPDATE` gives an atomic reserve/settle:

```python
class HouseBudgetDay(Base):
    __tablename__ = "house_budget_days"
    house: FK houses.key, pk
    day: Date, pk                      # UTC date
    reserved_usd: Cost NOT NULL server_default '0'   # in-flight stage caps
    spent_usd: Cost NOT NULL server_default '0'      # settled actuals
    cap_usd: Cost | None               # snapshot of the configured cap for that day
    updated_at
```

Reserve on attempt launch, settle on attempt close (SPEC-CORE/SPEC-RUNNER own the
policy; this spec owns the row and the invariant `reserved_usd >= 0`).

### 4.22 `outbox_events` — transactional outbox (core → adapter)

The adapter must post to Mattermost *because* a domain fact was committed, and must not
miss one if it was disconnected. Core writes an outbox row in the same transaction as
the state change; the adapter consumes with `SKIP LOCKED` and marks delivered.

```python
class OutboxEvent(Base):
    __tablename__ = "outbox_events"
    id: Uuid pk
    kind: String(48) NOT NULL          # coverage.state_changed | run.finished | gate.opened |
                                       # gate.answered | digest.ready | risk.alert | error.raised
    coverage_id: FK coverage.id | None
    run_id: FK runs.id | None
    payload: JsonType NOT NULL         # everything the adapter needs; no DB round-trip required
    dedupe_key: String(128) NOT NULL UNIQUE
    available_at: datetime NOT NULL server_default now()
    claimed_by: String(64) | None
    lease_expires_at: datetime | None
    attempts: Integer NOT NULL server_default '0'
    delivered_at: datetime | None
    last_error: Text | None
    created_at
    Index("ix_outbox_pending", "delivered_at", "available_at")
```

### 4.23 `audit_log`, `idempotency_keys`, `scheduler_job_logs`

```python
class AuditLog(Base):          # ported from v2, actor generalized
    __tablename__ = "audit_log"
    id, action: String(64) NOT NULL      # coverage.promote | run.cancel | trade.confirm | …
    actor_type: String(16) NOT NULL      # ActorType
    actor_id: String(64) NOT NULL        # PM user id / run id / job name
    target: String(256) | None           # "coverage:<id>" / "run:<id>"
    before_state: JsonType | None
    after_state: JsonType | None
    request_id: String(64) | None        # correlates API request → audit rows
    created_at (indexed)

class IdempotencyKey(Base):    # every mutating API action carries one (AGENTS.md)
    __tablename__ = "idempotency_keys"
    key: String(64) primary key          # sha256(route|actor|normalized_args)
    route: String(64) NOT NULL
    actor_id: String(64) NOT NULL
    response: JsonType | None            # replayed verbatim on a repeat call
    status_code: Integer | None
    expires_at: datetime NOT NULL        # retention job deletes expired rows
    created_at

class SchedulerJobLog(Base):   # ported; v2's silent-scheduler-death lesson
    __tablename__ = "scheduler_job_logs"
    id, job_name: String(64) NOT NULL
    status: String(16) NOT NULL          # started | succeeded | failed | skipped
    scheduled_for: datetime | None
    started_at, finished_at
    detail: Text | None
    runs_created: Integer NOT NULL server_default '0'
    created_at (indexed)
    Index("ix_scheduler_job_logs_job_created", "job_name", "created_at")
```

A missed-schedule watchdog (SPEC-CORE) queries `scheduler_job_logs` for a job whose
last success is older than its interval and raises a desk alert via the outbox.

### 4.24 `news_articles` (ported from v2, embedding column deferred)

```python
class NewsArticle(Base):
    __tablename__ = "news_articles"
    id: Uuid pk
    url: String(2048) | None UNIQUE
    normalised_url: String(2048) | None      # produced by core/news/normalize.py ONLY
    content_hash: String(64) | None          # sha256 of normalized title+body
    cluster_id: Uuid | None                  # near-duplicate grouping (SPEC-MONITORING)
    title: String(512) | None
    source: String(64) | None
    published_at: datetime | None
    full_text: Text | None
    summary: Text | None
    tickers: JsonType | None                 # matched coverage tickers
    scored_at: datetime | None               # tripwire scoring (SPEC-MONITORING)
    scores: JsonType | None                  # [{coverage_id, tripwire_id, materiality, reason}]
    created_at
    Index("ix_news_normalised_url", "normalised_url")
    Index("ix_news_content_hash", "content_hash")
    Index("ix_news_published", "published_at")
```

**pgvector is NOT in the baseline.** The extension is not created and no `embedding`
column exists. v2 used pgvector for news dedup/search; v3's dedup is deterministic
(normalized URL + content hash + cluster) and its relevance judgment is the tripwire
scoring call, so the embedding pipeline has no consumer today. The keep/drop decision
is formally made in SPEC-MONITORING (phase 4); if it lands as "keep", it is an
**additive** migration (`CREATE EXTENSION vector` + `ALTER TABLE news_articles ADD
COLUMN embedding vector(N)`), which is why the column name is reserved and unused here.

### 4.25 Ported deterministic tables (unchanged semantics, v3 types)

Ported from v2 `db/models.py` with three mechanical changes: **`Float` → `Numeric`**
for every price/quantity/money column, `String(36)` ids → `Uuid`, and Mattermost-shaped
columns (`post_id`, `channel_id`) retained but nullable — the core must be usable with
no chat adapter at all (design/01 principle 11).

- `positions` — `(ticker, book_id)` unique; `net_quantity Numeric(20,6)`,
  `avg_cost Price`, `currency`, `last_trade_at`.
- `trades` — direction `buy|sell`, `quantity Numeric(20,6)`, `price Price`, `currency`,
  `book_id`, `traded_at`, `confirmed_at`, `posted_by_user_id`, `source_ref` (was
  `post_id`), `raw_text`, `parse_confidence`.
- `pending_trade_confirmations` — `source_ref` UNIQUE, `trade_json`, `status`,
  `expires_at`, resolution fields. Manual PM confirmation only; **no LLM books a trade**.
- `book_cash` — `book_id` pk, `cash_usd Money`.
- `cash_events` — `book_id`, `amount_usd Money`, `event_type`, `posted_by_user_id`.
- `risk_configs` — one row per book; all limits/params as in v2 (`Numeric` now).
- `risk_events` — `(book_id, ticker, event_type, snapshot_at)` unique, `severity`,
  `acknowledged*`.
- `portfolio_snapshots` — `book`, `total_nav_usd Money`, `positions_json`,
  `var_results`, concentration metrics, `snapshot_at`.

Retired v2 tables — **do not create**: `agents`, `analyses`, `conversation_turns`,
`debates`, `channel_watchlist`, `watch_channel_modes`, `thread_ownership`,
`generated_reports`, `global_directives`, `scheduled_tasks`, `runtime_settings`,
`processed_commands` (replaced by `idempotency_keys`).

---

## 5. Coverage state machine

`core/domain/coverage_sm.py` is **pure**: it takes `(current_state, action, context)`
and returns `Transition(to_state, cause, side_effects)` or raises
`IllegalTransition`. The repository applies it inside one transaction.

```
proposed ──initiate──▶ initiating ──finalizer_done──▶ decision_pending
    ▲                      │                                │
    │                      └──run_failed──▶ failed          ├──decide:active──▶ active
    │                                        │              ├──decide:watch───▶ watch
    │                                        └──retry───────┘                  │
    │                                          (→ initiating)                  │
    │                                                       └──decide:reject──▶ rejected
    │                                                                          │
 re-propose ◀── exited ◀──exit── active ◀──promote── watch ◀──demote── active ─┘
 re-propose ◀── rejected            │                  │
                                    └──────exit────────┴──▶ exited

additional edges: decision_pending ──cancel──▶ failed        (run cancelled at the gate)
                  proposed | failed ──withdraw──▶ rejected   (PM abandons the proposal)
```

### Transition table (complete — no other transition is legal)

| # | From | To | Action | Actor | Guard | Side effects (same transaction) |
|---|---|---|---|---|---|---|
| 1 | — | `proposed` | `propose` | PM | ticker+exchange not already covered (unique) | create coverage row (+ `dossier_slug`), transition row, audit, outbox `coverage.state_changed` |
| 2 | `proposed` | `initiating` | `start_initiation` | RUN | an `initiation` run for this coverage moved to `running`; `lead_house` set | set `coverage.lead_house` from run params; acquire coverage lock |
| 3 | `initiating` | `decision_pending` | `initiation_delivered` | RUN | run status `waiting_pm` with an open `initiation_decision` gate | open gate; outbox `gate.opened` (adapter posts PDF + summary + decision prompt) |
| 4 | `initiating` | `failed` | `initiation_failed` | RUN | run terminal `failed` or `cancelled` | release lock; outbox `run.finished` (desk alert) |
| 4b | `decision_pending` | `failed` | `initiation_cancelled` | PM / RUN | the initiation run was cancelled while `waiting_pm` (PM cancels instead of deciding) | cancel the open gate (`state='cancelled'`); release lock; branch retained, not merged; outbox `run.finished` (desk alert). Without this edge a cancelled gate would strand the coverage in `decision_pending` forever (`decide` requires an open gate) |
| 5 | `failed` | `initiating` | `retry_initiation` | PM | — | new `initiation` run (old dossier branch retained) |
| 5b | `proposed` / `failed` | `rejected` | `withdraw` | PM | — | mandatory note; no branch merge (there may be no run at all); a withdrawn ticker can be re-proposed later via row 13 |
| 6 | `decision_pending` | `active` | `decide:active` | PM | gate open; answer in `allowed_answers` | answer gate; merge run branch to dossier main; predictions `open`; write `coverage_levels` from the finalizer's proposal (PM may override); `decided_at`; outbox `coverage.state_changed` (adapter creates channel + posts report) |
| 7 | `decision_pending` | `watch` | `decide:watch` | PM | as above | same as 6, monitoring cadence = watch |
| 8 | `decision_pending` | `rejected` | `decide:reject` | PM | as above | answer gate; branch retained, **not** merged; predictions of that run marked `superseded` (never scored — they were never adopted); no channel |
| 9 | `watch` | `active` | `promote` | PM | — | cadence switches immediately; **auto-spawn `deep_review` run** (does not block the transition); outbox |
| 10 | `active` | `watch` | `demote` | PM | — | cadence switches; open predictions stay open |
| 11 | `active` | `exited` | `exit` | PM | **no open position** in any book (`positions.net_quantity = 0`) unless `force=true` with a mandatory note | open predictions → `expired` (scored at exit price by SPEC-TRACKREC); `exited_at`; outbox (adapter archives channel) |
| 12 | `watch` | `exited` | `exit` | PM | — | as 11 |
| 13 | `exited` / `rejected` | `proposed` | `re_propose` | PM | — | same coverage row, same slug; new initiation receives the old dossier as context; transition row records the re-entry |
| 14 | `active` / `watch` | (same) | `set_lead` | PM | new house `enabled AND assignable`; a `lead_change` gate answered (or PM direct) | `coverage_lead_history` row; open predictions keep their original `house`; outbox |

Rules that hold for **every** transition:

- Exactly one `coverage_transitions` row **and** one `audit_log` row per transition, in
  the same transaction as the `coverage.state` UPDATE.
- An outbox row for every transition that the desk should see (all of the above).
- `cause` is machine-readable and stable (`pm_decision`, `run_failed`, `promote`,
  `exit`, `re_propose`, …); free text goes in `notes`.
- Illegal transitions raise `IllegalTransition` and never partially apply — the API
  returns 409 (SPEC-CORE).
- `set_lead` on `initiating`/`decision_pending` is rejected: a lead cannot be swapped
  mid-initiation (the running stages carry the old lead).

---

## 6. Run and stage lifecycle

### 6.1 Run status machine

```
queued ──▶ running ──▶ waiting_pm ──▶ succeeded
   ▲          │  │                        ▲
   └──────────┘  ├────────────────────────┘   (runs with no PM gate go straight to succeeded)
   (transient    ├──▶ failed        (terminal stage failure / max attempts exhausted)
    failure)     └──▶ cancelled     (PM)
```

- `running → queued` is the **transient-failure re-entry** (design/02 §3): a provider
  outage or an expired lease re-queues the run *at the current stage boundary*.
  Completed stages are not redone. `runs.error` records the last transient reason;
  `stage.attempts` increments. Only a terminal failure (max attempts exhausted, or a
  gate that fails deterministically after retries) reaches `failed`.
- `waiting_pm` requires exactly one `open` `pm_gates` row.
- `cancelled` is reachable from `queued`, `running`, and `waiting_pm`; it kills
  in-flight attempts (SPEC-RUNNER), releases the coverage lock, and leaves the dossier
  branch unmerged.
- Terminal statuses (`succeeded`, `failed`, `cancelled`) release the coverage lock and
  write outbox `run.finished`.

### 6.2 Stage status machine

`queued → running → succeeded | failed | skipped`, with `running → queued` on a
retryable failure while `attempts < max_attempts`, and `available_at = now() +
backoff(attempts)` (exponential with jitter; parameters from config). `skipped` is set
by the orchestrator when a stage is not needed (e.g. `verify_count=1` skips stage 3, a
cross-check that the auto-rule did not trigger).

### 6.3 Queue: claiming with `SKIP LOCKED`

`core/db/queue.py`:

```python
CLAIM_SQL = text("""
    WITH claimable AS (
        SELECT s.id
        FROM run_stages s
        JOIN runs r ON r.id = s.run_id
        WHERE s.status = 'queued'
          AND s.available_at <= now()
          AND r.status = 'running'
          AND (s.depends_on_seq IS NULL OR EXISTS (
                SELECT 1 FROM run_stages d
                WHERE d.run_id = s.run_id
                  AND d.seq = s.depends_on_seq
                  AND d.status IN ('succeeded', 'skipped')))
          AND (:substrates IS NULL OR s.substrate = ANY(:substrates))
          AND (:houses IS NULL OR s.house = ANY(:houses))
        ORDER BY r.priority DESC, r.created_at, s.seq
        FOR UPDATE OF s SKIP LOCKED
        LIMIT 1
    )
    UPDATE run_stages s
    SET status = 'running',
        claimed_by = :worker_id,
        claimed_at = now(),
        lease_expires_at = now() + (:lease_seconds * interval '1 second'),
        attempts = s.attempts + 1,
        started_at = COALESCE(s.started_at, now())
    FROM claimable c
    WHERE s.id = c.id
    RETURNING s.*;
""")
```

- `FOR UPDATE OF s SKIP LOCKED` — two runner workers never claim the same stage; a
  locked row is skipped rather than blocking (design/05 §1).
- **Stage dependencies are enforced here, in the claim query itself** (the `EXISTS` on
  `depends_on_seq`): a stage whose dependency has not reached `succeeded`/`skipped` is
  simply invisible to workers. There is no separate orchestrator "release" step to race
  with — a verifier stage cannot be claimed while the author stage is still running.
  A `skipped` dependency satisfies its dependents; the orchestrator only marks a stage
  `skipped` once its own dependency chain is settled, so a skip cannot open a claim
  window ahead of an unfinished earlier stage.
- Only stages of a run already in `running` are claimable: the orchestrator promotes
  `queued → running` (and acquires the coverage lock) *before* stages become visible to
  the runner. That keeps per-ticker serialization in one place.
- The `:substrates` / `:houses` filters let the operator run a dedicated harness worker
  later; both default to `NULL` (claim anything).
- **Lease**: the worker calls `heartbeat(stage_id, worker_id)` every
  `lease_seconds / 3`, extending `lease_expires_at`. Harness stages run for
  minutes-to-hours, so the lease must be long (config; e.g. 300 s) and heartbeats
  cheap.
- **Reaper** (core, every minute): stages with `status='running'` and
  `lease_expires_at < now()` are treated as crashed workers →
  `attempts < max_attempts` ? back to `queued` with backoff (and the open attempt row
  closed with `kill_reason='lease_expired'`) : `failed`, which fails the run.
- `release_stage(stage_id, result)` writes the terminal stage row + its attempt row + a
  `dossier_commits` row (if any) in one transaction.

`SKIP LOCKED` is Postgres-only; the SQLite unit-test dialect ignores it. Queue
semantics (concurrent claim exclusivity, lease expiry) therefore have **integration
tests** against real Postgres (§9.3), while the *logic around* the queue is unit-tested
against fakes.

### 6.4 Invariants (asserted in tests; violations are bugs)

1. A run in `waiting_pm` has exactly one `open` gate; a run in a terminal state has
   none.
2. A `mutates_dossier` run in `running` or `waiting_pm` holds the coverage lock; at most
   one such run exists per coverage.
3. `runs.cost_usd == SUM(run_stages.cost_usd) == SUM(stage_attempts.cost_usd)` after
   every stage close.
4. `stage.house` for a `meta=true` house appears only in `distillation` runs;
   `predictions.house` and `corrections.attributed_house` are never a meta house.
5. Every `coverage.state` change has a matching `coverage_transitions` row with the same
   `to_state` and an `audit_log` row with the same `request_id`.
6. No `predictions` row is ever deleted, and no immutable column is ever updated (§4.12).

---

## 7. Alembic baseline

- `core/db/migrations/versions/0001_baseline.py`, `down_revision = None`. **One** fresh
  baseline: no v2 migration history is carried over.
- Creates every table in §4 in FK order, all constraints, all indexes, including the
  partial unique index on `doctrine_versions(is_current) WHERE is_current`.
- Does **not** `CREATE EXTENSION vector` (§4.24) and does not create the retired tables.
- `env.py` reads the URL from the settings module (`settings.database_url`), never from
  `os.getenv` and never from a `.env` read (AGENTS.md Rule 4). `target_metadata =
  Base.metadata` so `alembic check` (autogenerate diff) can run in CI.
- `downgrade()` drops all tables in reverse order (baseline only; later migrations must
  be reversible individually).
- A `seed_doctrine_version` data step is **not** in the migration. Seeding the initial
  `doctrine_versions` row (from the current `doctrine/` commit) is done by
  `fund bootstrap` in SPEC-CORE, so migrations stay pure DDL.

---

## 8. Repository API (what later specs call)

Thin, typed, transaction-scoped. Signatures are the contract other specs rely on:

```python
# core/db/repo/coverage.py
propose(ticker, exchange, name, currency, actor_id, notes=None, isin=None) -> Coverage
transition(coverage_id, action, actor_type, actor_id, *, run_id=None, force=False,
           notes=None, request_id=None) -> CoverageTransition      # applies coverage_sm
set_lead(coverage_id, house, actor_id, *, run_id=None, rationale=None) -> None
resolve_contributors(coverage_id) -> list[str]                     # §4.3 rule
set_levels(coverage_id, levels: list[LevelIn], set_by, run_id=None) -> None
get_by_slug(slug) / list_by_state(state) -> …

# core/db/repo/runs.py
create_run(coverage_id, type, trigger, *, params, priority, budget_cap_usd,
           doctrine_version_id, requested_by=None) -> Run
add_stages(run_id, stages: list[StageIn]) -> list[RunStage]        # seq, role, house, substrate
start_run(run_id) -> bool        # acquires coverage lock; False if lock held (stay queued)
requeue_run(run_id, reason)      # running → queued (transient)
finish_run(run_id, status, summary=None, error=None) -> None       # releases lock, outbox
open_gate(run_id, kind, prompt, payload, allowed_answers) -> PmGate
answer_gate(gate_id, answer, actor_id, notes, idempotency_key) -> PmGate

# core/db/queue.py
claim_stage(worker_id, *, substrates=None, houses=None, lease_seconds) -> RunStage | None
heartbeat(stage_id, worker_id) -> bool                             # False if lease lost
complete_stage(stage_id, attempt: AttemptIn, result: dict) -> None
fail_stage(stage_id, attempt: AttemptIn, retryable: bool) -> None
reap_expired(now) -> list[UUID]

# core/db/repo/predictions.py
register_from_stage(stage_id, entries: list[PredictionIn]) -> list[Prediction]  # idempotent
supersede(prediction_id, by_prediction_id, run_id) -> None
score(prediction_id, status, realized_price, ...) -> None          # SPEC-TRACKREC calls

# core/db/repo/events.py
record_event(coverage_id, kind, severity, title, dedupe_key, payload, ...) -> Event | None
                                                                    # None if dedupe hit
mark_handled(event_id, run_id) -> None

# core/db/repo/outbox.py
publish(kind, payload, *, coverage_id=None, run_id=None, dedupe_key) -> None
consume(worker_id, limit, lease_seconds) -> list[OutboxEvent]      # SKIP LOCKED
ack(event_id) / nack(event_id, error, backoff)
```

Every mutating repository function takes an explicit `Session` (dependency-injected);
no module-level session global, no `scoped_session` (v2 used one; v3 does not — see
Checklist/concurrency).

---

## 9. Test plan (TDD)

Tests live in `tests/unit/db/`, `tests/unit/domain/`, `tests/integration/db/`.
Network is blocked by an autouse socket fixture in `tests/unit/conftest.py`; unit tests
touch **no** external service.

### 9.1 Unit — pure state machines (no DB at all)

`core/domain/coverage_sm.py` and `run_sm.py` are pure functions, so these are table-driven:

- Every legal transition in §5 returns the expected `to_state`, `cause`, and
  side-effect list.
- **Every illegal pair raises `IllegalTransition`** — generated exhaustively from the
  cross-product of `CoverageState × Action` minus the legal table (catches a builder
  silently allowing `watch → decision_pending`).
- Guards: `exit` from `active` with an open position raises unless `force=True`;
  `force=True` without a note raises; `set_lead` to a `meta` or non-`assignable` house
  raises; `set_lead` during `initiating`/`decision_pending` raises; `withdraw` without a
  note raises.
- `initiation_cancelled` from `decision_pending` returns the "cancel open gate, release
  lock, retain branch" side effects; the coverage is then retryable via `retry_initiation`
  (row 5) — the cancel-at-gate path can never strand a ticker.
- `promote` returns the "spawn `deep_review`" side effect; `decide:reject` returns the
  "supersede predictions, do not merge branch" side effect.
- Run SM: `running → queued` allowed with `attempts < max_attempts`; forbidden after
  exhaustion (must go `failed`). `waiting_pm` requires a gate in the side-effect list.

### 9.2 Unit — models and repositories on SQLite

Fixture: in-memory SQLite (`sqlite+pysqlite:///:memory:`) with
`Base.metadata.create_all`, `PRAGMA foreign_keys=ON`. This is a *fake* Postgres for
dialect-independent behavior only; the `JsonType`/`Uuid` variants make it work.

- Coverage lifecycle end to end: propose → initiating → decision_pending → active,
  asserting after each step that a `coverage_transitions` row **and** an `audit_log` row
  **and** an `outbox_events` row exist with the same `request_id` (invariant 5).
- Rolling back the transaction leaves no transition/audit/outbox rows (atomicity).
- `dossier_slug` computed once and stable across re-proposal (`exited → proposed` keeps
  the slug).
- Contributor resolution: empty table → all enabled+assignable houses except lead;
  explicit rows → exactly those; a disabled house is excluded; the result is frozen into
  stage rows so a later `houses` edit does not change the run.
- Predictions: `register_from_stage` twice with the same stage → one row (idempotency
  key); a superseding registration marks the old row `superseded` and preserves its
  score; a repository call attempting to update `value`/`horizon_date`/`house` raises;
  `confidence=None` persists as NULL (never 0.5).
- Corrections: `attributed_stage` seq that is not a stage of this run → `NULL` + warning
  (no exception, no cross-run attribution); a valid seq resolves to the right
  `attributed_house`.
- Events: same `dedupe_key` twice → second `record_event` returns `None` and inserts
  nothing.
- Levels: setting a new `entry` level supersedes the old (`active=false`), and both rows
  remain.
- Cost rollups: closing three attempts (2 failed, 1 succeeded) sums into
  `stage.cost_usd` and `run.cost_usd` (invariant 3) — failed attempts are counted.
- CHECK constraints reject: `confidence=1.5`, `value<=0` on a level, `assignable AND
  meta` on a house, a bad enum string.

### 9.3 Integration — Postgres only (`@pytest.mark.integration`)

Opt-in (`pytest -m integration`), against a disposable Postgres (docker or an operator
URL from settings; skipped if unset). These cover everything SQLite cannot prove:

- `alembic upgrade head` from empty → `alembic check` reports **no** diff vs
  `Base.metadata` (models and migration cannot drift).
- `alembic downgrade base` succeeds.
- **Queue exclusivity**: N threads/connections call `claim_stage` concurrently on M
  queued stages → each stage is claimed exactly once, no worker blocks (SKIP LOCKED),
  no duplicate `RETURNING` row.
- **Dependency gating**: with stage 1 `running` and stage 2 `queued` (`depends_on_seq=1`),
  `claim_stage` returns nothing; the moment stage 1 is `succeeded` (or `skipped`),
  stage 2 is claimable. Two parallel stages both depending on seq 1 are both claimable
  after it.
- **Lease/reaper**: a claimed stage whose `lease_expires_at` passes is requeued by
  `reap_expired`, its open attempt closed `kill_reason='lease_expired'`, and
  `attempts` incremented; after `max_attempts` it is `failed` and the run fails.
- **Coverage lock**: two concurrent `start_run` calls for two mutating runs on one
  coverage → exactly one acquires; the loser stays `queued` (no exception, no deadlock).
  A `monitor_tick` run starts regardless.
- **Partial unique index**: two `doctrine_versions` with `is_current=true` → violation.
- **Outbox**: concurrent `consume` calls never hand the same row to two adapters;
  `nack` applies backoff via `available_at`.
- Numeric round-trip: a `Price` value with 6 dp survives insert/select exactly (no float
  drift) — the deterministic-money-path guard.

### 9.4 Fakes provided to later specs

`tests/fakes/` (importable by every later spec's tests):
`FakeClock`, `FakeMarketData` (pinned prices/history/FX), `FakeNewsSource`,
`FakeLLM` (records prompts, returns canned `stage_result.yaml`), `FakeHarnessDriver`
(SPEC-RUNNER defines the interface; the fake lives here so SPEC-CORE can use it in
phase 1.3 before the runner exists), and `make_coverage()/make_run()/make_stage()`
factories. **No fake in this repo ever opens a socket.**

---

## 10. Spec Authoring Checklist

- **Side-effect cost.** No provider is called from this layer — zero LLM, market-data,
  news, search, or embedding calls; cost is unchanged by definition. Two *deliberate*
  cost decisions are encoded here: (a) predictions arrive structured in
  `stage_result.yaml`, so v2's per-report LLM extraction call (1 light call/report) is
  **eliminated**; (b) no `embedding` column and no `CREATE EXTENSION vector` in the
  baseline, so the per-article embedding call v2 made (1 call/article, ~50–200
  articles/day) is **not incurred** unless SPEC-MONITORING re-adopts it. DB write volume
  grows (attempts, transitions, outbox, corrections rows), which is bounded and local:
  ~10² rows/run, ~10³ rows/day at 50 tickers.
- **Concurrency model.** Shared state is *durable*, not in-process: (1) the stage queue
  is guarded by row locks (`FOR UPDATE … SKIP LOCKED`) plus a lease + reaper, so a
  crashed worker cannot strand a stage; (2) per-ticker serialization is the
  `coverage_run_locks` row (a unique PK), acquired with `INSERT … ON CONFLICT DO
  NOTHING` and held through `waiting_pm`; (3) per-house daily budgets use a
  `house_budget_days` row with `SELECT … FOR UPDATE` so two concurrent reservations
  cannot both pass the cap; (4) the outbox is consumed with `SKIP LOCKED` + lease, so a
  reconnecting adapter cannot double-post. **No module-level session, engine, or cache
  is exposed**: `core/db/session.py` builds an engine bound to settings and hands out
  `Session`s; repositories take a `Session` argument (v2's `scoped_session` global is
  deliberately dropped, since the runner and scheduler are concurrent). The only
  process-global is the SQLAlchemy engine's connection pool, which is thread-safe by
  design.
- **LLM-as-filter threat model.** No LLM decision gates any output in this layer. What
  it *does* is bound the damage an LLM can do elsewhere, because model output crosses
  into the DB here: (a) `stage_result.yaml` is schema-validated by the runner before any
  row is written, and this layer accepts only typed fields — free prose is stored, never
  executed or interpreted; (b) a model cannot attribute a correction to an arbitrary
  stage: `attributed_stage` is a **seq within its own run**, resolved to a stage id by
  code, dropped to NULL if it does not resolve (a model cannot smear another house's
  record); (c) a model cannot forge identity: `predictions.house`,
  `corrections.correcting_house`, and `stage_attempts.house` are copied from the stage
  row the orchestrator created, never read from model output; (d) idempotency keys are
  derived from **our** ids, so a model cannot cause duplicate or colliding rows by
  echoing values; (e) `confidence` is NULL when unstated — code never invents one; (f)
  text fields written from model output (`summary`, `corrections.was/now_`,
  `disagreements.*`) pass through the secret redactor before persist (AGENTS.md Rule 2),
  and length caps apply. Prompt-injected news content therefore cannot reach the PM
  through this layer without a `severity`+`dedupe_key` that code assigned.
- **Identity-key ownership.** Every key used for "did we already do this / which item
  survived" is minted by the module that owns the concept: `coverage.dossier_slug`
  (computed once at creation by `core/domain/dossier_slug.py`; every other module —
  dossier repo, workspace, channel name — *reads the column*, never re-derives it);
  `predictions.idempotency_key` (sha256 over the **stage id** and typed fields, not over
  model text); `events.dedupe_key` (minted by the detecting module with a documented
  prefix grammar, unique-constrained); `outbox_events.dedupe_key` (minted by the
  publisher); `idempotency_keys.key` (sha256(route|actor|normalized_args), minted by the
  API layer). The one borrowed key is `news_articles.normalised_url`, which is produced
  **only** by `core/news/normalize.py` (SPEC-MONITORING) — the invariant "no other module
  normalizes a URL" is enforced by keeping the function private to that module and by a
  unit test that asserts dedup uses `normalised_url` + `content_hash`, not raw `url`.
- **Test isolation.** Unit tests reach Postgres never: they run on in-memory SQLite
  (`JsonType`/`Uuid` variants make the models dialect-portable) or on pure functions with
  no DB at all. Postgres-only behavior (`SKIP LOCKED`, leases, partial unique indexes,
  `alembic check`, numeric fidelity) is covered by `@pytest.mark.integration` tests that
  **skip** when no test-database URL is configured. No LLM, market-data, news, search, or
  harness code path exists in this layer, so there is nothing to mock; the autouse socket
  guard in `tests/unit/conftest.py` would catch any accidental client import (e.g. a
  builder adding an embedding call to `news_articles`), which is exactly the miss it is
  there to catch. The shared fakes in `tests/fakes/` (§9.4) open no sockets.

---

## 11. Decisions worth knowing (and what could invalidate them)

1. **`Numeric` everywhere for money** (v2 used `Float`). Phase 7.3's data migration must
   cast on import; a float→numeric cast of an existing v2 price is exact enough at 6 dp.
2. **No Postgres `ENUM` types** — `String` + `CHECK` + Python `StrEnum`. Adding a run
   type or role is then an ordinary migration, not an `ALTER TYPE`.
3. **`stage_attempts` is a table, not a counter.** Costs of failed attempts are real and
   must be attributable; v2's cost drift came from unaccounted retries.
4. **The coverage lock is held through `waiting_pm`.** This is deliberate (an unmerged
   dossier branch must not be forked from) but it means a PM who never answers an
   initiation gate blocks that ticker's deep reviews. Mitigation: gates are surfaced in
   the desk channel and by `fund gates list`; PM can cancel. If this proves annoying in
   the Phase 2.4 pilot, the alternative is to merge the branch on `finalizer` success and
   treat the PM decision as coverage-state-only — revisit with evidence, not before.
5. **pgvector deferred, not adopted.** Baseline ships without it (§4.24); SPEC-MONITORING
   decides.
6. **`monitor_tick` and `pm_query` never mutate the dossier**, which is what lets them run
   concurrently with a heavy run on the same ticker. If a future monitor wants to write
   `events/` notes into the dossier, it must become a `mutates_dossier` run type and take
   the lock.

---

## 12. Cross-spec amendments to this schema (fold in at BUILD)

Later specs amend this baseline. **None of this spec has been built yet, so the builder
folds these in directly** (they are listed authoritatively here; the motivating spec owns
the semantics):

| Amendment | Source |
|---|---|
| `StageRole` gains `PM_QUERY = "pm_query"` (needs `doctrine/roles/pm_query.md`, PM-approved) | SPEC-CORE §3.2/§10 |
| `runs`: partial unique index `uq_runs_trigger_ref ON (coverage_id, type, trigger_ref) WHERE trigger_ref IS NOT NULL` | SPEC-CORE §4 |
| `corrections` gains `idempotency_key String(64) UNIQUE = sha256(stage_id \| target \| was \| now)` | SPEC-INITIATION §6 |
| `predictions` gains `direction String(8)` (`up\|down\|flat`) and `pinned_price Price`, both code-written at registration | SPEC-TRACKREC §2.2 |
| `events` gains `pm_rating SmallInteger \| None`, `pm_rated_at`, `pm_rated_by` | SPEC-TRACKREC §8 |
| New table `house_metric_snapshots (house, as_of, scope, metrics JSONB)`, unique `(house, as_of, scope)` | SPEC-TRACKREC §3 |
| New tables `mm_channels`, `mm_posts` (core-owned; adapter reaches them only via the API) | SPEC-ADAPTER §3.2 |
| `outbox_events` is consumed **through the core API** (`POST /outbox/claim\|{id}/ack\|{id}/nack`), not by the adapter touching Postgres — §4.22's "the adapter consumes with SKIP LOCKED" claim happens inside core | SPEC-ADAPTER §5.1 |
| `tripwires.yaml` gains optional per-tripwire `keywords: [...]` (dossier contract, not a DB column) | SPEC-MONITORING §4.2 amending SPEC-INITIATION §3.3 |
| New table `price_pins (exchange, ticker, trading_date, value, currency, source, pinned_at)`, unique on the first three — the persisted "one price truth"; owned and written only by the market-data service | SPEC-MARKETDATA §2.1 |
