# SPEC-CORE — Orchestrator, config, core API, CLI

**Phase:** 1.2 (SPEC) · **Implements:** PROMPTS.md 1.2 · **Branch:** `spec-core`
**Status:** ready for BUILD (after `spec-domain` merges)
**Depends on:** SPEC-DOMAIN (models, repositories, queue, state machines)
**Consumed by:** SPEC-RUNNER (claims the stages this spec queues), SPEC-INITIATION,
SPEC-MONITORING, SPEC-ADAPTER (thin client of the API), SPEC-TRACKREC, SPEC-DISTILLATION

Self-contained: a builder implements it without reading `design/`. Where it references
SPEC-DOMAIN, the section number is given.

---

## 1. Purpose and scope

The **core service** is the only thing that changes state. It owns:

1. **Run orchestrator** — create run → expand run type into stages → queue → collect
   results → apply gates → PM-gate states → finish. Includes the `running → queued`
   transient-failure re-entry.
2. **Scheduler** (APScheduler, in-process) — every recurring job emits runs/ticks
   through the same queue.
3. **Config** — `/etc/ai-fund/{houses,fund,pricing}.yaml` + `.env`, loaded through
   pydantic-settings, validated by `checkconfig`, **outside the deploy tree**.
4. **Core API** (FastAPI, localhost, authenticated) — coverage lifecycle, runs,
   decisions/gates, track-record, costs, queries.
5. **`fund` CLI** — mirrors every route (design principle: everything chat can do, the
   CLI can do; chat is an adapter over this API, never the source of truth).

### Out of scope

- **Stage execution** (phase 2, SPEC-RUNNER). This spec queues stages and consumes their
  results; it never launches a harness or calls a provider. For phase 1.3, a
  `FakeRunner` (§9.1) claims stages, writes a canned `stage_result.yaml`, and marks them
  succeeded — the orchestrator, gates, budgets, and PM-gate flow must be **fully
  testable without any LLM**.
- Metric math (SPEC-TRACKREC), news/tripwire logic (SPEC-MONITORING), Mattermost
  (SPEC-ADAPTER), risk/trades (phase 7.2 port; routes reserved in §6.8).

### Processes

| Process | Contains | systemd unit |
|---|---|---|
| `core` | FastAPI app + orchestrator loop + APScheduler | `ai-fund-core` |
| `runner` | stage workers (phase 2) | `ai-fund-runner` |
| `adapter` | Mattermost ws/REST (phase 5) | `ai-fund-adapter` |

Single host, Postgres, no Redis. `core` may be run as a single instance today; the
leader locks in §4.4 make a second instance safe rather than catastrophic.

---

## 2. Configuration (`/etc/ai-fund/`)

**Rule (AGENTS.md 1, 4; design/05 §6):** operator config and secrets live outside the
deploy tree so a `git reset` can never clobber them. **All** secrets flow through the
settings module. No code calls `os.getenv` for a secret, opens `.env`, or hardcodes a
model name, price, budget, or threshold.

```
/etc/ai-fund/
  .env            # secrets only, chmod 0600, root:ai-fund
  houses.yaml     # model-house registry
  fund.yaml       # books, exchanges/sessions, cadences, run policy, thresholds, schedules
  pricing.yaml    # per-model token prices
```

Repo-tracked templates: `.env.example` (exists) and `config/houses.yaml.example`,
`config/fund.yaml.example`, `config/pricing.yaml.example` (this spec adds them —
resettable, non-secret, placeholder values only).

### 2.1 `core/config/settings.py` — pydantic-settings

```python
class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=os.environ.get("AI_FUND_CONFIG_DIR", "/etc/ai-fund") + "/.env",
        env_file_encoding="utf-8", extra="ignore", frozen=True,
    )
    config_dir: Path = Path("/etc/ai-fund")          # AI_FUND_CONFIG_DIR
    database_url: PostgresDsn
    artifacts_dir: Path
    dossier_repo_path: Path                          # bare repo or working clone root
    doctrine_dir: Path                               # read-only mount source for stages
    core_api_url: AnyHttpUrl = "http://127.0.0.1:8080"
    core_api_token: SecretStr                        # CLI/adapter → core auth
    pm_user_ids: list[str] = []                      # allowlist for mutating routes
    market_data_url: AnyHttpUrl                      # localhost market-data service
    search_endpoint: AnyHttpUrl | None = None
    provider_keys: dict[str, SecretStr] = {}         # keyed by the env NAME in houses.yaml
    log_level: str = "INFO"
    env: Literal["dev", "staging", "prod"] = "dev"
```

- Provider keys are **not** enumerated in code. `houses.yaml` names the env var
  (`api_key_env: OPENAI_API_KEY`); the settings module resolves it once at load into
  `provider_keys[name]` as a `SecretStr`. Adding a house never touches Python.
- `get_settings()` is an `lru_cache`d factory; tests override via
  `Settings(_env_file=None, **overrides)` — never by writing a `.env`.
