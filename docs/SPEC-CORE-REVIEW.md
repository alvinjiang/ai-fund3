# SPEC-CORE implementation review

Review of `specs/SPEC-CORE.md` against the implementation on branch `spec-core-truthing`,
after the truthing pass and BUGS #1–#5.

**Method:** section-by-section audit. Every finding below is either read off a named
`file:line` or verified by running the code. Claims marked **(verified)** were reproduced
empirically in this session, not inferred from reading.

**Baseline at review time:** `pytest tests/unit` → 370 passed, 32 skipped.

**Status vocabulary:** IMPLEMENTED / PARTIAL / MISSING / DEVIATES (present but differs
from spec in a way that changes behavior).

---

## §2 Configuration

### Findings

- **§2 templates** — IMPLEMENTED. `config/{houses,fund,pricing}.yaml.example` and
  `.env.example` all exist and match the spec's documented shape.
- **§2.1 `env_file` via `AI_FUND_CONFIG_DIR`** — DEVIATES. `core/config/settings.py:21`
  hardcodes `env_file=".env"` (CWD-relative) instead of
  `os.environ.get("AI_FUND_CONFIG_DIR", "/etc/ai-fund") + "/.env"`. The operator `.env`
  at `/etc/ai-fund/.env` is never read, and `config_dir` (line 24) is a dead field no
  code consumes.
- **§2.1 field types** — DEVIATES. `database_url: str` not `PostgresDsn`;
  `core_api_url` / `market_data_url` / `search_endpoint` are `str` not `AnyHttpUrl`
  (`core/config/settings.py:25-33`). No URL validation fires.
- **§2.1 defaults** — DEVIATES. The spec makes `database_url`, `artifacts_dir`,
  `dossier_repo_path`, `doctrine_dir`, `market_data_url` required; the impl gives all
  five dev defaults, including a literal placeholder DSN
  (`postgresql://ai_fund:replace_me@localhost:5432/ai_fund`, `settings.py:25`). A
  deployment missing `DATABASE_URL` gets a placeholder instead of failing loudly — the
  silent-fallback pattern AGENTS.md forbids. Only `core_api_token` is required.
- **§2.1 `frozen`** — IMPLEMENTED + tested. `settings.py:21`, asserted in
  `tests/unit/config/test_settings.py:30-35`.
- **§2.1 `lru_cache` `get_settings()`** — PARTIAL. Hand-rolled module global +
  `reset_settings()` (`settings.py:39-51`) rather than `lru_cache`. Behaviorally
  equivalent; no test covers cache identity.
- **§2.1 `provider_keys` resolved from `houses.yaml`** — PARTIAL **(verified)**. The
  mechanism is correct (`resolve_provider_keys`, `core/config/houses.py:63-74`, keyed by
  env NAME, raises `KeyError(name)` never the value) and is tested — but it has **no
  production caller**. `grep` finds callers only in `tests/unit/config/test_houses.py`.
  `Settings.provider_keys` is `{}` at runtime; no house API key is reachable by any
  consumer.
- **§2.1 redaction helper + structlog processor** — MISSING **(verified)**. No
  `core/infra/` package; `grep -rn "def redact"` returns nothing repo-wide. The
  `*_KEY|*_TOKEN|*_PASSWORD|*_SECRET` processor and the
  `redact(value) -> "set (sha256:…)" | "missing"` helper are absent. AGENTS.md Rule 2
  is currently unenforced by code.
- **§2.1 no `os.getenv` for secrets outside settings** — DEVIATES. `cli/main.py:55`
  reads `os.environ["CORE_API_TOKEN"]` directly — a secret, read outside the settings
  module. (`core/config/checkconfig.py:19` also reads `os.environ`, but only for
  presence, which is legitimate.)
- **§2.2 `assignable`/`meta` mutually exclusive** — IMPLEMENTED + tested.
  `houses.py:41-45`; `test_houses.py:17-19`.
- **§2.2 ≥1 `enabled AND assignable`** — IMPLEMENTED + tested. `houses.py:52-56`;
  `test_houses.py:22-28`.
- **§2.2 every `api_key_env` present in `.env`** — PARTIAL. Not enforced at load;
  surfaced only as a checkconfig row (`checkconfig.py:44-55`) and via the uncalled
  `resolve_provider_keys`. The spec places this under `houses.py` validation.
- **§2.2 referenced model ids exist in `pricing.yaml`** — PARTIAL. Not enforced in
  `houses.py` (no access to pricing); only a checkconfig row (`checkconfig.py:31-42`).
  `reload_config` validates the three files independently and never cross-checks them
  (`core/config/store.py:45-49`), so a reload with an unpriced model succeeds.
- **§2.2 harness driver key known to the runner registry** — MISSING.
  `HarnessConfig.type` is a free `str` (`houses.py:19`); no driver registry exists. A
  typo'd `type: codx-cli` validates clean.
- **§2.2 houses upsert into the `houses` table** — MISSING **(verified)**. No upsert
  function, no `core/db/repo/houses.py`, no `fund bootstrap` command (`grep -rni
  bootstrap` over `*.py` returns nothing), no FastAPI startup/lifespan hook. The `House`
  table (`core/db/models.py:42`) is never populated from config, so insert-new /
  update-mirrored / vanished→`enabled=false`-never-deleted are all unimplemented.
