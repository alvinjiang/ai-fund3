# AI Fund v3 — Implementation Roadmap (steps and prompts)

Status: Approved design 2026-07-07. This is the **single execution file**: a future
session (human + agent) works through the phases below in order. Each step names the
model tier it needs and contains a copy-paste prompt.

## How to use this file

- **Read order for any new session:** `redesign/00` (decisions) → `01` (principles) →
  the docs a step names. Do not start coding from this file alone.
- **Model tiers:**
  - **SPEC steps → strongest available reasoning model** (Opus-class). They turn
    design docs into detailed, self-contained specs — the v2 codebase was built well
    this way (spec → implement), keep the pattern.
  - **BUILD steps → mid-tier coding model** (Sonnet-class) driven by the spec.
  - **CHORE steps → small model** (Haiku-class): mechanical ports, config, data entry.
- **Conventions (carry over from v2, they worked):** one branch per spec, PR review
  before merge; dated NOTES.md entries per change; README updated on every feature
  branch; TDD; unit tests block network by default; no model names/prices hardcoded —
  registry/config only; secrets never in logs or commits.
- **PM gates** are marked ⛔ — stop and get Alvin's sign-off before continuing.
- Each SPEC step's output goes to `specs/` in the v3 repo; BUILD prompts then
  reference that spec file. Specs must be self-contained (a builder should not need
  the redesign docs).

---

## Phase 0 — Kickoff (½ day)

### 0.1 CHORE — Repo scaffolding
⛔ PM confirms first: fresh repo `ai-fund3` (recommended, see 06-MIGRATION-MAP.md) or
subtree in the existing repo.

> Create the v3 repository skeleton: `core/`, `runner/`, `adapter/`, `cli/`,
> `doctrine/`, `dossiers/` (separate git repo, init empty), `specs/`, `tests/`,
> `pyproject.toml` (Python 3.12, ruff, pytest), pre-commit config, `.env.example`,
> `README.md` stub describing the v3 architecture in ~20 lines (source:
> redesign/01 and 05), `NOTES.md` with a first dated entry. Copy the eight
> `redesign/*.md` docs from the v2 repo into `design/`. Add a `CONVENTIONS.md`
> capturing the conventions block from redesign/07.

### 0.2 CHORE — Seed doctrine
> Split `Equity-Research-Prompts_2026-06-05.md` (v2 repo root) into the doctrine
> layout defined in redesign/03 §3: `core.md` (Additional Notes + past-mistakes
> subsections that are process rules), `sections/*.md` (the 14 section prompts,
> one file each), `lessons/global.md` (the "Further notes from past mistakes"
> content), `report/full-report.md` (Full reports + Workflow sections, file-naming
> rules). Copy the ironclad HTML template from the Yakult sample
> (scratchpad or PM copy) into `doctrine/templates/`. Preserve wording — these
> prompts are proven; reorganize, do not rewrite. Cross-check the 14 sections
> against v2 `prompts/equity_prompts.py` and note any drift in NOTES.md.

