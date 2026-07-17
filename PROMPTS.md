# ai-fund3 — Work Prompts (phase-ordered)

> **Single execution file.** A fresh session works top to bottom. Source:
> `design/07-IMPLEMENTATION-ROADMAP.md`, adapted to this repo with the Phase 0.1 PM
> gate (fresh repo) already cleared and the design docs + role prompts already in place.
> Each step's spec output goes to `specs/`; BUILD prompts reference that spec file.
> Specs must be self-contained (a builder should not need the design docs).

## Read first (before any code)

`design/00-DECISIONS-LOG.md` → `design/01-VISION-AND-PRINCIPLES.md` → the docs a step
names. **Do not start coding from this file alone.**

## Confirmed (PM 2026-07-07; transfer 2026-07-12)

- Fresh repo `ai-fund3` (this repo). v2 stays at `/home/alvin/aicode/ai-fund`,
  running until the Phase 7 cutover.
- Design docs in `design/`. Role prompts in `doctrine/roles/` (copied unchanged from
  the 2026-07-07 design session).
- Proprietary inputs in `proprietary/` (gitignored — never commit): the doctrine
  source `Equity-Research-Prompts_2026-06-05.md`, and the two gold-standard report
  zips `yakult-20260610.zip`, `LINK Reit Equity Research 20260606.zip`.
- First harness house (Phase 2.1): whichever of Codex CLI / Gemini CLI the operator
  can authenticate fastest on the host.
- pgvector keep/drop decision deferred to Phase 4.

## Model tiers

- **SPEC** → strongest available reasoning model (Opus-class). Turns design docs into
  detailed, self-contained specs. (The v2 codebase was built well this way: spec →
  implement. Keep the pattern.)
- **BUILD** → mid-tier coding model (Sonnet-class), driven by the spec.
- **CHORE** → small model (Haiku-class): mechanical ports, config, data entry.

## Conventions (carry over from v2 — they worked)

The full, durable contract — Absolute Rules (secrets), workflow, spec checklist, test
isolation, code rules, retired v2 concepts — lives in **`AGENTS.md`** (opencode reads
it at session start). Read it before coding. Headline: one branch per spec with PR
review; spec → implement; TDD; unit tests block network by default; no model names/
prices hardcoded; secrets never in logs or commits; operator config outside the deploy
tree.

## PM gates ⛔

Stop and get Alvin's sign-off before continuing. Marked ⛔.

## Repo paths

- v2 source (for porting): `/home/alvin/aicode/ai-fund`
- v3 (this repo): `/home/alvin/aicode/ai-fund3`
- Specs output: `specs/SPEC-*.md`

---

## Phase 0 — Kickoff (½ day)

### 0.1 CHORE — Repo scaffolding  [⛔ PM gate CLEARED: fresh repo confirmed]

**Already done (transfer session 2026-07-12):** repo dir created and `git init` run
(no commit yet). Copied in: `design/` (8 docs), `doctrine/roles/` (8 role prompts +
README), `proprietary/` (doctrine source + gold-standard zips, gitignored). Process/
convention memory ported from v2 (history not copied): `AGENTS.md` (the agent contract
— Absolute Rules + conventions, succeeding v2's NOTES.md role; opencode reads it at
session start), `docs/SPEC_AUTHORING_CHECKLIST.md`, `pytest.ini` (network-isolation
markers), `.pre-commit-config.yaml` (gitleaks + pre-commit-hooks + ruff). Starter
`.gitignore`, `README.md`, `NOTES.md`, `PROMPTS.md` also written.

**TODO (this step):**

> Create the directory skeleton: `core/`, `runner/`, `adapter/`, `cli/`,
> `doctrine/{sections,lessons,report,templates}`, `dossiers/` (separate git repo —
> init it separately with a first empty commit; per `design/06` it is its own repo),
> `specs/`, `tests/`. Add `pyproject.toml` (Python 3.12, ruff, pytest; fold in the
> `pytest.ini` markers or keep both), `pre-commit install`, and `.env.example`.
> Expand `README.md` from the stub (describe the v3 architecture in ~20 lines; source
> `design/01` and `design/05`). The durable conventions already live in `AGENTS.md` —
> create `CONVENTIONS.md` only if you want a tool-agnostic mirror. Make the initial
> git commit (design docs, doctrine/roles, AGENTS.md, scaffolding — never
> `proprietary/`).

