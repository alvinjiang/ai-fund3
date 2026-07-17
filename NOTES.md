# NOTES

## 2026-07-18 — PM decisions recorded; docs/NEXT_STEPS.md created

Three PM answers recorded in the owning specs (same Fable session as below):

- **JP price providers: yfinance-primary** (no paid J-Quants plan) → SPEC-MARKETDATA
  §3.3 updated; fresh JP pins are single-source (free-plan J-Quants covers only dates
  > ~12 weeks old as a cross-check) — stated, not hidden. PROMPTS 2.3 note updated.
- **Doctrine repo: Mode A** (standalone, outside the deploy tree) → SPEC-DISTILLATION
  §3; instantiation is `fund bootstrap`'s job (SPEC-CORE §6 updated: creates+seeds the
  repo if absent, idempotent, never re-seeds an existing one — no manual git surgery).
- **First-house (2.1)**: nothing to decide yet; context and the selection rule
  (authenticate fastest; prefer parseable usage output) written into docs/NEXT_STEPS.md
  §Open actions.

**`docs/NEXT_STEPS.md` (new)** is the PM's standing reference: status, locked
decisions, open PM/operator actions in order, the execution sequence with model tiers,
and the ranked Fable checkpoints (PR-review 1.3/2.2 branches, gate 2.4/3.3 triage,
spec contradictions during BUILD, first distillation diff, pre-cutover). Keep it
updated as phases complete.

Also noted: the 1.3 BUILD session started on branch `spec-domain` today; an earlier
commit from this session accidentally swept its uncommitted files and was immediately
redone clean (5e15a14) — the builder's working tree was restored untouched.

## 2026-07-18 — Fable derisking session: SPEC-MARKETDATA, doctrine diffs staged, review checklists