- **Redaction:** `core/infra/logging.py` (ported, phase 1.4) installs a structlog
  processor that redacts any value of a key matching `*_KEY|*_TOKEN|*_PASSWORD|*_SECRET`
  and any `SecretStr`. `redact(value) -> "set (sha256:ab12…)" | "missing"` is the only
  way a secret's *presence* is ever shown (AGENTS.md Rule 2).

### 2.2 `houses.yaml`

```yaml
houses:
  gpt:
    display_name: GPT
    provider: openai
    api_key_env: OPENAI_API_KEY
    base_url_env: OPENAI_BASE_URL        # optional
    models:
      heavy: "<operator fills in>"       # model ids live HERE, never in code
      light: "<operator fills in>"
    harness:
      type: codex-cli                    # driver key (SPEC-RUNNER §3)
      config: { }                        # driver-specific
    budgets:
      daily_usd: 25
      per_run_usd: 40
    assignable: true
    enabled: true
  claude:
    display_name: Claude
    provider: anthropic
    api_key_env: ANTHROPIC_API_KEY
    models: { heavy: "<operator fills in>" }
    harness: { type: claude-code }
    assignable: false                    # PM decision: Claude is meta, not an analyst
    meta: true
    enabled: true
```

Validation (`core/config/houses.py`, pydantic): `assignable` and `meta` are mutually
exclusive; at least one `enabled AND assignable` house; every `api_key_env` must be
present in `.env`; every referenced model id must exist in `pricing.yaml`; a house with
harness stages must name a driver key the runner registry knows.

On core startup (and on `fund bootstrap`), houses.yaml is upserted into the `houses`
table (SPEC-DOMAIN §4.1): new keys inserted, mirrored columns updated, vanished keys set
`enabled=false` — **never deleted** (track records reference them).

### 2.3 `fund.yaml`

```yaml
books: [main]                            # deterministic ledger books (phase 7.2)
timezone: UTC
exchanges:
  tse:  { sessions: ["pre:23:00", "post:06:10"], calendar: jpx }   # UTC times
  hkex: { sessions: ["pre:01:00", "post:08:10"], calendar: hkex }
  nyse: { sessions: ["pre:13:00", "post:21:10"], calendar: nyse }
cadence:
  active: every_session                  # monitor_tick per exchange session
  watch:  daily                          # daily | weekly (news-only)
runs:
  initiation:
    verify_count: 2                      # 1..3; PM may override per run
    include_data_checker: false          # optional cheap numbers-only pass
    max_attempts: 3
    budget_cap_usd: 40
    stage_timeout_s: 10800
  deep_review:   { max_attempts: 3, budget_cap_usd: 20, stage_timeout_s: 7200 }
  event_analysis:{ max_attempts: 2, budget_cap_usd: 10, stage_timeout_s: 5400 }
  monitor_tick:  { max_attempts: 2, budget_cap_usd: 0.10, stage_timeout_s: 300 }
  pm_query:      { max_attempts: 2, budget_cap_usd: 0.50, stage_timeout_s: 300 }
  lead_review:   { max_attempts: 2, budget_cap_usd: 15, stage_timeout_s: 3600 }
  distillation:  { max_attempts: 2, budget_cap_usd: 15, stage_timeout_s: 10800 }
monitor:
  house: gpt                             # which house's LIGHT model scores news
  digest_min_severity: info
  digest_max_items: 10
escalation:                              # SPEC-MONITORING owns the semantics
  auto_cross_check: { tp_change_pct: 10, stance_change: true, thesis_tripwire: true }
lead_review:
  candidacy: { consecutive_misses: 3, corrections_rate_vs_median: 2.0 }
scheduler:
  prediction_scoring: "15 2 * * *"
  quarterly_sweep:    "0 3 1 */3 *"
  distillation:       "0 4 1 * *"
  cost_rollup:        "30 1 * * *"
  retention:          "0 5 * * 0"
retention:
  transcripts_days: 365
  news_days: 180
  idempotency_days: 30
queue:
  lease_seconds: 300
  backoff: { base_s: 30, max_s: 3600, jitter: 0.2 }
orchestrator:
  tick_seconds: 5
```

Every number above is **operator policy**, not a code default. Code reading a missing
key is a `checkconfig` failure, not a silent fallback.

### 2.4 `pricing.yaml`

```yaml
models:
  "<model-id>":
    input_per_mtok: 0.00
    output_per_mtok: 0.00
    cached_input_per_mtok: 0.00
    currency: USD
    effective_from: 2026-07-01
```

`core/config/pricing.py` resolves `(model, tokens) -> Decimal` and is the **only** place
a price exists. An unpriced model is a hard error at `checkconfig` and at cost
attribution time (the usage row is written with `cost_source='estimated'`,
`cost_usd=NULL`, and a desk alert fires — never a silently-wrong zero).

### 2.5 Config store and reload

`core/config/store.py` exposes `get_config() -> FundConfig` (a frozen pydantic model
holding houses + fund + pricing). Load = read → validate → **atomic rebind** of one
module-level reference. Readers get a consistent immutable snapshot; no lock needed, no
partial state. `SIGHUP` (or `fund config reload`) re-reads and rebinds; an invalid file
leaves the previous snapshot in place and logs the error.