### 0.3 CHORE — Place role prompts
Drafts already written (by the Fable design session, 2026-07-07) in the v2 repo at
`redesign/doctrine-seed/roles/` — the verifier prompt encodes the PM's proven
formula and is the highest-leverage text in the system; do not weaken it.
> Copy `redesign/doctrine-seed/roles/*.md` into `doctrine/roles/` unchanged.
> Read each against doctrine/core.md for terminology consistency (report a diff,
> don't silently edit). Expect these files to be tuned during the Phase 2/3 PM
> gates; every tuning change gets a NOTES.md entry.

---

## Phase 1 — Core skeleton (spec 1–2 days, build 3–5 days)

### 1.1 SPEC — Domain schema & coverage state machine
> Read design/02 fully, design/05 §1/§8, and v2 `db/models.py` +
> `docs/SPEC_PREDICTION_TRACKING.md` (schema prior art). Produce
> `specs/SPEC-DOMAIN.md`: exact SQLAlchemy models + alembic baseline for coverage,
> runs, run_stages, predictions, events, houses, plus the ported tables listed in
> design/06 (positions, trades, cash, risk_configs, risk_events,
> portfolio_snapshots, llm_usage+run refs, audit_log, scheduler_job_logs,
> news_articles). Define every state transition with
> actor/cause/audit requirements, the PG-queue claiming query (SKIP LOCKED), and
> the dossier-repo index table. Include test plan.

### 1.2 SPEC — Orchestrator, config, core API, CLI
> Read design/02 §3, design/03 §2 (run graphs), design/05 §1/§3/§4/§6. Produce
> `specs/SPEC-CORE.md`: run orchestrator (create run → expand type into stages →
> queue → collect results → gates → PM-gate states, incl. the running→queued
> transient-failure re-entry from design/02 §3), APScheduler job set,
> config layout (/etc/ai-fund: houses.yaml, fund.yaml, pricing.yaml, .env) with
> pydantic-settings, checkconfig port, the core API routes (coverage lifecycle,
> runs, decisions, track-record, costs), and the `fund` CLI mirroring every route.
> Stage execution itself is out of scope (Phase 2). Include test plan.

### 1.3 BUILD — Implement 1.1 then 1.2
> Implement `specs/SPEC-DOMAIN.md` (branch `spec-domain`), then `specs/SPEC-CORE.md`
> (branch `spec-core`), TDD, following CONVENTIONS.md. Stage execution is stubbed
> with a fake runner that marks stages succeeded and writes canned stage_result.yaml
> — the orchestrator, gates, and PM-gate flow must be fully testable without LLMs.

### 1.4 CHORE — Port infra
> Port from v2 repo into `core/infra/`: retry_policy.py, rate_budget.py, logging.py
> (secret redaction), shutdown.py, async_utils.py, with their unit tests. Adapt
> imports only; note any dead code dropped in NOTES.md.

---

## Phase 2 — Harness runner + first real initiation (the risk phase)

Goal: prove the two-tier bet — one house, one ticker, end-to-end, meeting the Yakult
bar. Do this BEFORE building everything else; if harness quality/ops disappoint,
the design adjusts here cheaply.

### 2.1 SPEC — Runner & harness drivers
> Read design/05 §2 fully, design/03 §1. Produce `specs/SPEC-RUNNER.md`: the
> HarnessDriver interface (launch/monitor/collect/kill), sandbox spec (container or
> bwrap; per-house key injection; egress allowlist: provider endpoint, market-data
> service, search endpoint; wall-clock + budget kill), workspace lifecycle (git
> worktree per run branch, task.md generation, doctrine ro-mount), stage-gate
> validators (stage_result schema, required artifacts by role, reconciliation
> script report↔workbook, no-invented-confidence check), transcript + cost capture
> per provider (usage API where available, transcript parse otherwise), bounded
> retry with named omissions. Pick the first house pragmatically: whichever of
> Codex CLI / Gemini CLI the operator can authenticate fastest. Include test plan
> (fake harness binary for unit tests).

### 2.2 BUILD — Implement runner
> Implement `specs/SPEC-RUNNER.md` (branch `spec-runner`), TDD with a fake harness.
> Real-harness integration test behind a manual flag.

### 2.3 CHORE — Market-data service (minimal)
> Port v2 `data_sources/` (router, fx, freshness, exchange_registry; jquants +
> sec_edgar as-is) and expose GET endpoints (pinned price with source+timestamp,
> history, FX) as a localhost service reachable from sandboxes. Full monitor
> integration comes in Phase 4; this step only serves the pipeline's price needs.

### 2.4 ⛔ PM GATE — Pilot initiation
> Run a real initiation for one PM-chosen ticker with lead = the configured house
> and verify passes = 1 (same house is acceptable if only one house is set up;
> note it). Deliver report-content.md, workbook, build script, corrections log,
> rendered PDF. PM compares against the Yakult standard and grades: artifacts
> complete? numbers reconciled? citations real? Iterate role prompts/doctrine and
> runner gates until the PM passes it. Record every fix as a dated NOTES.md entry —
> these are the first doctrine lessons.

---

## Phase 3 — Full pipeline & dossiers

### 3.1 SPEC — Initiation pipeline complete
> Read design/03 §2.1, design/02 §2 (dossier contract), design/04 §1–2. Produce
> `specs/SPEC-INITIATION.md`: multi-house stage graph (author → verify×2 rotating
> contributors → finalizer), dossier file contracts (dossier.md frontmatter,
> valuation.md, tripwires.yaml schema, lessons.md, predictions.yaml mirror),
> prediction registration from stage_result, PM decision gate + coverage
> transition + channel-creation event, corrections attribution into the DB,
> configurable verify count, per-run budget behavior at cap (pause waiting_pm).
> Include the remaining houses' harness drivers (from 2.1 interface) as sub-tasks.

### 3.2 BUILD — Implement 3.1
> Implement `specs/SPEC-INITIATION.md`, branch `spec-initiation`, TDD (fake
> harness), plus one real multi-house pilot behind manual flag.

### 3.3 ⛔ PM GATE — Multi-house pilot
> Real initiation on a second ticker: lead house A, verifiers houses B and C.
> PM grades against Yakult bar and reviews the corrections log quality (are
> verifiers catching real errors?). Tune rotation/prompts until passed.

---

## Phase 4 — Monitoring & events

### 4.1 SPEC — Monitor ticks, tripwires, event analysis
> Read design/03 §2.3–2.4, design/02 §2 (tripwires). Produce
> `specs/SPEC-MONITORING.md`: scheduler-emitted per-exchange ticks by tier;
> deterministic price/level evaluation; news port (fetchers + dedup from v2
> `news/`) with tripwire-scoring call (single cheap model call, structured
> output); escalation policy (severity → event_analysis spawn / digest / silence)
> with threshold gating; event_analysis run graph incl. auto cross-check rule
> (|ΔTP|>10% or stance change or thesis tripwire) and structured-disagreement
> record; digest formatting for the desk; pgvector keep/drop decision.
### 4.2 BUILD — Implement 4.1 (branch `spec-monitoring`)
### 4.3 CHORE — Backfill tripwires for any pilot tickers via a deep_review run.

---

## Phase 5 — Mattermost adapter & CLI parity

### 5.1 SPEC — Adapter
> Read design/05 §3, v2 `mattermost/` (poster, bots, auth, channel_naming — port;
> router — mine skip/routing rules only). Produce `specs/SPEC-ADAPTER.md`:
> ws→core-API translation, bot-per-house + desk posting identities, coverage-driven
> channel lifecycle, slash commands + mentions mapped to API routes (propose,
> decide, review, analyze, track-record, portfolio, risk, cost, trade confirm),
> pm_query flow (lead's light model via api substrate, dossier-cited answers),
> delivery of run outputs (PDF + summary + gate messages), `reconcile` command
> (mined from v2 bootstrap_integrations), error surfacing (every failed command
> answers in-channel — no silent failures).
### 5.2 BUILD — Implement 5.1 (branch `spec-adapter`)
### 5.3 ⛔ PM GATE — Drive a full initiation + decision + a monitor escalation
entirely from Mattermost on the staging server.

---

## Phase 6 — Track record & reviews

### 6.1 SPEC — Scoring, calibration, deep_review, lead_review
> Read design/02 §4, design/03 §2.2/§2.6, design/04 §3, and v2
> `docs/SPEC_PREDICTION_TRACKING.md` (prior art). Produce `specs/SPEC-TRACKREC.md`:
> scoring job (price-history based hit/miss/expiry, supersede handling),
> per-house metric views incl. corrections-received rate and calibration curve,
> `/track-record` surfaces, earnings-calendar + quarterly deep_review scheduling,
> lead_review run (contributor ballots via stage_result, aggregation, PM approval
> flow, handover bookkeeping), track-record injection into lead_review and
> distillation contexts, PM 👍/👎 rating capture on event notes.
### 6.2 BUILD — Implement 6.1 (branch `spec-trackrec`)

---

## Phase 7 — Meta, remaining ports, cutover

### 7.1 SPEC+BUILD — Distillation (Claude meta)
> Read design/03 §2.7, design/04 §4. Spec + implement the monthly distillation run:
> gather corrections/misses/lessons since last run → Claude harness stage →
> doctrine amendment as git diff + system-health memo → PM approval flow → doctrine
> version bump recorded. Small spec; one branch.
### 7.2 CHORE — Port risk engine + trades/cash + cost rollups
> Port v2 `risk/`, `trades/`, remaining `cost/` reporting with tests; wire risk
> alerts and sizing advice to the desk channel; trade confirmation through adapter.
### 7.3 CHORE — Data migration + legacy import
> Per design/06: copy positions/cash/trades/risk configs; import Yakult + LINK
> report artifacts into their dossiers (extraction pass on cheap model registers
> dated predictions retroactively where stated).
### 7.4 ⛔ PM GATE — Cutover
> Initiations run for the PM's chosen active-tier list (batched, budget-aware);
> two clean weeks of v3 monitoring alongside v2; then freeze v2, repoint bots,
> decommission per design/06 cutover/rollback plan.

---

## Standing risks to watch (from the design session)

1. **Harness ops** is the novel surface: sandbox networking, non-interactive CLI
   quirks, cost capture per provider. Phase 2 exists to de-risk it first — do not
   reorder.
2. **Quality regression vs the manual process**: the PM gates (2.4, 3.3) are the
   defense; treat every gate failure as doctrine/prompt work before code work.
3. **Cost drift**: enforce caps from Phase 2 onward; nightly reconciliation of
   metered vs provider-reported spend.
4. **Scope creep toward v2 features** (debates, personas, watch modes): the
   migration map's "retire" list is deliberate; revisit only after cutover.