### 0.2 CHORE — Seed doctrine

> Split `proprietary/Equity-Research-Prompts_2026-06-05.md` into the doctrine layout
> defined in `design/03` §3: `doctrine/core.md` (Additional Notes + past-mistakes
> subsections that are process rules), `doctrine/sections/*.md` (the 14 section
> prompts, one file each), `doctrine/lessons/global.md` (the "Further notes from past
> mistakes" content), `doctrine/report/full-report.md` (Full reports + Workflow
> sections, file-naming rules). Copy the ironclad HTML template from the Yakult sample
> — extract `proprietary/yakult-20260610.zip` and locate the template — into
> `doctrine/templates/`. **Preserve wording — these prompts are proven; reorganize,
> do not rewrite.** Cross-check the 14 sections against v2
> `/home/alvin/aicode/ai-fund/prompts/equity_prompts.py` and note any drift in
> `NOTES.md`.

### 0.3 CHORE — Place role prompts  [role files ALREADY in `doctrine/roles/`; do the review]

> `doctrine/roles/*.md` are in place (copied unchanged from the design session). Once
> `doctrine/core.md` exists (after 0.2), read each role prompt against `core.md` for
> terminology consistency (report a diff, don't silently edit). Expect these to be
> tuned during the Phase 2/3 PM gates; every tuning change gets a `NOTES.md` entry.
> The `verifier.md` prompt encodes the PM's proven multi-pass formula — it is the
> highest-leverage text in the system; do not weaken it.

---

## Phase 1 — Core skeleton (spec 1–2 days, build 3–5 days)

### 1.1 SPEC — Domain schema & coverage state machine

> Read `design/02` fully, `design/05` §1/§8, and v2 `/home/alvin/aicode/ai-fund/db/models.py`
> + `/home/alvin/aicode/ai-fund/docs/SPEC_PREDICTION_TRACKING.md` (schema prior art).
> Produce `specs/SPEC-DOMAIN.md`: exact SQLAlchemy models + alembic baseline for
> coverage, runs, run_stages, predictions, events, houses, plus the ported tables
> listed in `design/06` (positions, trades, cash, risk_configs, risk_events,
> portfolio_snapshots, llm_usage+run refs, audit_log, scheduler_job_logs,
> news_articles). Define every state transition with actor/cause/audit requirements,
> the PG-queue claiming query (`SKIP LOCKED`), and the dossier-repo index table.
> Include test plan.

### 1.2 SPEC — Orchestrator, config, core API, CLI

> Read `design/02` §3, `design/03` §2 (run graphs), `design/05` §1/§3/§4/§6. Produce
> `specs/SPEC-CORE.md`: run orchestrator (create run → expand type into stages →
> queue → collect results → gates → PM-gate states, incl. the `running→queued`
> transient-failure re-entry from `design/02` §3), APScheduler job set, config layout
> (`/etc/ai-fund`: `houses.yaml`, `fund.yaml`, `pricing.yaml`, `.env`) with
> pydantic-settings, checkconfig port, the core API routes (coverage lifecycle, runs,
> decisions, track-record, costs), and the `fund` CLI mirroring every route. Stage
> execution itself is out of scope (Phase 2). Include test plan.

### 1.3 BUILD — Implement 1.1 then 1.2

> Implement `specs/SPEC-DOMAIN.md` (branch `spec-domain`), then `specs/SPEC-CORE.md`
> (branch `spec-core`), TDD, following `CONVENTIONS.md`. Stage execution is stubbed
> with a fake runner that marks stages succeeded and writes canned `stage_result.yaml`
> — the orchestrator, gates, and PM-gate flow must be fully testable without LLMs.

### 1.4 CHORE — Port infra

> Port from v2 `/home/alvin/aicode/ai-fund/infra/` into `core/infra/`:
> `retry_policy.py`, `rate_budget.py`, `logging.py` (secret redaction), `shutdown.py`,
> `async_utils.py`, with their unit tests. Adapt imports only; note any dead code
> dropped in `NOTES.md`.

---

## Phase 2 — Harness runner + first real initiation (the risk phase)

Goal: prove the two-tier bet — one house, one ticker, end-to-end, meeting the Yakult
bar. Do this BEFORE building everything else; if harness quality/ops disappoint, the
design adjusts here cheaply. **Do not reorder.**

### 2.1 SPEC — Runner & harness drivers

> Read `design/05` §2 fully, `design/03` §1. Produce `specs/SPEC-RUNNER.md`: the
> `HarnessDriver` interface (launch/monitor/collect/kill), sandbox spec (container or
> bwrap; per-house key injection; egress allowlist: provider endpoint, market-data
> service, search endpoint; wall-clock + budget kill), workspace lifecycle (git worktree
> per run branch, `task.md` generation, doctrine ro-mount), stage-gate validators
> (`stage_result` schema, required artifacts by role, reconciliation script
> report↔workbook, no-invented-confidence check), transcript + cost capture per
> provider (usage API where available, transcript parse otherwise), bounded retry with
> named omissions. Pick the first house pragmatically: whichever of Codex CLI /
> Gemini CLI the operator can authenticate fastest. Include test plan (fake harness
> binary for unit tests).

### 2.2 BUILD — Implement runner

> Implement `specs/SPEC-RUNNER.md` (branch `spec-runner`), TDD with a fake harness.
> Real-harness integration test behind a manual flag.

### 2.3 SPEC done → CHORE — Market-data service  [spec: `specs/SPEC-MARKETDATA.md`]

> **Interface already specified** (2026-07-18 Fable session — the v2 port alone would
> not have met what later specs assume: pinned close per trading date, dated FX,
> adjustment factors + dividends, halt signal, earnings-calendar tiers). Implement
> `specs/SPEC-MARKETDATA.md`: port v2 `/home/alvin/aicode/ai-fund/data_sources/`
> (router, fx, freshness, exchange_registry; jquants + sec_edgar) behind the specced
> endpoints as a localhost service reachable from sandboxes. JP provider order
> DECIDED (PM 2026-07-18): `yfinance`-primary, J-Quants free plan as delayed
> cross-check (spec §3.3). Full monitor integration comes in Phase 4; this step
> serves the pipeline's price needs.

### 2.4 ⛔ PM GATE — Pilot initiation

> Run a real initiation for one PM-chosen ticker with lead = the configured house and
> verify passes = 1 (same house is acceptable if only one house is set up; note it).
> Deliver `report-content.md`, workbook, build script, corrections log, rendered PDF.
> PM compares against the Yakult standard and grades: artifacts complete? numbers
> reconciled? citations real? Iterate role prompts/doctrine and runner gates until the
> PM passes it. Record every fix as a dated `NOTES.md` entry — these are the first
> doctrine lessons.

---

## Phase 3 — Full pipeline & dossiers

### 3.1 SPEC — Initiation pipeline complete

> Read `design/03` §2.1, `design/02` §2 (dossier contract), `design/04` §1–2. Produce
> `specs/SPEC-INITIATION.md`: multi-house stage graph (author → verify×2 rotating
> contributors → finalizer), dossier file contracts (`dossier.md` frontmatter,
> `valuation.md`, `tripwires.yaml` schema, `lessons.md`, `predictions.yaml` mirror),
> prediction registration from `stage_result`, PM decision gate + coverage transition +
> channel-creation event, corrections attribution into the DB, configurable verify
> count, per-run budget behavior at cap (pause `waiting_pm`). Include the remaining
> houses' harness drivers (from 2.1 interface) as sub-tasks.

### 3.2 BUILD — Implement 3.1

> Implement `specs/SPEC-INITIATION.md`, branch `spec-initiation`, TDD (fake harness),
> plus one real multi-house pilot behind manual flag.

### 3.3 ⛔ PM GATE — Multi-house pilot

> Real initiation on a second ticker: lead house A, verifiers houses B and C. PM grades
> against the Yakult bar and reviews the corrections log quality (are verifiers
> catching real errors?). Tune rotation/prompts until passed.

---

## Phase 4 — Monitoring & events

### 4.1 SPEC — Monitor ticks, tripwires, event analysis

> Read `design/03` §2.3–2.4, `design/02` §2 (tripwires). Produce
> `specs/SPEC-MONITORING.md`: scheduler-emitted per-exchange ticks by tier;
> deterministic price/level evaluation; news port (fetchers + dedup from v2
> `/home/alvin/aicode/ai-fund/news/`) with tripwire-scoring call (single cheap model
> call, structured output); escalation policy (severity → `event_analysis` spawn /
> digest / silence) with threshold gating; `event_analysis` run graph incl. auto
> cross-check rule (|ΔTP|>10% or stance change or thesis tripwire) and
> structured-disagreement record; digest formatting for the desk; pgvector keep/drop
> decision (deferred item — decide here).

### 4.2 BUILD — Implement 4.1 (branch `spec-monitoring`)

### 4.3 CHORE — Backfill tripwires for any pilot tickers via a `deep_review` run.

---

## Phase 5 — Mattermost adapter & CLI parity

### 5.1 SPEC — Adapter

> Read `design/05` §3, v2 `/home/alvin/aicode/ai-fund/mattermost/` (poster, bots,
> auth, channel_naming — port; router — mine skip/routing rules only). Produce
> `specs/SPEC-ADAPTER.md`: ws→core-API translation, bot-per-house + desk posting
> identities, coverage-driven channel lifecycle, slash commands + mentions mapped to
> API routes (propose, decide, review, analyze, track-record, portfolio, risk, cost,
> trade confirm), `pm_query` flow (lead's light model via api substrate, dossier-cited
> answers), delivery of run outputs (PDF + summary + gate messages), `reconcile`
> command (mined from v2
> `/home/alvin/aicode/ai-fund/scripts/bootstrap_integrations.py`), error surfacing
> (every failed command answers in-channel — no silent failures).

### 5.2 BUILD — Implement 5.1 (branch `spec-adapter`)

### 5.3 ⛔ PM GATE — Drive a full initiation + decision + a monitor escalation entirely from Mattermost on the staging server.

---

## Phase 6 — Track record & reviews

### 6.1 SPEC — Scoring, calibration, deep_review, lead_review

> Read `design/02` §4, `design/03` §2.2/§2.6, `design/04` §3, and v2
> `/home/alvin/aicode/ai-fund/docs/SPEC_PREDICTION_TRACKING.md` (prior art). Produce
> `specs/SPEC-TRACKREC.md`: scoring job (price-history based hit/miss/expiry, supersede
> handling), per-house metric views incl. corrections-received rate and calibration
> curve, `/track-record` surfaces, earnings-calendar + quarterly `deep_review`
> scheduling, `lead_review` run (contributor ballots via `stage_result`, aggregation,
> PM approval flow, handover bookkeeping), track-record injection into `lead_review`
> and distillation contexts, PM 👍/👎 rating capture on event notes.

### 6.2 BUILD — Implement 6.1 (branch `spec-trackrec`)

---

## Phase 7 — Meta, remaining ports, cutover

### 7.1 SPEC+BUILD — Distillation (Claude meta)

> Read `design/03` §2.7, `design/04` §4. Spec + implement the monthly distillation run:
> gather corrections/misses/lessons since last run → Claude harness stage → doctrine
> amendment as git diff + system-health memo → PM approval flow → doctrine version bump
> recorded. Small spec; one branch.

### 7.2 CHORE — Port risk engine + trades/cash + cost rollups

> Port v2 `/home/alvin/aicode/ai-fund/{risk,trades}` and remaining `cost/` reporting
> with tests; wire risk alerts and sizing advice to the desk channel; trade
> confirmation through the adapter.

### 7.3 CHORE — Data migration + legacy import

> Per `design/06`: copy positions/cash/trades/risk configs; import the Yakult + LINK
> report artifacts (from `proprietary/*.zip`) into their dossiers (extraction pass on
> cheap model registers dated predictions retroactively where stated).

### 7.4 ⛔ PM GATE — Cutover

> Initiations run for the PM's chosen active-tier list (batched, budget-aware); two
> clean weeks of v3 monitoring alongside v2; then freeze v2, repoint bots, decommission
> per `design/06` cutover/rollback plan.

---

## Standing risks to watch (from the design session)

1. **Harness ops** is the novel surface: sandbox networking, non-interactive CLI
   quirks, cost capture per provider. Phase 2 exists to de-risk it first — do not
   reorder.
2. **Quality regression vs the manual process**: the PM gates (2.4, 3.3) are the
   defense; treat every gate failure as doctrine/prompt work before code work.
3. **Cost drift**: enforce caps from Phase 2 onward; nightly reconciliation of metered
   vs provider-reported spend.
4. **Scope creep toward v2 features** (debates, personas, watch modes): the migration
   map's "retire" list is deliberate; revisit only after cutover.