**Runs pin their config**: at run creation the orchestrator resolves `verify_count`,
`max_attempts`, `budget_cap_usd`, the house/model selection, and the doctrine version,
and writes them into `runs.params` / stage rows. A mid-run config edit cannot change a
run's contract (and cannot retroactively alter an old run's attribution).

### 2.6 `checkconfig` (ported from v2 `scripts/checkconfig.py`, adapted to houses)

`fund checkconfig [--strict]` — also `ExecStartPre` for the systemd units.

Checks: config dir exists and `.env` is `0600`; settings parse; every `api_key_env`
present and non-empty (**presence only — the value is never printed or logged**); every
house model priced; DB reachable and `alembic current == head`; `artifacts_dir`
writable; dossier repo present and clean; `doctrine_dir` readable and a `doctrine_versions`
row exists for its current commit; market-data service reachable; at least one
`enabled AND assignable` house; `pm_user_ids` non-empty.

Output is a table of `OK / WARN / FAIL` per check. `--strict` exits non-zero on any FAIL
(gates service start). **If a key is reported missing, the operator fixes `.env`; the
agent never reads it** (AGENTS.md Rule 6).

---

## 3. Run orchestrator

`core/orchestrator/` — `planner.py` (pure), `engine.py` (the loop), `budgets.py`,
`gates.py`.

### 3.1 Run creation

```python
create_run(type, *, coverage_id=None, trigger, trigger_ref=None, params={},
           requested_by=None, priority=None) -> Run
```

1. Validate the request against the coverage state (e.g. `initiation` requires
   `proposed` or `failed`; `deep_review`/`event_analysis` require `active` or `watch`).
2. Resolve config → `budget_cap_usd`, `max_attempts`, `verify_count`, substrate defaults,
   the current `doctrine_version_id`, and the house assignments (§3.2).
3. Insert `runs` (status `queued`) + its `run_stages` (status `queued`,
   `depends_on_seq` chained) in one transaction. Stage rows are **fully materialized at
   creation** (except dynamically-appended stages, §3.4).
4. Emit outbox `run.created`.

### 3.2 Stage graphs (the planner is a pure function)

`plan_stages(run_type, coverage, config, params, houses) -> list[StageIn]`

| Run type | Stages | Houses | Substrate |
|---|---|---|---|
| `initiation` | 1 author · 2..(1+N) verifier ×`verify_count` · [data_checker] · last finalizer | lead; verifiers = rotating contributors; finalizer = lead | harness |
| `deep_review` | 1 author(update) · 2 verifier | lead; verifier = next contributor in rotation | harness |
| `event_analysis` | 1 analyst(author role, event scope) [· 2 cross_check appended dynamically, §3.4] | lead; cross-check = a contributor | harness (api if severity=`info` and config allows — **triage-only**: an api attempt that concludes anything but `nothing_material` is re-planned on harness, SPEC-MONITORING §8) |
| `monitor_tick` | 1 monitor | `fund.yaml monitor.house`, **light** model | api |
| `pm_query` | 1 pm_query | lead house, **light** model | api |
| `lead_review` | one `lead_review` stage **per contributor** (ballots), evaluated in parallel | all enabled assignable contributors | api (harness if configured) |
| `distillation` | 1 distiller | the `meta` house (Claude) | harness |

- **Contributor rotation** (`core/orchestrator/rotation.py`): contributors for a coverage
  are ordered by `(last_used_at ASC, key ASC)` from their prior stage rows on that
  coverage, so verify pairs do not calcify (design principle). The chosen set is written
  into the stage rows — rotation state is *derived*, never stored as a cursor.
- Verify stages are **parallelizable** but default to sequential (`depends_on_seq = seq-1`)
  because verifier 2 re-pins the price and re-runs reconciliation over verifier 1's
  corrections. `initiation.parallel_verify: false` in config; leave it false until the
  2.4 pilot says otherwise.
- The `data_checker` role is inserted only when `include_data_checker: true`.
- **Amendment to SPEC-DOMAIN §3:** add `PM_QUERY = "pm_query"` to `StageRole`. It needs a
  role prompt at `doctrine/roles/pm_query.md`; the text is proposed in §10 and requires
  PM approval (doctrine is PM-owned). Until it exists, `pm_query` runs fail
  `checkconfig`-style validation at creation rather than running promptless.

### 3.3 Orchestrator loop (`engine.tick()`, every `orchestrator.tick_seconds`)

Each tick, in one short transaction per item (never one giant transaction):

1. **Promote runs**: for each `queued` run whose coverage has no conflicting lock and
   whose house budgets are not exhausted → `start_run()` (SPEC-DOMAIN §8): acquire
   `coverage_run_locks` (mutating runs only), set `running`, `started_at`, transition the
   coverage (`proposed → initiating` for initiations). If the lock is held, the run stays
   `queued` — this is **not** an error and is not retried with backoff; it is re-tried
   next tick.
2. **Stage readiness needs no orchestrator step**: claimability is enforced entirely
   inside the claim SQL (SPEC-DOMAIN §6.3) — run `running`, `available_at` reached, and
   the `depends_on_seq` dependency `succeeded`/`skipped` (an `EXISTS` in the query). The
   orchestrator neither "releases" stages nor can it race the runner over readiness; its
   only stage-visibility lever is promoting the run to `running`.