- **§2.3 `queue.backoff` nested-vs-flat** — DEVIATES, **silent-fallback bug (verified)**.
  The YAML nests `backoff: { base_s, max_s, jitter }`; `QueueCfg` declares flat
  `backoff_base_s/max_s/jitter` (`core/config/fund.py:43-47`) and pydantic's default
  `extra="ignore"` drops the nested key. Reproduced: editing the template to
  `backoff: { base_s: 120, max_s: 60, jitter: 0.9 }` still loads as
  `backoff_base_s=30 backoff_max_s=3600 backoff_jitter=0.2`. **Operator backoff tuning
  is silently discarded.** The bug is masked because the code defaults happen to equal
  the template values.
- **§2.3 `lead_review.candidacy`** — MISSING **(verified)**. No field on `FundConfig`;
  dropped by `extra="ignore"` (`fund.py:54-66`). `hasattr(cfg, "lead_review")` is `False`.
- **§2.3 "every number is operator policy, not a code default"** — DEVIATES broadly.
  Only `RunPolicy.max_attempts` is required (`fund.py:20`). `budget_cap_usd`,
  `stage_timeout_s`, `verify_count`, all of `MonitorCfg` (including a hardcoded
  `house: str = "gpt"`), `AutoCrossCheck` thresholds, `QueueCfg` and `OrchestratorCfg`
  all carry code defaults, so a missing key falls back silently instead of failing
  checkconfig.
- **§2.3 exchanges / cadence / scheduler / retention** — PARTIAL. Typed as bare `dict`
  (`fund.py:58-66`); session strings, cron expressions and retention days get no shape
  validation at load.
- **§2.3 `verify_count` 1..3 bound** — MISSING. Plain `int`, no `ge=1, le=3` (`fund.py:23`).
- **§2.4 pricing resolver** — IMPLEMENTED. `price_call` (`core/config/pricing.py:35-51`)
  is the single price site; an unpriced model raises `KeyError` rather than silently
  costing zero. Note: `billable_input = max(input - cached, 0)` assumes providers report
  cached tokens *inside* the input count — an unstated convention worth pinning in the spec.
- **§2.4 `effective_from`** — MISSING. Not modeled on `ModelPrice` (`pricing.py:15-20`);
  dropped by `extra="ignore"`. No date-effective price selection, so a repricing cannot
  be staged ahead of its effective date.
- **§2.4 unpriced-at-attribution → `cost_source='estimated'`, `cost_usd=NULL`, desk
  alert** — MISSING. `price_call` raises; no caller implements the estimated-row-plus-alert
  path.
- **§2.5 frozen snapshot + atomic rebind** — PARTIAL. `core/config/store.py:18-38` gives
  a frozen dataclass and a single-reference rebind, which satisfies the concurrency shape.
- **§2.5 load = read → validate → rebind** — PARTIAL **(verified)**. `reload_config`
  takes YAML **strings**, not paths (`store.py:40`). Nothing in the repo opens
  `/etc/ai-fund/*.yaml` — grep for those filenames in `*.py` hits only docstrings. The
  "read" half of the pipeline does not exist, so **config is never loaded in production**;
  `get_config()` raises unless a test called `set_config`.
- **§2.5 invalid file leaves previous snapshot intact** — IMPLEMENTED by construction
  (all three loads complete before `set_config`, `store.py:45-50`) but untested — there
  is no `tests/unit/config/test_store.py`.
- **§2.5 SIGHUP / `fund config reload`** — MISSING. No `signal.SIGHUP` handler, no
  `config reload` subcommand.
- **§2.6 checkconfig checks** — PARTIAL: **4 of 12**. Implemented
  (`checkconfig.py:22-63`): `assignable_house`, `models_priced`, `api_keys_present`,
  `pm_user_ids`. Missing: config dir exists, `.env` is `0600`, settings parse, DB
  reachable, `alembic current == head`, `artifacts_dir` writable, dossier repo present
  and clean, `doctrine_dir` readable + `doctrine_versions` row for the current commit,
  market-data reachable.
- **§2.6 presence-only, never print the value** — IMPLEMENTED + tested.
  `checkconfig.py:52-54`; asserted in `test_checkconfig.py:59-63`.
- **§2.6 OK/WARN/FAIL table** — PARTIAL. `format_table` works (`checkconfig.py:71-75`)
  but no check ever emits `WARN`; the advisory tier is unused.
- **§2.6 `--strict`** — DEVIATES in the module entrypoint. `cli/main.py:264` is correct
  (non-zero only under `--strict`). But `checkconfig.py:78-81` `main()` ignores `--strict`,
  always exits non-zero on FAIL, and constructs `Settings(_env_file=None,
  core_api_token="x")` — a fabricated token that bypasses the real settings load. It is
  `pragma: no cover` and would misreport if invoked.
- **§2.6 `ExecStartPre` systemd wiring** — MISSING. No unit files in the repo reference
  checkconfig.
- **§2 test coverage** — PARTIAL. `tests/unit/config/` has only `test_settings.py`,
  `test_houses.py`, `test_checkconfig.py`. No `test_fund.py`, `test_pricing.py` or
  `test_store.py`: `price_call` arithmetic, the queue-backoff shape, and reload atomicity
  are all untested. The missing `test_fund.py` is precisely why the backoff bug survived.

### §2 gaps ranked by severity

1. **No production config loader.** Nothing reads `/etc/ai-fund/{houses,fund,pricing}.yaml`;
   `get_config()` raises outside tests. §2.5's read step and the whole startup path are absent.
2. **`env_file` hardcoded to `".env"`** — `AI_FUND_CONFIG_DIR` is ignored, so the operator
   `.env` is never read.