Front-loaded the remaining strong-model work before BUILD begins (PM: "derisk or decide
in advance — I may not be able to call on you after implementation is done"). Three
deliverables:

1. **`specs/SPEC-MARKETDATA.md` (new)** — Phase 2.3 reclassified from CHORE to
   SPEC+CHORE (PROMPTS.md updated). Reading v2 `data_sources/` confirmed the port alone
   would not meet what later specs assume: v2 serves a *live* last price (no
   per-trading-date pinned close), FX with **no date parameter**, **unadjusted** history
   with no adjustment factors or dividends, no halt/delisting signal, and no earnings
   calendar outside J-Quants. The spec defines the endpoint contract (pinned close vs
   display-only quote as separate endpoints; **persisted `price_pins`** so provider
   revisions can't rewrite what a report reconciled against — added to SPEC-DOMAIN §12
   registry; dated FX; `adj_factor_cum` + dividends with confidence labels;
   `/status` halt inference; tiered earnings calendars with the quarterly sweep as the
   stated floor). ⚠️ **Operator decision before the 2.4 pilot** (spec §3.3): J-Quants
   free plan delays data up to 12 weeks — every JP price-pin gate would fail (the pilot
   ticker is likely JP). Choose paid-plan-primary or yfinance-primary for `tse`;
   `checkconfig --strict` now specced to enforce the choice.
2. **`docs/PROPOSED_DOCTRINE_EDITS.md` (new)** — the three PM-owned doctrine changes
   staged as ready-to-apply diffs: distiller evidence-window wording (matches
   SPEC-DISTILLATION §2), distiller citation-id form (avoids citation-gate retries),
   and the new `doctrine/roles/pm_query.md` (blocking for pm_query runs).
3. **`docs/BUILD_REVIEW_CHECKLISTS.md` (new)** — per-branch review criteria for every
   BUILD PR (the failure modes a mid-tier builder introduces while tests still pass:
   silent defaults, gate "repair", float creep, borrowed keys, import-time clients),
   plus the 2.4/3.3 gate-failure triage order. AGENTS.md now points PR review at it.

## 2026-07-17 — Spec review pass (Fable): cross-spec consistency fixes

Full review of all 8 `specs/SPEC-*.md` against `design/` and the authoring checklist,
by the model tier that wrote the design docs. **Verdict: the specs are strong and are
kept — no rewrite.** Four real cross-spec defects and several small gaps were corrected
in place (no code exists yet, so all fixes are spec edits, no migrations):

1. **Queue dependency hole** (SPEC-DOMAIN §6.3 + SPEC-CORE §3.3): the `SKIP LOCKED`
   claim SQL never checked `depends_on_seq`, so a verifier stage was claimable while the
   author stage was still running; SPEC-CORE's "release ready stages" step named no
   mechanism. Fixed by adding the dependency `EXISTS` predicate to the claim query itself
   (supports the future `parallel_verify` case; `skipped` satisfies dependents) and
   rewording SPEC-CORE — readiness is enforced in one place, with an integration test.
2. **`decision_pending` dead-end** (SPEC-DOMAIN §5 vs SPEC-INITIATION §7): cancelling an
   initiation at the PM gate had no legal coverage transition — the gate would be
   cancelled and the ticker stuck (`decide` requires an open gate). Added
   `decision_pending → failed` (`initiation_cancelled`) plus a PM `withdraw` edge
   (`proposed|failed → rejected`) so no state can strand.
3. **api-substrate `event_analysis` contradiction** (SPEC-CORE §3.2 / SPEC-MONITORING §8
   vs SPEC-RUNNER §8): the api loop has no workspace writes, but the role's gates require
   an `events/` note + dossier edits. Resolved as **triage-only**: an api attempt may
   conclude `nothing_material` (runner materializes the note from the structured result);
   anything material re-plans the stage on harness. Tests added.
4. **`queries/` curation flow impossible as written** (SPEC-INITIATION §3.4 said the
   read-only, already-finished `pm_query` run writes the file; SPEC-ADAPTER called
   `POST /queries/{id}/keep`, which SPEC-CORE never defined). Now: core writes it as a
   code-formatted **system commit** under the coverage lock; route added to SPEC-CORE
   §5.1 + CLI.

Smaller fixes: sparse-checkout cone path (`<slug>`, not `dossiers/<slug>` — the dossier
repo root has no prefix); direction-aware `entry_point` scoring (breakout entries above
the pinned price used the wrong comparison); dropped dead schema (`disagreements.pm_rating`
— v1 rates events only; unused `GateKind.REVIEW_ESCALATION`); CLI parity gaps closed
(`fund re-propose`, `fund dossier show`, `fund run artifacts`, `fund query keep`); and a
new **SPEC-DOMAIN §12 amendments registry** consolidating every later spec's schema
amendment so the phase-1.3 builder doesn't have to scan five specs (per the "fold in if
not yet built" rule below).

**Role-prompt ↔ spec-contract audit** (follow-up, same session): all 8
`doctrine/roles/*.md` checked against the machine contracts the specs later defined
(SPEC-RUNNER §6.1 `stage_result` schema + artifact gates, SPEC-INITIATION §3–4 dossier
validators, SPEC-TRACKREC §6.3 ballots, SPEC-DISTILLATION §5.1). Result: **compatible —
no gate-contradicting instruction found** (author/verifier/finalizer/cross_check/
lead_review/data_checker/monitor all match their gates' field vocabulary). SPEC-RUNNER
§4.2 amended: `task.md` must spell out the config-derived validator numbers
(min_tripwires, tolerances, min_references, max_price_age_days) since role prompts
deliberately omit them. Two **proposed doctrine edits reported, not applied**
(doctrine is PM-owned; 0.3 rule):
1. `distiller.md` — says the window is "since the last distillation"; SPEC-DISTILLATION
   §2 defines it as *since the current doctrine version's `approved_at`* (a rejected
   amendment must not shrink the evidence window). One-line wording fix.
2. `distiller.md` — asks for "a one-paragraph summary" in `stage_result.yaml` but does
   not mention the structured `amendments[].cites` list of **resolvable ids**
   (`correction:<id>`, `run:<id>`) the §6.3 citation gate enforces; prose-cited incidents
   would fail the gate. Suggest naming the citation form in the role prompt (task.md
   carries the schema either way, so this is retry-avoidance, not correctness).
Also pending PM: `doctrine/roles/pm_query.md` (text proposed in SPEC-CORE §10) must be
approved before any `pm_query` run can execute.

## 2026-07-12 — v3 repo bootstrapped (transfer from v2)

Created `ai-fund3` as a fresh repo (PM-confirmed fresh-repo decision, closing the open
item in design/00). Copied in: the 8 redesign docs → `design/`; the 8 stage role
prompts → `doctrine/roles/` (unchanged from the 2026-07-07 Fable design session); the
proprietary doctrine source (`Equity-Research-Prompts_2026-06-05.md`) and the two
gold-standard report zips (Yakult, LINK REIT) → `proprietary/` (gitignored, per the
v2 policy that proprietary doctrine stays out of git). Wrote `PROMPTS.md` as the
phase-ordered execution file.

No code yet. Process/convention memory ported from v2 (`/home/alvin/aicode/ai-fund`),
history not copied: the Absolute Rules + Editing Rules from v2's `NOTES.md` →
`AGENTS.md` (adapted to v3 — dropped Redis/dispatchers/personas, kept secrets discipline,
test isolation, no-model-defaults, config-outside-deploy-tree); v2's
`docs/SPEC_AUTHORING_CHECKLIST.md` → `docs/` (test-isolation item updated for the
harness); `pytest.ini` (network-isolation markers) and a cleaned `.pre-commit-config.yaml`
(gitleaks + pre-commit-hooks + ruff; dropped the v2-specific pytest hook). v2's dated
Implementation Decisions, code-audits, and `.claude/settings.local.json` (accumulated
permission grants) were deliberately left behind as history.

Next session: start at PROMPTS.md Phase 0.1 (repo scaffolding — design/, doctrine/roles/,
AGENTS.md, pytest.ini, .pre-commit-config.yaml, docs/SPEC_AUTHORING_CHECKLIST.md already
in place; dirs, pyproject, .env.example, README expansion, initial commit remain).

## 2026-07-12 — Phase 0.1 complete (repo scaffolding)

Executed PROMPTS.md Phase 0.1 (CHORE). Done:

- **Directory skeleton**: `core/`, `runner/`, `adapter/`, `cli/` (Python packages, empty
  `__init__.py`); `doctrine/{sections,lessons,report,templates}/` and `specs/`, `tests/`,
  `tests/unit/` (`.gitkeep` so empty dirs track).
- **`dossiers/`** initialized as a **separate git repo** with a first empty commit (per
  design/05 §8 / design/06), and added to this repo's `.gitignore` so the two repos stay
  cleanly separated (the main repo never tracks the embedded repo).
- **`pyproject.toml`** added: setuptools backend, `requires-python = ">=3.12"`, runtime
  deps limited to the convention-mandated foundational libs (pydantic, pydantic-settings,
  structlog); dev extras = ruff/pre-commit/pytest(+asyncio/mock/cov). `pytest.ini` markers
  folded into `[tool.pytest.ini_options]` (single source of truth) and `pytest.ini`
  **removed**. ruff config (line-length 100, target py312, lint select
  E/F/W/I/UP/B/SIM/C4/RUF) is read by both local and pre-commit ruff (same pyproject).
- **`.env.example`** added: fake values only, secrets-only shape per design/05 §6 (non-secret
  config belongs in `/etc/ai-fund/*.yaml`). Exact var names finalized in SPEC-CORE (1.2).
- **`README.md`** expanded: ~20-line architecture sourced from design/01 + design/05;
  Layout section updated with the new dirs.
- **`CONVENTIONS.md`** created as a tool-agnostic mirror (AGENTS.md remains the source of
  truth). Includes the venv/dev setup commands.

Decisions worth flagging:

- Host has **only Python 3.14.4**; it satisfies `>=3.12`. Verified ruff 0.8.4 installs and
  runs on 3.14 (abi3 wheels), so the operator's pinned `.pre-commit-config.yaml` rev is
  left unchanged.
- A `.venv/` (gitignored) was created for dev tooling; `pre-commit install` ran, so the
  git hook is active — keep the venv active when committing.
- Initial commit author is `alvin <ajiang@gmail.com>` (matches v2's recent commits) applied
  via per-command `git -c user.name=… user.email=…` — global `user.name` is unset and no
  git config files were modified. Amend if a different identity is preferred.

Next: Phase 0.2 (seed doctrine) — a reasoning-model session handles the SPEC steps.

## 2026-07-12 — Phase 0.2 complete (seed doctrine)

Seeded `doctrine/` from `proprietary/Equity-Research-Prompts_2026-06-05.md` (reorganized,
not rewritten — wording preserved verbatim, including source typos like "occurence",
"whats best", "formay", "why?What"). Split per `design/03` §3:

- `doctrine/core.md` — preamble + "Additional Notes" (References, Currencies,
  Calculations, Price prediction, Recommendations, Check your work) + the `###`
  past-mistakes *process-rule* subsections (Subject-company market data, Comps tables,
  Workbooks, Tie actuals to filings, Report-to-workbook reconciliation, Validation, FX
  and capital raises, Division of labour, Terminology).
- `doctrine/lessons/global.md` — the "Further notes from past mistakes" top-level bullets
  (the lessons; amendable only via distillation PRs).
- `doctrine/report/full-report.md` — Workflow for full reports + Full reports (stat block,
  `NAME_SYM_EXCH-YYYYMMDD` naming, corrections-log requirement).
- `doctrine/sections/*.md` — the 14 section prompts, one file each.
- `doctrine/templates/ironclad-template.html` — extracted from
  `proprietary/yakult-20260610.zip` (`Yakult-Honsha/ironclad-template.html`).

Split decision: the source's "Further notes from past mistakes" section interleaved
top-level lessons with `###` operational process-rule subsections. I separated them —
bullets → `lessons/global.md`, `###` subsections → `core.md` — so every source line lands
in exactly one doctrine file (no loss, no duplication). The "no-placeholder" rule is
enforced in `core.md` (### Workbooks/Comps) and stated as a lesson in `lessons/global.md`.

14-section cross-check vs v2 `prompts/equity_prompts.py` (`SECTION_PROMPTS`): **1:1 match,
no drift** — company_overview, bull_vs_bear, competitive_advantages, supply_chain,
segments, earnings_result, earnings_calls, management, stock_price_analysis, comps,
forward_projection, red_flags, management_questions, devils_advocate. v2 stored one-line
summaries; the source contains the full proven prompts (full coverage, nothing
missing/extra). Filenames use v2's canonical keys; note `design/03`'s example "bull_bear"
was normalized to `bull_vs_bear` to match the v2 key. Source mixes US/UK spelling
(capitalisation/finalising/analysed/parallelisable/labour vs behavior/labeling) — preserved
verbatim. `proprietary/` stays gitignored; only the reorganized `doctrine/` is tracked.

## 2026-07-13 — SPEC session: cross-cutting decisions (Phases 1.1–7.1)

Dated log of decisions and risks that span more than one spec. Each is stated in full in
the spec that owns it; this is the index. (Written during the SPEC-tier session that
produced `specs/SPEC-*.md`; no code written.)

**From SPEC-DOMAIN (1.1):**

- **`Numeric`, never `Float`, for every price/quantity/money column** (v2 used `Float`).
  Follows design/01 principle 6 (deterministic money path). Consequence: the Phase 7.3
  data migration casts v2 floats on import.
- **No Postgres `ENUM` types** — `String` + `CHECK` + Python `StrEnum`. Adding a run type
  or stage role stays an ordinary migration instead of an `ALTER TYPE`.
- **`stage_attempts` is its own table.** Failed attempts cost real money; cost, transcript,
  and validation errors are per attempt, so retries are attributable (v2 cost-drift lesson).
- **Transactional outbox (`outbox_events`)** is how core notifies the adapter. Written in
  the same transaction as the domain change, consumed with `SKIP LOCKED` — a disconnected
  adapter cannot miss a gate or a channel creation, and cannot double-post.
- **Per-ticker serialization = `coverage_run_locks` row, held through `waiting_pm`** (the
  dossier branch is unmerged until the PM decides). **Risk:** an unanswered initiation gate
  blocks that ticker's other mutating runs until the PM answers or cancels. Revisit after
  the 2.4 pilot if it bites; the alternative (merge on finalizer success) weakens the
  "PM decides" boundary, so it is not the default.
- **pgvector is not in the alembic baseline** (no extension, no embedding column). The
  keep/drop decision belongs to Phase 4; re-adopting it is an additive migration.
- **One severity vocabulary** (`thesis | valuation | info`) across tripwires and events.
  The monitor role prompt's `materiality: high|medium|low|none` is scoring output, mapped
  onto severity by code — it is never a stored state. (Closes the naming drift flagged in
  the 0.3 review.)

**From SPEC-MONITORING (4.1) — the deferred pgvector decision:**

- **pgvector: DROPPED** (decision made here, per design/00's "defer to Phase 4"). Its only
  v3 consumer would be news dedup/near-dup; v3 judges relevance with the tripwire-scoring
  call (against authored conditions, not semantic similarity), so embeddings would be pure
  cost (~50–200 provider calls/day) with no reader, plus an extension + dimension migration
  to carry. Replacement is deterministic: normalized URL + content hash (both ported
  verbatim from v2 `news/dedup.py`) + SimHash near-dup clustering.
  **Reversal is measured, not vibes:** the monitor role already flags duplicate stories, so
  `dedup_miss_rate` (model-flagged dups the code failed to cluster) is logged per tick. If
  it exceeds 10% over 30 days at ≥20 active tickers: tune SimHash → try `pg_trgm` → only
  then adopt pgvector (additive migration; the column name stays reserved).

**Cross-spec risks flagged (SPEC-INITIATION 3.1, SPEC-MONITORING 4.1):**

- The specs from 3.1 onward carry a "Preliminary — pending PM gates" section listing the
  interface assumptions PM gates 2.4/3.3 could invalidate (gate list, sequential verify
  passes, registration at the finalizer only, rotation policy, escalation caps, trust
  tiers). Each is config-driven so a gate failure is doctrine/config work, not a rewrite.
- **Escalation caps and news source trust tiers are the anti-prompt-injection levers** for
  the one place an LLM decides what the PM sees and what the fund spends money on (news →
  `event_analysis`). Both are guesses until the first month of live monitoring.

**Amendments later specs make to earlier ones** (all additive; fold them in if the earlier
spec has not been built yet, else they are one-line migrations):

- SPEC-CORE: `StageRole` gains `PM_QUERY` (needs a new `doctrine/roles/pm_query.md` — text
  proposed in SPEC-CORE §10, **PM approval required**, since doctrine is PM-owned); `runs`
  gains a partial unique index on `(coverage_id, type, trigger_ref)` so a scheduler re-fire
  cannot double-spawn.
- SPEC-INITIATION: `corrections` gains a unique `idempotency_key` (without it, a retried
  verifier stage double-charges another house's corrections-received rate — i.e. it would
  corrupt the earliest quality signal in the system); `tripwires.yaml` gains optional
  `keywords` (used by SPEC-MONITORING for keyword corroboration).
- SPEC-ADAPTER: new core-owned tables `mm_channels` / `mm_posts`; the outbox is consumed
  **through the core API** (`POST /outbox/claim|ack|nack`) rather than by the adapter
  touching Postgres — core stays the only DB writer and the adapter needs no DB credentials.
- SPEC-TRACKREC: `predictions` gains `direction` + `pinned_price` (code-written at
  registration, so scoring never re-derives what a prediction meant); `events` gains
  `pm_rating`; new `house_metric_snapshots` table.

**From SPEC-DISTILLATION (7.1) — needs an operator decision before BUILD:**

- **Where doctrine lives.** Recommended: a **standalone doctrine git repo outside the deploy
  tree** (`/srv/ai-fund/doctrine`), seeded at bootstrap from the repo-tracked `doctrine/`.
  Reason: an approved amendment is fund state, and a `git reset` in the deploy tree must
  never clobber it (the same rule as operator config). The alternative (doctrine stays in
  this repo; distillation pushes a branch and the PM merges a PR) is supported via
  `fund.yaml doctrine.mode: B`. Pick one before building 7.1.
- **7.1 BUILD was deliberately not done** in this SPEC session (PROMPTS marks it SPEC+BUILD).
  The spec's §6 gates — meta firewall, diff-scope, and the **citation gate** (every proposed
  doctrine rule must cite an incident row that actually exists, in-window) — are the
  acceptance criteria: doctrine is what every future run obeys, so a hallucinated
  justification would propagate into every report the fund ever writes.

## 2026-07-12 — Phase 0.3 complete (role-prompt review)

Reviewed all 8 role prompts (`doctrine/roles/*.md`) against `core.md` for terminology
consistency. **No inconsistencies.** Roles faithfully use core.md doctrine vocabulary:
"unverified" (never a plausible substitute), pinned price (one authoritative timestamped
close, cross-checked across two sources), primary filing, workbook one-computed-layer
(formulas not typed numbers), report↔workbook reconciliation (report value vs workbook
cell vs match Y/N), 52-week range sanity gate, HKD/SGD/USD currency discipline, corrections
log with attribution, street consensus, confidence/target/horizon. The finalizer's
`NAME_SYM_EXCH-YYYYMMDD` naming and reconciliation table match `report/full-report.md`; the
distiller quotes core.md's cached-"last close" rule verbatim. Roles introduce v3 terms not
in core.md (tripwires, dossier, predictions, `stage_result.yaml`, lead/contributor/house,
substrate) — expected, since core.md is seeded from the v2-era source and roles are
v3-authored; not an inconsistency. No role prompts edited (PROMPTS 0.3: report, don't
silently edit; tuning deferred to Phase 2/3 PM gates). `verifier.md` (the PM's proven
multi-pass formula) confirmed intact — not weakened.

Two minor cross-*document* naming drifts flagged for later SPEC alignment (NOT core.md
inconsistencies, no action now):
1. `monitor.md` scoring output field is `reason`; `design/03` §2.4 uses `one_line_reason`.
2. `monitor.md` `materiality: high|medium|low|none` vs `design/03` §2.4 escalation severity
   vocabulary (`thesis`/`info`) — reconcile in SPEC-MONITORING (Phase 4).

Next: Phase 1.1 SPEC — domain schema & coverage state machine (reasoning-model session).

## 2026-07-18 — Phase 1.3 (spec-domain) BUILD complete — SPEC-DOMAIN implemented

Implemented `specs/SPEC-DOMAIN.md` on branch `spec-domain`, TDD from the spec's §9 test
plan. 286 unit tests (SQLite + pure functions) + 8 integration tests (Postgres via
testcontainers) green; ruff clean.

- **Domain (pure):** `enums` (§3 + §12 `PM_QUERY`), `dossier_slug`, `contributors`,
  `coverage_sm` (§5: every legal transition, the exhaustive illegal cross-product, guards,
  side effects), `run_sm` + stage SM (§6: transient `running→queued` re-entry guarded by
  `attempts<max_attempts`, `waiting_pm` carries an `open_pm_gate` side effect, terminal
  statuses release the lock + emit `run.finished`).
- **Schema:** `core/db/types` (Numeric money path — `Money/Price/Cost/Prob`; cross-dialect
  `JsonType` → JSONB on Postgres / JSON on SQLite; `utc_now`), `base` (naming convention +
  `TimestampMixin`), and `models` — all 35 tables of §4 with every CHECK/FK/unique
  constraint, the §12 amendments folded in (runs partial `uq_runs_trigger_ref`, corrections
  `idempotency_key`, predictions `direction`/`pinned_price`, events `pm_rating*`,
  `house_metric_snapshots`, `mm_channels`, `mm_posts`), and the §4.25 ported v2 tables
  (`Float`→`Numeric`, `String(36)`→`Uuid`, MM columns nullable).
- **Repos:** coverage (lifecycle chain — transition+audit+outbox in one tx, `request_id`
  linkage (invariant 5), rollback atomicity, slug stability, lead history, level
  supersession, contributor resolution); runs (per-ticker lock held through `waiting_pm`,
  `monitor_tick` takes no lock, `finish_run` releases + emits, gate open/answer idempotent);
  predictions (idempotent registration keyed on the *stage id*, immutability of everything
  but scored/superseded columns, NULL confidence stays NULL); events (`dedupe_key`
  idempotency); outbox (publish/consume/ack/nack, lease + backoff); `queue` (`claim_stage`
  `FOR UPDATE SKIP LOCKED` + dependency `EXISTS` gating, heartbeat, `complete`/`fail` with
  per-attempt cost rollup, `reap_expired`).
- **Infra:** minimal `core/settings` (DATABASE_URL + ARTIFACTS_DIR; SPEC-CORE expands),
  `core/db/session` factory (no `scoped_session` global), alembic baseline (`0001` via
  `metadata.create_all`, `env.py` reads URL from settings, `alembic.ini` at root). §9.4
  fakes: `FakeClock`, `FakeHarnessDriver` (+ minimal `HarnessDriver` Protocol), and
  `make_house/coverage/run/stage` factories.

**Decisions/fixes worth flagging:**
- `server_default=func.now()` (not `text("now()")`) everywhere — `func.now()` compiles to
  `CURRENT_TIMESTAMP` on SQLite and `now()` on Postgres; `text("now()")` failed on SQLite.
- `doctrine_versions.distillation_run_id` FK marked `use_alter=True` to break the
  `runs ↔ doctrine_versions` FK cycle (otherwise SQLAlchemy can't sort tables for
  create_all/drop_all and `alembic check` drifts).
- Removed the redundant `UniqueConstraint` on `house_metric_snapshots` — the composite PK
  already enforces uniqueness, and the duplicate showed up as drift in `alembic check`.
- Integration suite is opt-in (`pytest -m integration`); a conftest hook marks+skips it
  otherwise so the unit suite stays fast and service-free. `testcontainers[postgres]` added
  to dev deps (Docker; `postgres:16-alpine`).
- One SQLite unit-test caveat: SQLite returns datetimes naive on read-back, so tests that
  compare a Python-aware datetime to a refreshed one normalize first (the `heartbeat` test).
- Cost rollup counts failed attempts (invariant 3); `stage_attempts` is a table, not a
  counter (v2 cost-drift lesson).

Next: Phase 1.3 (spec-core) — orchestrator + config + core API + CLI (`SPEC-CORE.md`).

## 2026-07-18 — Phase 1.3 (spec-core) BUILD complete — SPEC-CORE implemented

Implemented `specs/SPEC-CORE.md` on branch `spec-core`, TDD from the spec's §9 plan. 335
unit + 9 integration tests green; ruff clean.

- **Config (§2):** `settings` (pydantic-settings, required `core_api_token`, frozen,
  `_env_file=None` test override), `houses` (assignable/meta exclusivity, ≥1 assignable,
  provider-key resolver that names missing vars not values), `fund` (run policies +
  auto-cross-check thresholds), `pricing` (single price resolver; unpriced = hard error),
  `store` (atomic-snapshot rebind), `checkconfig` (assignable/priced/keys/pm checks).
- **Orchestrator (§3):** pure `planner` (every run-type stage graph + dependency chain +
  meta firewall) and `rotation`; `engine` (create_run, promote_run with coverage
  proposed→initiating, advance_run draining via a runner, register predictions, append
  cross_check on a material ΔTP, per-run budget cap → `budget_cap` gate, finalize at the
  initiation gate / succeed, fail on terminal stage failure), `tick`; pure cross-check
  rule (reads registered predictions, not prose).
- **FakeRunner (§9.1):** claims via the real `queue.claim_stage`, canned per-role results,
  configurable fail/hang — the whole flow proven with zero LLM.
- **API (§5):** FastAPI app + `require_pm` (bearer + allowlist) + `Idempotency`
  (body_hash replay / 409) + structured error envelope; coverage/runs/gates/health routes.
- **CLI (§6):** `fund` httpx client; `ROUTE_COMMAND_MAP` parity asserted total vs the
  router (an added route without a command fails CI).
- **Scheduler (§4):** `session_tick`/`watch_tick` (deterministic `trigger_ref`, idempotent
  double-fire), `schedule_watchdog` (stale-job detection), leader advisory-lock helper.

**Fixes worth flagging:**
- Coverage lock is a **run-lifecycle** concern (acquire in `runs_repo.start_run`, release
  in `finish_run`); removed `acquire_lock`/`release_lock` from the coverage SM side effects
  (SPEC-DOMAIN §5 vs SPEC-CORE §3.3 were redundant) — a genuine cross-spec reconciliation.
- API tests use `StaticPool` so the SQLite `:memory:` DB is shared across TestClient's
  portal thread, and are marked `allow_network` (TestClient's anyio bridge uses a
  socketpair — not an outbound connection).
- The required `core_api_token` meant alembic's `env.py` (which reads `get_settings()`)
  needs `CORE_API_TOKEN` in the integration env — set in the integration conftest.
- B008 ignored for `core/api/**` (FastAPI's `Depends()` in defaults idiom).

**Deferred (tracked, do not block the merge):**
- §9.2 transient `running→queued` re-entry test: a retryable stage failure is currently
  absorbed within `FakeRunner.drain` (the stage requeues with `available_at=now` and
  re-succeeds in the same drain). True run-level pause/resume on a retryable failure (with
  config-driven backoff) is the phase-2 runner's natural home; revisit with SPEC-RUNNER.
- §3.5 per-house **daily** budgets (`house_budget_days` reserve/settle): only the per-run
  cap is enforced; the per-house daily cap + desk notice is deferred to the runner/cost
  port (phase 2/7.2).
- Research read-model API routes (queries/predictions/track-record/events/costs): the
  lifecycle routes (coverage/runs/gates/health) are implemented; the read models are
  stubbed and land with their owning specs (SPEC-MONITORING/TRACKREC/ADAPTER).
- Leader advisory lock: `pg_try_advisory_lock` is wired; the two-core-processes
  exclusivity is an integration test that needs a second process (deferred).

Phase 1.3 BUILD (spec-domain + spec-core) is complete. Next per PROMPTS: Phase 1.4 CHORE
(port v2 infra: retry_policy, rate_budget, logging/redaction, shutdown, async_utils).