3. **Collect finished stages**: for each stage that reached a terminal status since the
   last tick, run `on_stage_finished()` (§3.4).
4. **Reap**: `queue.reap_expired(now)` — expired leases → requeue with backoff or fail.
5. **Transient re-entry**: a stage that failed with a `retryable` error (provider outage,
   sandbox launch failure, lease loss) and still has attempts left puts its **run** back
   to `queued` (`runs.status running → queued`, SPEC-DOMAIN §6.1) with
   `runs.error = <reason>`; the run re-enters at the current stage boundary — completed
   stages are never redone. The coverage lock is **retained** across this edge (the run
   is still "in flight").
6. **Budget sweep**: any `running` run whose `cost_usd > budget_cap_usd` → pause at the
   next stage boundary (§3.5).
7. **Watchdog**: any `running` run with no stage activity for `stage_timeout_s × 2` →
   desk alert (does not auto-fail; the runner owns kills).

### 3.4 `on_stage_finished(stage)`

```
succeeded →
    persist: result (validated stage_result), predictions (register_from_stage),
             corrections (with attribution resolution), disagreements, dossier commit row
    dynamic append: if run.type == event_analysis and stage.role == author and
                    auto_cross_check_rule(result, config) → append cross_check stage
                    (seq = max+1, house = next contributor, depends_on_seq = this.seq)
    if more stages remain → next stage becomes claimable
    else → finish_run(): terminal gate or success
failed (terminal, attempts exhausted) →
    fail_run(): status=failed, error, release lock, coverage transition if initiating,
                outbox run.finished (desk alert with the last validation errors)
```

`auto_cross_check_rule` (design/03 §2.3, thresholds from `fund.yaml escalation`):
append a cross-check when **|ΔTP| > tp_change_pct** vs the last registered
`target_price` for that coverage, **or** stance changed, **or** the triggering event's
severity is `thesis`. The rule reads *registered predictions and the event row* — both
system-owned data — not free text from the model (a model cannot suppress its own
cross-check by writing prose; it can only trigger one by changing a registered value).

### 3.5 Gates and budgets

**Terminal gates** (run → `waiting_pm`, exactly one open `pm_gates` row):

| Run type | Gate kind | `allowed_answers` |
|---|---|---|
| `initiation` | `initiation_decision` | `active` · `watch` · `reject` |
| `lead_review` | `lead_change` | `approve` · `keep` |
| `distillation` | `doctrine_amendment` | `merge` · `reject` |
| any (cap hit) | `budget_cap` | `raise_cap` · `cancel` |

Answering (`answer_gate`, SPEC-DOMAIN §8) is idempotent (unique `idempotency_key`) and
applies the answer's side effects in **one transaction**: coverage transition + branch
merge decision + prediction status + audit + outbox. A second identical answer replays
the stored response; a *different* answer to an answered gate is a 409.

**Budgets:**
- *Per run*: before launching each stage attempt, the orchestrator checks
  `run.cost_usd + expected_stage_cost < budget_cap_usd`. On breach → run `waiting_pm`
  with a `budget_cap` gate (spend so far, remaining stages, cap). `raise_cap` sets a new
  cap in `runs.params` (audited) and re-queues; `cancel` cancels the run. **The run never
  silently degrades** (design/05 §9).
- *Per house per day*: `house_budget_days` (SPEC-DOMAIN §4.21) reserve/settle under
  `SELECT … FOR UPDATE`. A house at its daily cap makes its stages unclaimable
  (`available_at = next UTC midnight`); the run stays `queued`, and the desk gets **one**
  notice per house per day (dedupe key `budget:{house}:{day}`).
- Reservation amount = the run's `per_run` remaining cap share; settlement = actual
  `stage_attempts.cost_usd` (including failed attempts).

---

## 4. Scheduler (APScheduler, in `core`)

`core/scheduler/jobs.py`. Every job: (a) takes a **leader advisory lock** (§4.4),
(b) writes `scheduler_job_logs` start/finish rows, (c) creates runs through
`create_run()` with a deterministic `trigger_ref` so a double-fire is idempotent,
(d) never calls a provider directly.