3. **`provider_keys` never populated** — no production caller; no house key reachable at runtime.
4. **Redaction entirely absent** — AGENTS.md Rule 2 unenforced by code, and `cli/main.py:55`
   reads a secret straight from `os.environ`.
5. **Dev defaults on required settings** — a misconfiguration becomes a silent
   wrong-target connection rather than a loud failure.
6. **`queue.backoff` silently discarded** — operator retry tuning has no effect.
7. **Houses→DB upsert missing** — the `houses` table is never reconciled with config.
8. **3 of 5 §2.2 validations not enforced at load**; harness driver-key validation absent entirely.
9. **checkconfig covers 4 of 12 checks** — the missing ones (`.env` mode, alembic head, DB
   and market-data reachability, dossier/doctrine) are the ones that gate a safe service start.
10. **Missing model fields**: `pricing.effective_from`, `fund.lead_review.candidacy`,
    `verify_count` 1..3 bound.

---

## §3 Run orchestrator

This is the section with the most serious findings. The headline: **the orchestrator loop
has no production caller.** `engine.tick` is referenced exactly once in the repo, from
`tests/unit/orchestrator/test_engine.py:179` **(verified)**. There is no orchestrator
process, no `JOBS` entry, no service entrypoint. Everything in §3.3 is exercised only by
unit tests.

### §3.1 Run creation

- **`create_run` insert + stage materialization** — IMPLEMENTED. `engine.py:42-114` plans
  stages then `create_run` + `add_stages` in one flush (`core/db/repo/runs.py:45-95`),
  with `depends_on_seq` chained. Production callers: `routes.py:193,439,608,774`,
  `jobs.py:107,130,205`.
- **(1) validate request against coverage state** — MISSING. `create_run` loads the
  coverage (`engine.py:59`) but never checks its state. An `initiation` on an `active`
  coverage, or a `deep_review` on `proposed`, is accepted. No test asserts rejection.
- **(2) config pinning — `doctrine_version_id`** — MISSING in practice **(verified)**.
  The parameter exists (`engine.py:53`, `runs.py:54`) but **no caller ever passes it** —
  grep across `routes.py`, `jobs.py` and `engine.py` finds only the definition and the
  default. Every run row is created with `doctrine_version_id = NULL`, so **no run is
  pinned to a doctrine version** and the audit/replay guarantee is unbacked.
  `budget_cap_usd` / `max_attempts` / `verify_count` pinning is real (`engine.py:97,67,72`).
- **(3) one transaction** — PARTIAL. `create_run` only `flush()`es; the transaction
  boundary is the caller's. Nothing enforces run+stages atomicity at the engine layer.
- **(4) emit outbox `run.created`** — MISSING **(verified)**. `grep -rn "run.created"`
  over `*.py` returns nothing. `finish_run` emits `run.finished` only (`runs.py:157`).
- **Idempotency / dedupe of concurrent identical runs** — PARTIAL. Idempotency rests
  solely on the partial unique index over `(coverage_id, type, trigger_ref)`
  (`models.py:218-226`, integration test `test_trigger_ref_unique.py`). Scheduler jobs
  pre-check with `_has_run` (TOCTOU — the index is the real guard), but
  `create_run` does **not** catch `IntegrityError`, so a genuine double-fire raises out
  of the job rather than no-op'ing. PM-triggered runs with no `trigger_ref` have no
  creation-time dedupe at all: two identical initiations both insert, the coverage lock
  serializes them at promote, and the loser sits `queued` forever.

### §3.2 Stage graphs / planner

- **Planner purity** — IMPLEMENTED. `planner.py` takes no session, does no I/O, is
  deterministic; 12 tests in `test_planner.py`.
- **All 7 run types have graphs** — IMPLEMENTED. `planner.py:50-96`; unknown type raises
  `PlanError`.
- **Contributor rotation** — DEVIATES; **the mechanism is inert.**
  `rotation.order_contributors` is only ever called with an **empty** `last_used_at` map
  (`planner.py:53,69`) — nothing queries prior stage rows for last use. Ordering
  collapses to alphabetical (`resolve_contributors` already returns `sorted(...)`,
  `coverage.py:353`), so **verify pairs calcify** — precisely the failure the spec calls
  out. `test_rotation.py` tests the pure helper only; deleting the planner's rotation
  call breaks no test. Fails the AGENTS.md fires-test rule.
- **Substrate rules** — PARTIAL. Substrates match the table except `event_analysis`,
  hardcoded to `harness` (`planner.py:77`) with no `severity=info` api-triage path. The
  "light model" requirement for `monitor_tick`/`pm_query` is not represented in `StageIn`.
- **`pm_query` role-prompt precondition** — MISSING. The spec requires `pm_query` runs to
  fail validation until `doctrine/roles/pm_query.md` exists. The file does not exist, and
  `POST /queries` (`routes.py:774`) creates the run anyway.

### §3.3 Orchestrator loop

- **`engine.tick()` production caller** — MISSING **(verified)**. See headline above.
- **(1) promote runs** — PARTIAL. `promote_run` (`engine.py:117-129`) does `start_run` +
  coverage transition and correctly returns `False` (stays `queued`, no backoff) when the
  coverage lock is held; tested at `test_engine.py:196`. But it does not check house
  budgets before promoting, and `tick` iterates queued runs in arbitrary order, **ignoring
  `runs.priority`**.
- **(2) claimability in the claim SQL** — IMPLEMENTED. `queue.py:36-60` enforces run
  `running`, `available_at <= now`, and the `depends_on_seq` `EXISTS`, under
  `FOR UPDATE SKIP LOCKED`. Covered by `test_queue_pg.py`.