| Job | Schedule | Emits |
|---|---|---|
| `session_tick:<exchange>` | per exchange session (fund.yaml `exchanges`) | `monitor_tick` runs for every `active` coverage on that exchange |
| `watch_tick` | daily (or weekly) | `monitor_tick` runs for `watch` coverage (news-only mode flag in params) |
| `earnings_sweep` | daily | `deep_review` for coverage with an earnings release since the last sweep |
| `quarterly_sweep` | cron | `deep_review` for `active` coverage with no review in N days |
| `prediction_scoring` | daily | scores open predictions (SPEC-TRACKREC owns the math) |
| `lead_review_candidacy` | weekly | flags leads breaching `fund.yaml lead_review.candidacy`; desk notice (never auto-spawns a heavy run) |
| `distillation` | monthly | one `distillation` run (meta house) |
| `cost_rollup` | nightly | reconciles metered vs provider-reported spend; desk alert on drift > threshold |
| `retention` | weekly | deletes expired `idempotency_keys`, old `news_articles`, transcripts past `retention.transcripts_days` |
| `schedule_watchdog` | every 15 min | any job whose last success is older than its interval × 2 → desk alert (v2's silent-scheduler-death lesson) |

Idempotency: `trigger_ref = f"{job}:{exchange}:{trading_date}:{session}"`; `create_run`
refuses a duplicate (unique on `(coverage_id, type, trigger_ref)` where trigger_ref is
not null — **add this partial unique index to SPEC-DOMAIN §4.7's `runs` table**). A
scheduler restart that re-fires a job creates no second tick.

### 4.4 Leader locks

Two Postgres advisory locks (`pg_try_advisory_lock`): `orchestrator` and `scheduler`. A
second `core` process gets `False` and idles as a warm standby instead of double-firing
jobs or double-promoting runs. This is the only concurrency guard the core process needs
beyond the DB row locks in SPEC-DOMAIN.

---

## 5. Core API (FastAPI)

Bound to `127.0.0.1` only. `Authorization: Bearer <core_api_token>`. Mutating routes
additionally require the caller's PM identity in `pm_user_ids` (**PM-only allowlist**)
and an `Idempotency-Key` header; every mutation writes `audit_log` with the request id.

Errors are structured and **always returned to the caller** (fixes v2's silent
slash-command failures): `{"error": {"code": "...", "message": "...", "request_id": "..."}}`
with 4xx/5xx. The adapter surfaces these verbatim in-channel (SPEC-ADAPTER).

### 5.1 Routes

**Coverage**
```
POST   /coverage                      {ticker, exchange, name, currency, isin?, lead?, notes?}
GET    /coverage                      ?state=&exchange=
GET    /coverage/{slug}
POST   /coverage/{slug}/initiate      {lead?, verify_count?}          → creates initiation run
POST   /coverage/{slug}/decide        {decision: active|watch|reject, notes?}   (answers the gate)
POST   /coverage/{slug}/promote       {notes?}                        → also spawns deep_review
POST   /coverage/{slug}/demote        {notes?}
POST   /coverage/{slug}/exit          {force?: bool, notes}           → guard: no open position
POST   /coverage/{slug}/re-propose
POST   /coverage/{slug}/lead          {house, rationale}
POST   /coverage/{slug}/levels        {levels: [{kind, value, currency, direction}]}
GET    /coverage/{slug}/dossier       → index row + file list + stance/TP + staleness
```

**Runs and gates**
```
POST   /runs                          {type, coverage?, params}       → any run type, PM-triggered
GET    /runs                          ?status=&type=&coverage=&limit=
GET    /runs/{id}                     → run + stages + attempts + cost + artifacts
POST   /runs/{id}/cancel              {reason}
POST   /runs/{id}/retry               → new run, same params (failed runs only)
GET    /runs/{id}/artifacts/{kind}    → file stream (PDF, workbook, transcript)
GET    /gates                         ?state=open
POST   /gates/{id}/answer             {answer, notes?}
```

**Research and read models**
```
POST   /queries                       {coverage, question}            → pm_query run (light)
POST   /queries/{run_id}/keep         → curate this pm_query Q&A into dossiers/<slug>/queries/
                                        (system-generated commit; see SPEC-INITIATION §3.4 —
                                        the adapter maps the PM's keep-reaction to this route)
GET    /predictions                   ?coverage=&house=&status=
GET    /track-record                  ?house=&coverage=&since=        (SPEC-TRACKREC computes)
GET    /events                        ?coverage=&since=&severity=
POST   /events/{id}/rate              {rating: up|down}               (PM 👍/👎)
GET    /costs                         ?by=house|run|day&since=
GET    /health                        → db, queue depth, leader, last scheduler success
GET    /config/check                  → checkconfig result (never any secret value)
```

**Reserved (phase 7.2 port; routes defined now so the CLI/adapter surface is stable):**
`GET /portfolio`, `GET /risk`, `POST /trades/confirm/{id}`, `GET /trades`.

### 5.2 Cross-cutting

- **Idempotency**: `Idempotency-Key` → `idempotency_keys` table (SPEC-DOMAIN §4.23). A
  replay returns the stored response verbatim; a same-key/different-body call is a 409.
- **Authorization**: one dependency (`require_pm`) applied to every mutating route; read
  routes require only the bearer token. The allowlist is config, not code.
- **Request ids**: generated per request, attached to structlog context, written to
  `audit_log.request_id` and returned in every response and error body.

---

## 6. `fund` CLI

`cli/main.py`, one thin httpx client over the API — **no direct DB access** (so the CLI
proves the API is complete). Output: human table by default, `--json` for scripting.
Exit codes: 0 ok, 1 error, 2 gate/validation refusal.

```
fund propose tse:2267 --name "Yakult Honsha" --currency JPY [--lead gpt]
fund coverage list [--state active]
fund coverage show tse_2267
fund initiate tse_2267 [--lead gpt] [--verify-count 2]
fund decide tse_2267 active|watch|reject [--notes "..."]
fund promote|demote|exit tse_2267 [--force --notes "..."]
fund lead set tse_2267 gemini --rationale "..."
fund levels set tse_2267 --entry 2450 --target 2900 --stop 2100
fund run new deep_review --coverage tse_2267
fund runs list [--status running]        fund run show <id>
fund run cancel <id> --reason "..."      fund run retry <id>
fund gates list                          fund gates answer <id> active --notes "..."
fund query tse_2267 "how does the China JV affect the thesis?"
fund query keep <run_id>                 # curate that Q&A into the dossier (POST /queries/{run_id}/keep)
fund re-propose tse_2267                 # POST /coverage/{slug}/re-propose
fund dossier show tse_2267               # GET /coverage/{slug}/dossier
fund run artifacts <id> <kind> [-o FILE] # GET /runs/{id}/artifacts/{kind}
fund track-record [--house gpt] [--coverage tse_2267]
fund predictions list [--status open]
fund events list [--coverage tse_2267]   fund events rate <id> up|down
fund cost [--by house] [--since 7d]
fund checkconfig [--strict]              fund config reload
fund bootstrap                           # upsert houses, seed doctrine_versions row, verify dossier repo
fund health
```

Every API route in §5.1 has exactly one CLI command (a test asserts the mapping is
total, §9.4).

---

## 7. Failure-mode behaviors (design/05 §9, made concrete)

| Failure | Core's behavior |
|---|---|
| Runner worker crashes mid-stage | lease expires → reaper requeues (attempt++) → same stage retried; after `max_attempts` the run fails visibly to the desk |
| Provider outage (retryable) | stage fails retryable → run `running → queued` with backoff; coverage lock retained; no dossier change |
| Cost blowout | per-run cap → `waiting_pm` + `budget_cap` gate; per-house daily cap → stages unclaimable until midnight UTC + one desk notice |
| Two mutating runs on one ticker | second stays `queued` (coverage lock); no error, no deadlock |
| Two core processes | advisory leader locks; the loser idles |
| PM never answers a gate | run sits in `waiting_pm` (lock held); `fund gates list` and the desk digest surface it; PM can `cancel` |
| Config edited mid-run | run keeps its pinned params; new runs pick up the new config |
| Scheduler dies | `schedule_watchdog` (a separate job) alerts; if the whole process died, systemd restarts and the missed-tick check on boot alerts |

---

## 8. Module layout

```
core/config/{settings,houses,fund,pricing,store,checkconfig}.py
core/orchestrator/{engine,planner,rotation,budgets,gates}.py
core/scheduler/{jobs,calendar,leader}.py
core/api/{app,deps,errors,routes/{coverage,runs,gates,queries,track_record,costs,health}}.py
cli/{main,client,format}.py
config/{houses,fund,pricing}.yaml.example
```

---

## 9. Test plan (TDD)

Unit tests: `tests/unit/{config,orchestrator,scheduler,api,cli}/`. Network blocked by the
autouse socket fixture; the API is exercised **in-process** via
`httpx.ASGITransport(app=app)` (no port, no socket). DB = in-memory SQLite (SPEC-DOMAIN
§9.2). Clock = `FakeClock`. Provider calls: **none exist in core** — the only way core
touches a model is by queuing a stage, and in tests that stage is consumed by `FakeRunner`.

### 9.1 `FakeRunner` (the phase-1.3 keystone)

`tests/fakes/runner.py`: claims stages via the real `queue.claim_stage`, writes a canned
`stage_result.yaml` per role (author → report artifacts + predictions; verifier →
corrections; finalizer → PM summary + final predictions), and completes them.
Configurable to fail (retryable/terminal), to emit an invalid `stage_result`, to exceed
budget, or to hang (lease expiry). **The entire orchestrator, gate, budget, and PM-gate
flow is tested through it, with zero LLM calls.**

### 9.2 Orchestrator

- Planner (pure): `initiation` with `verify_count=2` → author(lead) · verifier(c1) ·
  verifier(c2) · finalizer(lead), all harness; `verify_count=1` → three stages;
  `include_data_checker=true` inserts the data_checker before the finalizer; `lead_review`
  → one stage per contributor; `distillation` → the meta house only; a plan that would
  assign a `meta` house to an analyst role raises.
- Rotation: two consecutive initiations on the same coverage pick different verifier
  pairs when ≥3 contributors exist; with exactly 2 contributors it degrades gracefully
  (both used, order swapped).
- Full happy path with `FakeRunner`: propose → initiate → 4 stages succeed → run
  `waiting_pm` with one open `initiation_decision` gate → `POST /gates/{id}/answer
  {active}` → coverage `active`, predictions `open`, dossier branch merged (fake git),
  outbox rows for channel creation and report delivery.
- `decide: reject` → coverage `rejected`, branch not merged, that run's predictions
  `superseded`, no channel outbox event.
- Transient failure: stage 2 fails retryable → run `queued`, stage 2 requeued with
  backoff, stage 1 **not** re-executed; on resume the run reaches the gate normally.
- Terminal failure: stage 2 exhausts `max_attempts` → run `failed`, coverage
  `initiating → failed`, lock released, desk outbox row carries the last validation
  errors.
- Lease expiry (FakeRunner hangs): reaper requeues, `stage_attempts` row closed with
  `kill_reason='lease_expired'`, cost of the dead attempt still counted.
- Auto cross-check: an `event_analysis` author result registering a TP 12% above the last
  one appends exactly one `cross_check` stage; a 5% change appends none; a
  `thesis`-severity trigger appends one regardless of ΔTP; a result whose *prose* claims
  "no cross-check needed" but whose registered TP moved 20% still appends one.
- Per-ticker serialization: a `deep_review` created while an `initiation` is `waiting_pm`
  stays `queued`; a `monitor_tick` for the same ticker runs immediately.
- Budgets: a run whose stage costs exceed `budget_cap_usd` → `waiting_pm` + `budget_cap`
  gate; `raise_cap` resumes it (audited); `cancel` cancels it. A house at its daily cap
  makes its stages unclaimable and emits exactly one desk notice per day.

### 9.3 Config

- `Settings` loads from an **explicit test env dict**, never a file on disk; a missing
  required secret raises with a message naming the *variable*, never a value.
- `houses.yaml` validation: `assignable AND meta` → error; unknown harness type → error;
  a model id absent from `pricing.yaml` → error; zero assignable houses → error.
- **No hardcoded models/prices**: a repo-wide test greps `core/`, `runner/`, `adapter/`,
  `cli/` for provider model-id patterns and price literals and fails on a hit (the
  automated form of AGENTS.md's rule).
- Reload: an invalid `fund.yaml` leaves the previous snapshot active and logs; a valid
  one rebinds atomically (a reader holding the old snapshot sees a consistent old value).
- Houses upsert: a key removed from `houses.yaml` becomes `enabled=false` in the DB, is
  **not** deleted, and its historical predictions/corrections still join.
- `checkconfig`: each check has a failing fixture; `--strict` exits non-zero; **no test
  asserts on a secret value, and the output of a "key present" check is asserted to
  contain no key material.**

### 9.4 API and CLI

- Every mutating route: without a PM identity → 403; without `Idempotency-Key` → 400;
  replay with the same key → identical response, exactly one `audit_log` row; same key +
  different body → 409.
- Illegal transitions (e.g. `decide` on an `active` coverage) → 409 with a structured
  error, no partial state.
- **Route/CLI parity test**: enumerate FastAPI routes and CLI commands; assert the
  mapping is total in both directions (an added route without a CLI command fails CI).
- `GET /health` reports queue depth, leader status, last scheduler success per job.
- Error envelope: every 4xx/5xx carries `code`, `message`, `request_id`; a route that
  raises an unhandled exception returns 500 with a request id and logs a redacted
  traceback (never a secret).

### 9.5 Scheduler

- Jobs are invoked directly (no wall-clock waiting; APScheduler is not started in unit
  tests). `session_tick` creates one `monitor_tick` per `active` coverage on that
  exchange and none for `watch`; a double-fire creates no duplicate run (deterministic
  `trigger_ref`).
- `schedule_watchdog` raises a desk alert when a job's last success is stale.
- Leader lock: with the lock held (simulated), `tick()` and job bodies are no-ops.

### 9.6 Integration (`@pytest.mark.integration`, opt-in)

Real Postgres: two orchestrator instances contend for the advisory lock (exactly one
leads); `create_run` duplicate `trigger_ref` is rejected by the partial unique index;
the budget reserve/settle path is race-free under concurrent stage launches.

---

## 10. Appendix — proposed `doctrine/roles/pm_query.md` (PM approval required)

`pm_query` is the only run type with no role prompt in `doctrine/roles/`. Proposed text
(to be reviewed with the PM; doctrine is PM-owned, and this file must exist before
`pm_query` can run):

> **Role: PM query (lead house, light model, API substrate).** You answer the PM's
> question about a stock you lead, from its dossier. Cite dossier state explicitly —
> stance, target price, and the `as_of` date — and quote the file you drew each claim
> from. If the dossier's `as_of` is older than the newest material event, say so plainly
> ("the dossier is stale as of …") instead of improvising an update. Never invent a
> number, price, or filing detail that is not in the dossier or returned by a tool; if
> the answer needs work the dossier cannot support, say what run would produce it
> (`deep_review`, `event_analysis`) and offer to spawn it — **you never spawn heavy work
> yourself**. Answer in a few paragraphs, not a report.

---

## 11. Spec Authoring Checklist

- **Side-effect cost.** Core makes **zero** provider calls itself; it only *causes* them
  by queuing stages, and every stage's cost is capped twice (per run, per house per day)
  before launch. Cost deltas introduced here: (a) `pm_query` adds one light-model call
  per PM question (envelope < $0.20; the run type is capped in `fund.yaml`); (b) the
  scheduler's `session_tick` creates one `monitor_tick` run per active coverage per
  session — the *only* LLM call inside it is the single cheap scoring call
  (SPEC-MONITORING), capped at `budget_cap_usd: 0.10`, so ~25 active × 2 sessions ≈
  50 calls/day ≈ $2.50/day worst case; (c) no embedding, FX, search, or news call
  originates in core. DB writes per run are bounded (≤ ~100 rows). The nightly
  `cost_rollup` reconciles metered vs provider-reported spend and alerts on drift, which
  is how a mispriced or unpriced model gets caught rather than silently costing money.
- **Concurrency model.** Process-global state exists and is named: (1) the **config
  snapshot** (`core/config/store.py`) — a single module-level reference to a frozen
  pydantic model, mutated only by **atomic rebind** on reload, so concurrent readers
  always see one consistent version, and runs additionally *pin* their resolved config
  into `runs.params` at creation; (2) the **settings** `lru_cache` — immutable, built
  once; (3) the SQLAlchemy **engine/pool** — thread-safe by design; no `scoped_session`
  and no module-level `Session`. Cross-process concurrency is guarded in Postgres, not in
  Python: two Postgres **advisory leader locks** (orchestrator, scheduler) make a second
  `core` process a warm standby rather than a double-firer; the stage queue uses
  `FOR UPDATE … SKIP LOCKED` + leases (SPEC-DOMAIN §6.3); per-ticker serialization is the
  `coverage_run_locks` row; per-house daily budgets are reserved under
  `SELECT … FOR UPDATE` on `house_budget_days`. The orchestrator tick is idempotent and
  re-entrant: each item is handled in its own short transaction, so a crash mid-tick
  leaves no half-applied run.
- **LLM-as-filter threat model.** Core never lets an LLM decide what the PM sees. Two
  paths where model output influences control flow, and their backstops: (1) **auto
  cross-check** (§3.4) is computed from *registered predictions and the event row* — both
  written through validated, typed fields — not from model prose, so a model cannot talk
  its way out of a cross-check by asserting one is unnecessary, and cannot force one on a
  peer except by actually moving a registered number; (2) **gates** are opened by code on
  run-type completion, and their `allowed_answers` are a code-owned closed set — a model
  cannot open, answer, or widen a gate, and the *only* actor who can answer is a PM in
  the `pm_user_ids` allowlist. `pm_query` (the one core-adjacent run type that reads
  untrusted-ish content) is read-only by construction: it holds no coverage lock, cannot
  write the dossier, cannot register predictions, and cannot spawn a run — it may only
  *suggest* one, which the PM then triggers. Every stage result crossing into the DB is
  schema-validated by the runner first (SPEC-RUNNER §5) and stored in typed columns;
  free text is redacted and length-capped. Fail-closed: an invalid `stage_result` fails
  the stage (bounded retry, then a visible failure) — code never repairs or synthesizes
  a missing recommendation or confidence.
- **Identity-key ownership.** Core mints the keys it depends on: `runs.trigger_ref`
  (`{job}:{exchange}:{trading_date}:{session}` — scheduler-owned, backed by a partial
  unique index so a re-fire cannot double-spawn); `idempotency_keys.key` =
  `sha256(route|actor|normalized_body)` — **API-owned**, not the caller's trigger id (v2's
  bug was keying on Mattermost's per-interaction `trigger_id`, which changes on retry);
  `pm_gates.idempotency_key` (answer-once); outbox `dedupe_key` (publisher-owned). Keys
  core *borrows*: `coverage.dossier_slug` (owned by SPEC-DOMAIN's
  `core/domain/dossier_slug.py` — core **reads the column**, never re-derives it, and the
  API resolves `{slug}` path params through that column) and stage identity (the
  orchestrator creates stage rows, so `attributed_stage` seqs from model output are
  resolved against *its own* rows — SPEC-DOMAIN §4.14). Config keys (house keys, model
  ids) are owned by the operator's YAML; the DB mirrors them and never invents one.
- **Test isolation.** Nothing in core opens a socket in unit tests: the API is driven
  through `httpx.ASGITransport` in-process; the DB is in-memory SQLite; the scheduler is
  invoked function-by-function (APScheduler is never started); the market-data service,
  search endpoint, and every provider are behind interfaces that unit tests fake
  (`FakeMarketData`, `FakeLLM`) — and core calls none of them directly anyway. The one
  path that could reach a provider is stage execution, which core **does not implement**:
  `FakeRunner` (§9.1) stands in for it, which is precisely why phase 1.3 can be built and
  proven with zero LLM spend. Postgres-specific behavior (advisory locks, partial unique
  indexes, budget races) is `@pytest.mark.integration` and skips without a test-DB URL.
  The autouse socket guard in `tests/unit/conftest.py` is the backstop that catches a
  builder wiring a real provider client or a real HTTP call into core.

---

## 12. Amendments this spec requires of SPEC-DOMAIN

Two additive changes (both one-line migrations if `spec-domain` merges first; fold them
in if it has not):

1. `StageRole` gains `PM_QUERY = "pm_query"` (§3.2), with `doctrine/roles/pm_query.md`
   (§10) added on PM approval.
2. `runs` gains a partial unique index
   `uq_runs_trigger_ref ON runs (coverage_id, type, trigger_ref) WHERE trigger_ref IS NOT NULL`
   so scheduler double-fires cannot create duplicate runs (§4).