- **(3) collect finished stages / `on_stage_finished`** — DEVIATES. No such function; the
  logic is inlined in `advance_run` (`engine.py:269-319`) and re-scans **all** stages of
  the run on every call rather than the newly-terminal ones. `advance_run` also calls
  `runner.drain(session)` (`engine.py:274`) — the engine **pushes** the runner
  synchronously, inverting the spec's pull model where runners claim independently.
  Re-entrancy is safe only because `_register_predictions` leans on
  `register_from_stage` idempotency and the cross-check has an explicit already-exists
  guard (`engine.py:196-201`); nothing else is guarded.
- **(4) reap expired leases** — MISSING from the loop **(verified)**. `queue.reap_expired`
  is implemented and tested (`queue.py:208`, `test_queue.py:163`,
  `test_review_paths.py:64`, `test_queue_pg.py:143`) but **`tick` never calls it** — grep
  finds no non-test caller. Lease expiry and stage-timeout detection therefore **never
  fire in production**. This is a fully-built, fully-tested safety mechanism with no
  production caller.
- **(5) transient re-entry (BUGS #2)** — PARTIAL. It does fire: `engine.py:299-309`
  requeues when a stage is `queued` with a future `available_at`, and
  `test_engine.py:154-181` fails if it is removed. But the trigger is "a backoff timer is
  pending", not "a stage failed retryably" — a stage requeued by the reaper, or any future
  `available_at`, requeues the run. And because `tick` promotes queued runs
  unconditionally, the run flips `queued→running→queued` every tick until backoff expires,
  overwriting `runs.error` with a synthetic string each pass.
- **(7) watchdog (`stage_timeout_s × 2` → desk alert)** — MISSING. No implementation, no
  test. `stage_timeout_s` is parsed into `RunPolicy` and never read by the orchestrator.
- **"one short transaction per item" / advisory lock** — MISSING. `tick` runs everything
  on one session with no per-item commit (`engine.py:338-347`), so a crash mid-tick loses
  the whole tick. `core/scheduler/leader.py` exists but `tick` never takes the advisory
  lock — two orchestrator processes would both promote and both drive runners.

### §3.4 `on_stage_finished`

- **Persist result / predictions** — PARTIAL. Predictions are registered
  (`engine.py:132-159`, asserted at `test_engine.py:57`). **Corrections (with
  attribution), disagreements, and the dossier commit row are not persisted at all** —
  no repo module exists for them and the engine never references them.
- **Dynamic cross-check append** — IMPLEMENTED, with two dead arms. `engine.py:175-225`
  + `cross_check.py`; `last_tp` is captured before registration so a run cannot suppress
  its own check (`engine.py:280-285`), and `test_engine.py:85-128` fails if it is removed.
  But `last_stance` and `event_severity` are always passed `None` (`engine.py:189-190`),
  so **two of the three spec triggers (stance change, `thesis`-severity event) can never
  fire** — only the ΔTP arm is live.
- **Attempt counting / `max_attempts`** — IMPLEMENTED, in the queue rather than the
  engine. `claim_stage` increments `attempts` (`queue.py:78`); `fail_stage` retries only
  while `attempts < max_attempts` (`queue.py:180`), else terminal. `reap_expired` applies
  the same rule. Tested at `test_queue.py:163`.
- **Terminal vs transient classification** — PARTIAL. `retryable` is a caller-supplied
  argument to `queue.fail_stage`; nothing in core maps an error to terminal-vs-transient,
  so classification belongs entirely to the (not-yet-existing) runner.
- **`fail_run` with last validation errors** — PARTIAL. `fail_run` (`engine.py:251-263`)
  sets status/error, releases the lock, transitions the coverage and emits `run.finished`
  (tested at `test_engine.py:67-82`). But **`RunStage.error` is never written by any
  producer** (`queue.fail_stage` stores `validation_errors` on the attempt row only), so
  `last_error` (`engine.py:291`) always resolves to the literal `"stage failed"` and the
  desk alert carries no validation detail.

### §3.5 Gates and budgets

- **Terminal gate `initiation_decision`** — IMPLEMENTED, via the coverage-SM side effect
  `open_gate:initiation_decision` (`coverage.py:182-193`) reached from `finalize_run`
  (`engine.py:231-246`); asserted at `test_engine.py:54-55`.
- **Terminal gates `lead_change` / `doctrine_amendment`** — MISSING **(verified)**.
  `finalize_run` special-cases `run.type == "initiation"` only; every other run type falls
  to the `else` branch and calls `finish_run(SUCCEEDED)` (`engine.py:247-248`). A
  `lead_review` or `distillation` run **never opens its gate and silently succeeds** — the
  PM decision the spec requires is skipped entirely.
- **`gates.terminal_gate` mapping (BUGS #3)** — DEVIATES **(verified)**. The mapping is
  correct (`gates.py:9-18`) but has **no production caller**; its only test
  (`test_section8_modules.py:33`) asserts the dict against itself. Deleting `gates.py`
  changes no runtime behavior. Passes "symbol exists + has a test", fails "has a
  production caller."
- **Per-run budget cap** — DEVIATES. The spec requires the check *before launching each
  stage attempt* (`cost + expected_stage_cost < cap`); the implementation checks *after*
  the runner has drained every claimable stage (`engine.py:313-315`). The existing test
  documents the overspend: `test_engine.py:131-151` shows a $0.02 cap running all four
  stages to $0.04 before pausing — **2× the cap**. The gate itself is correct
  (`budget_cap`, `["raise_cap","cancel"]`, run→`waiting_pm`).
- **`raise_cap` / `cancel` handling** — MISSING. Nothing consumes a `budget_cap` answer:
  no code writes a new cap into `runs.params` or re-queues/cancels the run (`grep
  raise_cap` finds only the `allowed_answers` literals). **A budget-paused run is
  permanently stuck holding its coverage lock.**
- **`budgets.py` module (BUGS #3)** — DEVIATES **(verified)**. `run_over_budget` /
  `pause_for_budget` have no production caller; `engine.py:313` and `engine.py:322-335`
  are a verbatim private duplicate (`_pause_for_budget`). Only
  `test_section8_modules.py:13` touches the module.
- **Per-house daily budgets** — MISSING. BUGS #4's "DEFERRED" understates the scope.
  Missing: reserve/settle under `SELECT … FOR UPDATE`, `available_at = next UTC midnight`
  on cap, run stays `queued`, and the once-per-house-per-day desk notice
  (`budget:{house}:{day}`). Only the `house_budget_days` table (`models.py:615`) and the
  config shape (`houses.py:23`) exist; **no code reads or writes the table.**
- **`answer_gate` idempotency** — PARTIAL. `runs.answer_gate` (`runs.py:193-211`) replays
  a same-key answer, but a **different** answer to an already-answered gate silently
  overwrites rather than returning 409. It also applies no side effects (coverage
  transition, branch merge, prediction status, outbox) — those live in `coverage_sm` on a
  separate path.

### §3 gaps ranked by severity

1. **No orchestrator process at all.** `engine.tick` has zero production callers. Nothing
   promotes, reaps, or watchdogs in a deployed system.
2. **`reap_expired` never called from `tick`** — lease-expiry and timeout detection are
   dead in production despite being fully implemented and tested.
3. **`lead_review` and `distillation` never open their terminal gates** — they complete
   as `succeeded`, skipping the required PM decision.
4. **`budget_cap` gate answers unhandled** — `raise_cap`/`cancel` do nothing; the run and
   its coverage lock are stranded permanently.
5. **Contributor rotation is inert** — `last_used_at` is never derived; verify pairs
   calcify exactly as the design principle warns.
6. **Budget check is post-hoc, not pre-attempt** — runs provably overspend their cap (2×
   in the existing test).
7. **`doctrine_version_id` never passed** — no run is pinned to a doctrine version.
8. **`create_run` skips coverage-state validation and never emits `run.created`** (§3.1
   steps 1 and 4).
9. **Cross-check stance and severity arms are hardcoded `None`** — only the ΔTP trigger
   can fire.
10. **Corrections / disagreements / dossier commit rows never persisted** on stage success.
11. **Dead-code duplication:** `budgets.py` and `gates.py` (BUGS #3) have no production
    callers; the engine keeps private copies of both.
12. **`RunStage.error` never written** — every failed run reports the literal
    `"stage failed"`.

---

## §4 Scheduler

**Correction to the prior truthing pass:** NOTES claims "2 of 9 jobs fully implemented,
5 skeletons." The real count is **4 real / 5 pending / 1 partial**: `session_tick`,
`watch_tick`, `retention` and `quarterly_sweep` are all real
(`jobs.py:85,115,147,175`). The shape of the claim was right; the count understated it.

- **Job registry** — PARTIAL **(verified)**. `JOBS` holds 7 cron jobs
  (`jobs.py:277-285`). `session_tick`, `watch_tick` and `schedule_watchdog` are **not in
  `JOBS`**, so `build_scheduler` can never register them (`scheduler.py:60-71`).
- **The 5 pending jobs** (`earnings_sweep`, `prediction_scoring`,
  `lead_review_candidacy`, `distillation`, `cost_rollup`) — MISSING, as
  `NotImplementedError` skeletons (`jobs.py:213,218,225,232,237`). Their only test
  asserts they *don't* work (`test_jobs.py:175`). This is the honest form of a stub —
  loud, not silent — and matches AGENTS.md rule 3.
- **§4(a) leader lock in every job** — PARTIAL. Present in the 4 real jobs
  (`jobs.py:95,123,155,184`), but `schedule_watchdog` explicitly skips it
  (`jobs.py:251`) and the 5 pending jobs raise before reaching it.
- **§4(b) start + finish `scheduler_job_logs` rows** — DEVIATES. Only `session_tick`
  writes a `started` row (`jobs.py:97`); `watch_tick` / `retention` / `quarterly_sweep`
  write finish-only (`jobs.py:140,171,209`). No test asserts start rows.
- **§4(c) deterministic `trigger_ref` idempotency** — IMPLEMENTED at the application
  level (`jobs.py:98,125,202`, guarded by `_has_run` at `jobs.py:46`; tests
  `test_jobs.py:67,150`). But the §4 partial unique index on
  `(coverage_id, type, trigger_ref)` is **not in the schema**, so dedupe is a
  read-then-write race rather than a DB constraint.
- **Cron registration from `fund.yaml`** — IMPLEMENTED. The prior pass's APScheduler
  claim is **confirmed**: `scheduler.py:51-72` registers each entry via
  `CronTrigger.from_crontab`, with an unknown-job guard tested at
  `test_scheduler.py:39`. Note `config/fund.yaml.example:28-33` wires only 5 of the 7
  (`earnings_sweep` and `lead_review_candidacy` are absent).
- **Calendar-driven session jobs (exchange pre/post)** — MISSING **(verified)**.
  `calendar.py` is inert: `exchange_sessions` / `all_exchanges` are called only from
  `test_section8_modules.py:50-52`. **Nothing parses `"pre:23:00"` into a trigger**, so
  no code path ever reaches `session_tick` or `watch_tick` in production — the two
  most-used jobs in §4 are dead code in a deployed system.
- **`build_scheduler` production caller** — MISSING **(verified)**. The only entrypoint
  is `fund = "cli.main:main"` (`pyproject.toml:39`); there is no core service entrypoint
  and no systemd unit in-repo. `start()` / `shutdown()` are never called. The
  `scheduler.py:7` docstring's claim that "systemd `ai-fund-core` calls them" is **false**
  — exactly the kind of aspirational docstring AGENTS.md rule 3 forbids.

### §4.4 Leader locks

- **`pg_try_advisory_lock` taken by default** — IMPLEMENTED. `jobs.py:30-39` defaults
  `is_leader=None`; `leader.py:18-23`. Signature-default guard at
  `test_spec_coverage.py:42` and `test_jobs.py:188`.
- **Fires-test proving a non-leader no-ops** — IMPLEMENTED, and it is a real one.
  `tests/integration/db/test_leader_lock_pg.py:51-85` runs against Postgres: the
  challenger gets 0 runs, the leader ≥1. Runs in CI (`.github/workflows/ci.yml:47`),
  though skipped in the default unit run. This one meets the AGENTS.md bar.
- **Lock release** — **MISSING — new bug, not previously logged (verified).** There is no
  `pg_advisory_unlock` anywhere in `core/` (grep confirms: `leader.py` only ever *takes*
  the lock). `run_job` opens a session per fire from a pooled `sessionmaker`
  (`scheduler.py:33`, `core/db/session.py:17-22`, default `QueuePool`). A PostgreSQL
  **session-level** advisory lock is held until explicitly unlocked or the *connection*
  closes — returning a connection to the pool does neither. So job #1 leaves the lock
  held on connection A forever; job #2, handed connection B, gets `False` from
  `pg_try_advisory_lock` and **silently no-ops — permanently**. The integration test
  masks this because it releases via an explicit `close()` in its own `finally`
  (`test_leader_lock_pg.py:89-92`). Nothing covers the sequential-jobs-through-`run_job`
  path. **Net effect once a core service exists: the scheduler runs exactly one job, then
  goes quiet.**
- **`ORCHESTRATOR_KEY` lock** — MISSING. `leader.py:14` defines it; it has zero call
  sites. `engine.tick` (`engine.py:338`) takes no lock.

---

## §7 Failure-mode behaviors

- **§7.1 worker crash → lease expiry → reaper requeue → fail after `max_attempts`** —
  PARTIAL. The logic exists (`queue.py:208-233`) and is tested
  (`test_queue_pg.py:143`), but `reap_expired` has **no production caller** — no reaper
  job in `JOBS`, no loop. The "fails visibly to the desk" outbox notice is absent. In a
  real deployment a crashed worker's stage stays `running` forever.
- **§7.2 retryable provider outage → `running`→`queued` with backoff, lock retained** —
  IMPLEMENTED at stage level (`queue.py:188-193`, `_next_available_at`). No test asserts
  the *coverage lock is retained* across the requeue.
- **§7.3 per-run cost cap → `waiting_pm` + `budget_cap` gate** — IMPLEMENTED (with the
  post-hoc timing caveat from §3.5). `budgets.py:18-36`, called from `engine.py:314`.
- **§7.3 per-house daily cap → stages unclaimable until midnight UTC + one desk notice
  (`budget:{house}:{day}`)** — MISSING entirely. No daily-cap code anywhere; `budgets.py`
  is 39 lines and handles only the per-run case. No dedupe key of that form exists.
- **§7.4 two mutating runs on one ticker → second stays `queued`** — IMPLEMENTED.
  Coverage-lock discipline in `runs.py:4` + `start_run`; covered by repo tests.
- **§7.5 two core processes → loser idles** — PARTIAL. The lock works for scheduler jobs,
  but with no core process entrypoint there is no process to idle, and the orchestrator
  half is unlocked entirely.
- **§7.6 PM never answers a gate** — PARTIAL. `waiting_pm` + the gate row persist and
  `fund gates-list` exists (`cli/main.py:81,217`), but there is no desk-digest surfacing
  of stale gates and no test that an unanswered gate appears in a digest.
- **§7.7 config edited mid-run → run keeps pinned params** — PARTIAL. Params are
  snapshotted into `runs.params` at creation, but no test reloads a mutated `FundConfig`
  mid-run and asserts the in-flight run is unaffected.
- **§7.8 scheduler dies → `schedule_watchdog` alerts; missed-tick check on boot** —
  MISSING. `schedule_watchdog` (`jobs.py:242`) computes a stale list and **returns it** —
  it emits no desk alert, and it has no caller outside `test_jobs.py:120`. It is in
  neither `JOBS` nor `fund.yaml.example`, so **it can never be scheduled**. No boot-time
  missed-tick check exists. This is v2's silent-scheduler-death lesson, unimplemented.

### §4 / §7 gaps ranked by severity

1. **No core service entrypoint** — `build_scheduler` and `engine.tick` both have zero
   production callers. Nothing in a deployed system fires any job. Everything below is
   downstream of this.
2. **The advisory lock is never released** — with pooled connections the scheduler goes
   permanently non-leader after the first job. Needs `pg_advisory_unlock` in a `finally`
   (or a single long-lived leader connection), plus a test that drives two jobs in
   sequence through `run_job` and asserts both ran.
3. **`schedule_watchdog` emits no alert and is unschedulable** — the entire point of §7.8
   is missing. Add it to `JOBS`, write an outbox notice, and test that the notice row
   appears.
4. **`session_tick` / `watch_tick` are unreachable** — `calendar.py` never converts
   `"pre:23:00"` into a trigger and nothing registers these two jobs.
5. **Per-house daily cap (§7.3)** entirely absent — no cap, no midnight-UTC unclaimable
   window, no `budget:{house}:{day}` dedupe notice.
6. **`reap_expired` has no caller (§7.1).**
7. **Missing partial unique index** on `(coverage_id, type, trigger_ref)` — idempotency
   is advisory only; two concurrent fires can both pass `_has_run`.
8. **`test_spec_coverage.py` guards are weak.**
   `test_scheduler_wiring_exists_and_is_callable` (`:36`) asserts only importability, and
   `test_every_section4_cron_job_is_registered` (`:27`) passes with all 5 jobs still
   raising `NotImplementedError`. Neither fails if the mechanism stops working, so by the
   AGENTS.md definition-of-done they do not count as coverage. This is the spec-coverage
   pattern degrading into the very box-ticking it was introduced to prevent.
9. **Doc drift** — `scheduler.py:7` and NOTES assert systemd wiring that does not exist.

---

## §5 Core API

**BUGS #1/#5 claims hold up.** The router exposes exactly 26 `(method, path)` pairs,
`ROUTE_COMMAND_MAP` has 26, and the set difference is empty both ways. The three deferred
routes are genuinely absent and carry reasons (`test_spec_route_parity.py:37,42,44`).
Importantly, the **read models are real, not stubs** — every read route issues a genuine
`select()` against a real table (`routes.py:155,466,565,634,654,682,749`); none returns a
hardcoded `[]`. That part of the recent work is sound.

The problems are in §5.2 cross-cutting, and one is severe.

### §5.2 — the severe one

- **Bearer token on read routes** — **MISSING (verified empirically).** The spec requires
  read routes to require the bearer token. No read route has any auth dependency, and
  `create_app` includes the router with no global dependency (`app.py:62`). Driving the
  in-process app with **no `Authorization` header at all**:

  ```
  /coverage    -> 200      /predictions -> 200      /costs  -> 200
  /runs        -> 200      /events      -> 200      /health -> 200
  /gates       -> 200
  ```

  **The entire read surface — coverage, runs, gates, predictions, events, costs, config
  check — is open to anything that can reach the socket.** `require_pm` (`deps.py:41`) is
  the only thing that checks the token, and it is on mutations only. The existing tests
  assert the 403-on-mutation path (`test_api.py:26`) but nothing asserts a read route
  rejects a missing or bad token, so the absence is invisible to CI.

### §5.1 Routes

- **`GET /gates?state=`** — DEVIATES; **live functional bug (verified).** The param is
  declared `state_: str = Query(default="open")` with **no `alias="state"`**
  (`routes.py:508`), so the public query param is literally `state_`. Confirmed by
  reflection: `list_gates` alias is `state_` while `list_runs` correctly uses
  `alias="status"` (`routes.py:464`). `cli/client.py:115` sends `?state=…`, which is
  silently dropped — **`fund gates-list` can only ever see open gates.**
- **`POST /coverage/{slug}/promote` "also spawns `deep_review`"** — MISSING. The route
  only transitions (`routes.py:270`); the `spawn_deep_review` side effect emitted by the
  state machine (`coverage_sm.py:151`) is an explicit no-op in the repo
  (`repo/coverage.py:209`) and no caller handles it. **Promotion silently drops a specced
  run**, with no deferral record.
- **Query-param completeness** — PARTIAL. Missing: `GET /coverage ?exchange=`,
  `GET /runs ?type=&coverage=&limit=`, `GET /events ?since=`, `GET /costs ?since=` and
  `by=day` (which 400s at `routes.py:748`). Worse, `GET /predictions ?coverage=` takes a
  **UUID** (`routes.py:649`) while every other coverage-addressed route and the CLI use
  the dossier slug — so `fund predictions-list --coverage tse_2267` will 422 against its
  own API.
- **`GET /health` shape** — PARTIAL. Spec wants db, queue depth, leader, last scheduler
  success; returns `{status, queue_pending: bool}` (`routes.py:549-554`) — no depth, no
  leader, no scheduler timestamp, and no DB probe distinct from the query itself.
- **`GET /coverage/{slug}/dossier` shape** — PARTIAL. Spec wants index row + file list +
  stance/TP + staleness; returns index fields only (`routes.py:637-644`).
- **`POST /events/{id}/rate`** — DEVIATES. Spec's vocabulary is `{rating: up|down}`; the
  API takes `rating: int` (`routes.py:701`) and the CLI `choices=["1","-1"]`
  (`cli/main.py:152`). The adapter will have to translate 👍/👎 twice.

### §5.2 — the rest

- **PM allowlist on mutating routes** — IMPLEMENTED. `require_pm` is on all 12 mutating
  routes; no mutating route omits it. Allowlist comes from config, not code
  (`app.py:33`, `deps.py:50`).
- **`Idempotency-Key`** — IMPLEMENTED, and properly. On every mutating route; missing key
  → 400 (`deps.py:91`), replay returns the stored body verbatim (`:100`), same-key
  different-body → 409 (`:96`). Tests are removal-sensitive
  (`test_api.py:35,49,71`). This meets the AGENTS.md bar.
- **`audit_log` on every mutation** — IMPLEMENTED. `write_audit(...)` with
  `request.state.request_id` in all 15 mutating handlers, and replay short-circuits
  *before* the audit write so there is no double row (asserted at `test_api.py:63-67`).
- **Error envelope** — IMPLEMENTED. `{"error": {code, message, request_id}}`
  (`errors.py:26`), wired to both an `ApiError` handler and a catch-all 500
  (`app.py:50-58`).
- **Request id in every response** — PARTIAL. Present as the `X-Request-Id` header
  (`app.py:47`) and in error bodies, but absent from every success body. And the
  unhandled-500 handler passes `""` instead of `request.state.request_id`
  (`app.py:58`) — the one case where a caller most needs the correlation id is the one
  case that lacks it.
- **Pagination** — MISSING. Ad-hoc hardcoded caps (predictions `.limit(200)`, events
  `.limit(100)`); `/coverage`, `/runs`, `/costs` are unbounded with no cursor or offset.

---

## §6 `fund` CLI

- **Command coverage vs the spec list** — PARTIAL. Five specced commands are absent.
  Three (`fund track-record`, `fund query keep`, `fund run artifacts`) follow their
  deferred routes and are legitimately tracked. But **`fund bootstrap` and
  `fund config reload` have no deferral record anywhere** (`grep -rn bootstrap
  --include=*.py` → zero hits repo-wide) and no backing route — so they are invisible to
  every guard, because the parity test is route-anchored and these two have no route.
- **Commands call the API, not stubs** — IMPLEMENTED. All 26 dispatch branches
  (`cli/main.py:167-246`) call a real `CoreClient` method; `_call` is a genuine httpx
  request with `Authorization`, `X-PM-User` and a minted `Idempotency-Key`
  (`cli/client.py:40-53`).
- **"Human table by default, `--json` for scripting"** — DEVIATES **(verified)**. `_emit`
  (`cli/main.py:60-69`) prints `json.dumps` in **both** branches: for a dict the two paths
  are byte-identical, and the default merely prints one JSON object per line for lists.
  **`--json` is a no-op.** `cli/format.py:11 table()` has **zero production callers**; its
  only importer is `test_section8_modules.py:7`, which asserts the module exists. Dead
  code guarded by a test of its existence rather than its use — the §8 pattern again.
- **Exit code 2 for gate/validation refusal** — DEVIATES, inverted. `run()` returns 1 for
  every `CoreError` including 409/422 (`cli/main.py:249`); 2 is returned only when an
  unrecognized command falls off the end (`:265`) — the opposite of the spec's meaning.
- **No direct DB access** — IMPLEMENTED, with one documented carve-out: `fund checkconfig`
  imports `core.config.*` directly (`cli/main.py:251-264`) as a local preflight. Not a DB
  read; reasonable.
- **Command naming** — DEVIATES (cosmetic). Spec uses subcommand groups
  (`fund coverage list`, `fund run show`); the implementation uses flat hyphenated names
  (`coverage-list`, `run-show`). Worth a spec amendment either way so the two agree.

### The parity test itself

- **Does it test what it claims?** — PARTIAL, and worth being precise about. It genuinely
  enforces four directions and would fail on an added, removed, or silently-implemented
  route (`test_spec_route_parity.py:71-98`). Real value; BUGS #5 was not overclaimed.
- **Can it catch a stub?** — **No, by construction.** It reflects only `(method, r.path)`
  off the router (`:58-68`), so a handler body of `return []` passes every assertion.
  Likewise `test_every_mapped_command_is_a_registered_subcommand` (`:101`) checks that the
  **argparse subparser exists**, not that `run()` has a dispatch branch — a command
  registered in `build_parser` but missing from `run()` would fall through to `return 2`
  and the test would still pass. **No test invokes `main()` or `run()` for any command.**

### §5 / §6 gaps ranked by severity

1. **Read routes are entirely unauthenticated** (`app.py:62`) — verified: all seven read
   paths 200 with no token. Fix: `include_router(..., dependencies=[Depends(require_token)])`
   plus a test that a read route 401s without a token.
2. **`GET /gates?state=` is silently ignored** (`routes.py:508`, missing `alias="state"`)
   — gate filtering is inert and `fund gates-list` can never show non-open gates.
3. **`promote` never spawns `deep_review`** (`routes.py:270`, no-op at
   `repo/coverage.py:209`) — a specced durable side effect dropped with no deferral record.
4. **The parity test cannot detect a stub or an undispatched command.** Add (a) a smoke
   test driving `cli.main.run()` for each of the 26 commands against the in-process app,
   asserting no `return 2` and no exception, and (b) per-route assertions that read models
   return seeded rows rather than `[]`.
5. **`fund bootstrap` and `fund config reload` missing with no deferral record** — the two
   specced §6 commands invisible to every guard.
6. **`cli/format.py` is dead and `--json` is a no-op** — either wire `table()` into
   `_emit`'s non-JSON branch with per-command column sets, or drop the module and the
   spec's "human table by default" claim.
7. **`GET /predictions?coverage=` takes a UUID, not a slug** — guaranteed 422 from the
   CLI's own flag.
8. **CLI exit codes inverted** — map 409/422 → 2, transport/5xx → 1.
9. **`request_id` missing from success bodies; the 500 handler emits `""`.**
10. **Query-param and shape gaps** — `/coverage?exchange`, `/runs?type&coverage&limit`,
    `/events?since`, `/costs?since&by=day`, dossier file list + staleness, health
    leader/queue-depth/scheduler timestamp.
