# SPEC-INITIATION — Initiation pipeline (complete), dossier contracts, PM decision gate

**Phase:** 3.1 (SPEC) · **Implements:** PROMPTS.md 3.1 · **Branch:** `spec-initiation`
**Status:** ready for BUILD (after `spec-runner`; **preliminary pending PM gate 2.4**, §12)
**Depends on:** SPEC-DOMAIN (coverage/runs/predictions/corrections), SPEC-CORE
(orchestrator, planner, gates, budgets), SPEC-RUNNER (drivers, sandbox, stage gates)
**Consumed by:** SPEC-MONITORING (tripwires + dossier), SPEC-ADAPTER (delivery),
SPEC-TRACKREC (corrections, predictions), SPEC-DISTILLATION (lessons)

The initiation pipeline is **the front door of coverage** and the reason v3 exists: it
formalizes the manual workflow that produced the PM's gold-standard reports — a strong
author pass, then verification passes by *different* model houses instructed to verify,
correct, and re-reason (**not** to critique) — and it leaves behind a dossier that every
later run builds on.

Self-contained: implementable without reading `design/`.

---

## 1. Scope

- The multi-house stage graph (author → verify × N rotating contributors → finalizer),
  with configurable verify count and the optional cheap `data_checker` pass.
- The **dossier file contracts** — the exact schema of `dossier.md`, `valuation.md`,
  `tripwires.yaml`, `lessons.md`, `predictions.yaml`, `reports/`, `events/`, `queries/` —
  and the deterministic validators that enforce them.
- **Prediction registration** from `stage_result.yaml` into the ledger.
- **Corrections attribution** into the DB (the earliest per-house quality signal).
- The **PM decision gate**: payload, allowed answers, and the transactional effects of
  each answer (coverage transition, branch merge, levels, channel creation, monitoring
  start).
- **Per-run budget behavior at cap** (pause `waiting_pm`, never degrade).
- Sub-tasks: the **remaining houses' harness drivers** against the SPEC-RUNNER interface.

Out of scope: monitoring/tripwire evaluation (SPEC-MONITORING), scoring (SPEC-TRACKREC),
Mattermost rendering (SPEC-ADAPTER).

---

## 2. Stage graph

`initiation` is created by `POST /coverage` + `POST /coverage/{slug}/initiate`
(or `fund propose … && fund initiate …`, or `/propose` in chat). Coverage must be
`proposed` or `failed`.

| seq | Role | House | Substrate | Model tier | Depends on |
|---|---|---|---|---|---|
| 1 | `author` | **lead** (PM-specified; else the orchestrator's track-record suggestion, which the PM confirms) | harness | heavy | — |
| 2 | `verifier` | contributor A (rotation) | harness | heavy | 1 |
| 3 | `verifier` | contributor B (rotation) | harness | heavy | 2 |
| 3a | `data_checker` *(optional)* | cheapest enabled house | harness | **light** | last verifier |
| 4 | `finalizer` | **lead** | harness | heavy | previous |

- **`verify_count`** (`fund.yaml runs.initiation.verify_count`, default 2; range 1–3;
  overridable per run via `POST /coverage/{slug}/initiate {verify_count}`). A watch-tier
  initiation the PM wants cheap runs with 1. **Verifiers are always houses other than the
  lead** — that difference is where the quality comes from.
- If fewer distinct contributors are enabled than `verify_count`, the planner uses what
  exists and records a `plan_note` on the run (`"verify_count reduced to 1: only one
  contributor enabled"`), which is surfaced in the gate payload. **It never silently
  reuses the lead as its own verifier**; if zero contributors exist, the run is created
  with `verify_count=0` **only if** the PM passed `--allow-single-house` (the 2.4 pilot
  case: "same house is acceptable if only one house is set up; note it"), and the gate
  payload says so in bold.
- **Rotation** (SPEC-CORE §3.2): contributors ordered by `(last_used_at ASC, key ASC)`
  over prior stage rows *on this coverage*, so verify pairs do not calcify.
- Stages run **sequentially** (each verifier builds on the previous one's corrected
  artifacts). The **last** verifier additionally re-pins the subject price fresh and
  re-runs reconciliation (its `task.md` says so explicitly).

Every stage runs through the SPEC-RUNNER lifecycle: sandboxed harness session →
deterministic gates → bounded retry with named omissions → dossier commit on `run/<id>`.

---

## 3. Dossier contracts

One directory per ticker inside the single `dossiers/` git repo. The dossier **is** the
fund's memory: every run reads it and must leave it updated, or explicitly declare
`nothing_material`.

```
dossiers/<slug>/                     # slug = coverage.dossier_slug, e.g. tse_2267
  dossier.md          # thesis (frontmatter + required sections)
  valuation.md        # TP, method weights, scenarios, entry points (frontmatter)
  tripwires.yaml      # falsifiable monitor conditions
  lessons.md          # stock-specific lessons (append-only)
  predictions.yaml    # READ-ONLY mirror of the ledger (system-generated)
  events/             # YYYYMMDD-<slug>.md event analyses
  reports/            # initiation & review artifact set
  queries/            # notable PM Q&A (curated)
```

`runner/gates/dossier.py` validates every file below on any stage that writes it. A
schema violation fails the stage with the specific field named (bounded retry, §
SPEC-RUNNER 6.4).

### 3.1 `dossier.md`

```yaml
---
slug: tse_2267
ticker: "2267"
exchange: tse
name: Yakult Honsha
currency: JPY
stance: buy            # buy | accumulate | hold | reduce | sell | avoid
conviction: medium     # high | medium | low
as_of: 2026-06-10      # the date the thesis reflects (not the file's mtime)
lead_house: gpt
updated_by_run: 3f2c…  # run id
doctrine_version: v1
---
```

Required H2 sections (heading text is checked, order is not): `## Business model`,
`## Moat`, `## Key drivers`, `## Bull case`, `## Bear case`, `## What the market
believes and where we differ`, `## Stance`. A section may say "not applicable" **with a
reason** — an empty section fails.

`stance` in the frontmatter **must equal** `stage_result.recommendation` of the stage
that wrote it (cross-check gate: the file and the machine-readable result cannot
disagree).

### 3.2 `valuation.md`

```yaml
---
as_of: 2026-06-10
pinned_price:
  value: 2745
  currency: JPY
  as_of: 2026-06-09          # trading date of the pinned close
  source: "market-data:tse"
target_price:
  value: 2900
  currency: JPY
  horizon_date: 2027-06-30
  confidence: 0.65           # stated by the model; never synthesized
  upside_pct: 5.6            # must equal (target/pinned - 1) within 0.1pp
method_weights: { dcf: 0.5, comps: 0.3, sotp: 0.2 }
scenarios:
  - { label: bull, value: 3400, prob: 0.25 }
  - { label: base, value: 2900, prob: 0.55 }
  - { label: bear, value: 2100, prob: 0.20 }
entry_points:
  - { value: 2450, currency: JPY, note: "below 2450 the base case clears 15% IRR" }
stop: { value: 2050, currency: JPY }          # optional
workbook: reports/Yakult_2267_TSE-20260610.xlsx
---
```

Deterministic validators (all fail the stage, none are "warnings"):

- `sum(scenario.prob) == 1.00 ± 0.01`; `sum(method_weights.values()) == 1.00 ± 0.01`.
- `bear.value ≤ target_price.value ≤ bull.value`.
- **Scenario-weighted value reconciles**: `Σ(prob × value)` equals the workbook's
  computed blended target within `reconcile.tolerance_pct` (doctrine: "the sum of
  (weight × value) must equal the stated total" — enforced by code, not eyeball).
- `upside_pct` recomputed from `target/pinned − 1`.
- `pinned_price` matches the market-data service for that trading date (the SPEC-RUNNER
  price-pin gate) — one price truth across report, monitor, and scoring.
- Every currency is ISO-4217 and equals the coverage currency unless explicitly labelled
  (the HKD/SGD/USD confusion class is a doctrine-named failure mode).

### 3.3 `tripwires.yaml` — the falsifiability mechanism

```yaml
version: 1
tripwires:
  - id: hk-reversion                 # stable slug; NEVER reused for a different condition
    kind: metric                     # metric | news | price | filing | event
    condition: "HK retail rental reversion below -10% in any half-year result"
    check: news+filing               # price | news | filing | news+filing | metric
    severity: thesis                 # thesis | valuation | info
    action: event_analysis           # event_analysis | notify_pm | digest
    created_by_run: 3f2c…
    created_at: 2026-06-10
    status: active                   # active | retired
    retired_by_run: null
  - id: entry-zone
    kind: price
    condition: "close < 2450.00 JPY"
    check: price
    severity: info
    action: notify_pm
    price: { op: below, value: 2450.00, currency: JPY }   # REQUIRED iff kind == price
    created_by_run: 3f2c…
    created_at: 2026-06-10
    status: active
```

Validators:

- `id` unique within the file, `[a-z0-9-]{3,32}`, **stable across runs** (a review that
  changes a tripwire's meaning must retire the old id and add a new one — this is what
  makes `events.dedupe_key` and tripwire hit-rates meaningful over time).
- `kind == price` ⇒ a `price` block with `op ∈ {below, above}`, a positive `value`, and a
  currency. Price tripwires are then **machine-evaluable in pure code** (SPEC-MONITORING);
  no model is asked whether a price crossed a number.
- `severity == thesis` ⇒ `action == event_analysis` (a thesis-breaking condition cannot
  be configured to merely whisper).
- At least **3** active tripwires, of which **≥1** is `severity: thesis` — a thesis with
  no falsifiable break condition is not a thesis (author role: "'Sentiment deteriorates'
  is not a tripwire"). The count is config (`fund.yaml dossier.min_tripwires`).
- Retiring: a review sets `status: retired`, `retired_by_run`; rows are never deleted
  (the monitor stops evaluating them; history stays readable).

### 3.4 `lessons.md`, `predictions.yaml`, `reports/`, `events/`, `queries/`

- **`lessons.md`** — append-only, newest last, one bullet per lesson, each dated and run-
  referenced: `- 2026-06-10 (run 3f2c, verifier@gemini): the FY25 segment table in the
  IR deck is restated; use the securities report.` A stage that *edits or deletes* an
  existing lesson line fails the gate (append-only is checked against the previous
  commit).
- **`predictions.yaml`** — a **read-only mirror**, generated by *code* from the ledger,
  never authored by a model (a mirror a model can write is a second source of truth and
  will drift). It is regenerated and committed by the system at PM acceptance and at the
  start of every subsequent run on that ticker (so a run always sees a current mirror);
  it is **not** rewritten by the scoring job (the DB is truth; git is not touched outside
  a run). Header line states this explicitly.
- **`reports/`** — the Yakult contract, named per doctrine `NAME_SYM_EXCH-YYYYMMDD`:
  `report-content.md`, `<NAME>.xlsx`, `build_workbook.py`, `references.md`,
  `corrections.md`, `reconciliation.yaml`, `pm-summary.md`, `<NAME>.html`, `<NAME>.pdf`.
- **`events/`** — `YYYYMMDD-<slug>.md`, written by `event_analysis` / `deep_review`.
- **`queries/`** — curated PM Q&A. A `pm_query` run is read-only and finished by the time
  the PM decides an exchange is worth keeping, so the file is written by **core, as a
  system commit** (like the `predictions.yaml` mirror): `POST /queries/{run_id}/keep`
  (SPEC-CORE §5.1; the adapter maps the PM's keep-reaction to it) formats the question,
  answer, date, and run reference **in code** and commits
  `queries/YYYYMMDD-<slug>.md` to dossier `main` (`query:<run_id> keep` message).
  The commit takes the coverage run lock; if a mutating run holds it, the keep is queued
  and applied when free (bookkeeping, not time-critical). No model ever writes this
  directory.

### 3.5 New-ticker scaffolding and re-initiation

A first initiation creates `dossiers/<slug>/` on the run branch. A **re-initiation** (a
re-proposed `exited`/`rejected` ticker, or a `failed` retry) reuses the same directory:
the old dossier is the author's context, and the author's `task.md` says so explicitly
("a prior dossier exists at `as_of <date>` from run X; treat it as context to verify, not
as truth to copy"). Nothing is deleted; git carries the history.

---

## 4. Stage contracts (what each stage must deliver)

The role prompts in `doctrine/roles/` are the instructions; this section is the **machine
contract** the gates enforce.

| Stage | Must produce | Gates applied (SPEC-RUNNER §6) |
|---|---|---|
| `author` | full report per doctrine (all 14 sections, skip only with a stated reason), workbook (one computed layer, formulas) + `build_workbook.py`, `references.md` with retrieval dates, draft `dossier.md` / `valuation.md` / `tripwires.yaml`, `stage_result.yaml` with predictions + `pinned_price` + `recommendation` + `confidence` | 1–8 (all) |
| `verifier` | corrected artifacts **in place**, `corrections.md` appended (was/now/source/attribution), `stage_result.corrections`, updated predictions **if** a correction changed a registered value/horizon/confidence | 1–8; plus the **last** verifier re-pins the price (gate 4 runs against a fresh market-data close) |
| `data_checker` *(optional)* | numbers-only corrections; no prose/judgment changes | 1, 2, 3, 4, 6 |
| `finalizer` | rendered HTML+PDF per the doctrine template, `reconciliation.yaml` (every headline figure: report value vs workbook cell vs match Y/N), completed dossier files, final predictions list, `pm-summary.md` (one screen) | 1–8; plus **consistency gates** (§4.1) |

### 4.1 Finalizer consistency gates (the anti-drift backstop)

The finalizer adds no analysis; it makes the artifacts agree. Code checks that they do:

1. `valuation.md.target_price` == the finalizer's `stage_result.predictions[kind=target_price]`
   (value, currency, horizon_date, confidence) — exactly.
2. `dossier.md.stance` == `stage_result.recommendation`.
3. **No dropped verifier correction**: if any verifier's `stage_result` revised a
   prediction value/horizon/confidence, the finalizer's final list must carry the revised
   value. Otherwise the stage fails with the omission named (`"verifier stage 3 revised
   TP to 2,750; final predictions still show 2,900"`). This is a pure numeric comparison
   across stage results — a late stage cannot quietly restore an earlier, corrected
   number.
4. `reconciliation.yaml` covers every headline figure named in
   `fund.yaml reconcile.required_figures` (rating, target, upside, market cap, EV,
   each multiple, each financial-summary line) — a reconciliation table that omits the
   inconvenient row does not pass.
5. Filenames follow `NAME_SYM_EXCH-YYYYMMDD` (doctrine), and the PDF renders (non-zero
   size, ≥1 page).

A finalizer that finds the **workbook itself** wrong must emit `status: blocked` with the
cell and reason (doctrine: "never patch around it"). `blocked` passes the gates, does not
retry, and surfaces to the PM as a blocked run — a loud, honest stop.

---

## 5. Prediction registration

**When:** at `finalizer` stage completion (its list is the authoritative one; the lead
signs the thesis). Verifier-emitted predictions are *not* separately registered — they
are corrections to the lead's numbers and are enforced into the final list by gate 4.1.3.

**How:** `predictions.register_from_stage(stage_id, entries)` (SPEC-DOMAIN §8):

- `house` = the **stage row's** house (the lead), never a value from model output.
- `status = 'open'` on registration.
- `idempotency_key = sha256(stage_id | kind | scenario_label | value | horizon_date)` —
  a resumed/retried finalizer re-registers nothing.
- `confidence` is written only if the model stated it; `NULL` otherwise (code never
  invents one, and the gate already failed the stage if the role requires it).
- Scenario rows (bull/base/bear) share a `scenario_group` uuid minted by code.
- A re-initiation of a ticker that has prior `open` predictions **supersedes** them
  (`superseded_by_id` = the new row, `superseded_run_id` = this run) — nothing is edited
  or deleted, and the old rows keep any score they already had.

**Scoring boundary:** predictions are registered `open` before the PM decides, so the
scoring job (SPEC-TRACKREC) **must skip predictions whose coverage is not `active` or
`watch`**. On `reject`, this run's predictions are marked `superseded` and are never
scored (a thesis the PM never adopted must not pollute a house's track record).

---

## 6. Corrections attribution

At every verify/data-check stage completion, each `stage_result.corrections[]` entry
becomes a `corrections` row (SPEC-DOMAIN §4.14):

- `correcting_house` = the stage row's house.
- `attributed_stage_id` = resolved from `attributed_stage` (a **seq within this run**);
  an unresolvable seq → `NULL` + warning (a model cannot attribute an error to a stage
  outside its own run, i.e. cannot smear another house's record).
- `attributed_house` = copied from the resolved stage row (not from model output).
- Idempotency: **amendment to SPEC-DOMAIN §4.14** — add
  `idempotency_key String(64) UNIQUE = sha256(stage_id | target | was | now)` so a retried
  stage does not double-count corrections against a house. Without it, one flaky verifier
  retry would inflate another house's error rate.

This yields the **corrections-received rate** per house — the pipeline measuring each
house's factual-error rate long before market outcomes arrive (SPEC-TRACKREC §metrics).

---

## 7. PM decision gate

On `finalizer` success the run goes `waiting_pm` with one open `initiation_decision`
gate. The coverage transitions `initiating → decision_pending` in the same transaction.

**Gate payload** (everything the PM needs, without opening a terminal):

```json
{
  "coverage": {"slug": "tse_2267", "name": "Yakult Honsha", "currency": "JPY"},
  "lead": "gpt",
  "verifiers": ["gemini", "deepseek"],
  "plan_notes": ["verify_count reduced to 1: only one contributor enabled"],
  "recommendation": "buy",
  "target_price": {"value": 2900, "currency": "JPY", "horizon_date": "2027-06-30",
                   "confidence": 0.65, "upside_pct": 5.6},
  "pinned_price": {"value": 2745, "as_of": "2026-06-09", "source": "market-data:tse"},
  "scenarios": [ … ],
  "proposed_levels": [{"kind": "entry", "value": 2450, "direction": "below"}, … ],
  "tripwires": {"count": 5, "thesis": 2},
  "corrections": {"total": 9, "by_attributed_house": {"gpt": 8, "gemini": 1}},
  "cost_usd": "18.40",
  "artifacts": {"pdf": "<artifact_id>", "workbook": "<artifact_id>",
                "report_md": "<artifact_id>", "corrections_log": "<artifact_id>"},
  "pm_summary_md": "…one screen…",
  "allowed_answers": ["active", "watch", "reject"]
}
```

**Answers and their transactional effects** (one transaction each; SPEC-DOMAIN §5 rows
6–8):

| Answer | Effects |
|---|---|
| `active` | gate answered → coverage `active` · **merge `run/<id>` into dossier `main`** (`dossier_commit_after`, `dossier_index` refreshed, `predictions.yaml` mirror regenerated and committed) · `coverage_levels` written from `proposed_levels` (PM may override in the answer body) · predictions stay `open` · run `succeeded` · coverage lock released · outbox `coverage.state_changed` (adapter **creates the ticker channel**) + `run.finished` (adapter posts PDF + summary there) · monitoring starts at the next scheduled session tick |
| `watch` | identical, with `state = watch` (lighter cadence: daily/weekly, news-only) |
| `reject` | gate answered → coverage `rejected` · branch **retained, not merged** · this run's predictions → `superseded` · **no channel** · run `succeeded` (the run did its job; the fund said no) · outbox `run.finished` with the PM's notes |

A `failed` or `cancelled` initiation instead transitions the coverage to `failed`, leaves
the branch unmerged, releases the lock, and alerts the desk with the last stage's
validation errors. `fund run retry <id>` (or `/propose` again) starts a fresh run from
the retained branch's base.

**The PM's answer is the only path** out of `decision_pending`. Nothing auto-accepts, and
no LLM can open, widen, or answer a gate.

---

## 8. Budget behavior at cap

`runs.budget_cap_usd` is resolved from `fund.yaml runs.initiation.budget_cap_usd` at run
creation (envelope: $10–40). Before each stage attempt, the orchestrator checks
`run.cost_usd + expected_stage_cost ≤ cap`; during a stage, the runner kills on
`kill_reason='budget'` (SPEC-RUNNER §5.3).

At cap the run **pauses `waiting_pm`** with a `budget_cap` gate:

```json
{"spent_usd": "38.10", "cap_usd": "40.00", "stages_done": ["author", "verifier", "verifier"],
 "stages_remaining": ["finalizer"], "estimate_to_finish_usd": "6.00",
 "allowed_answers": ["raise_cap", "cancel"]}
```

- `raise_cap` (with a new cap in the answer body) → audited, `runs.params.budget_cap_usd`
  updated, run re-queued at the **current stage boundary** — completed stages are not
  redone, and the partial artifacts on the branch are intact.
- `cancel` → run `cancelled`, coverage `initiating → failed`, branch retained.
- **It never silently degrades**: no dropping the finalizer, no swapping to a cheaper
  model, no shortening the report. Quality is the product; the PM decides whether to pay
  for it.

The coverage lock is **held** while the run sits at a budget gate (its branch is
unmerged), so no competing mutating run starts on that ticker meanwhile.

---

## 9. Sub-task: the remaining houses' harness drivers

The roster is GPT, Gemini, DeepSeek, GLM, Qwen (Claude is meta, never an analyst). Phase
2 shipped the first house's driver; multi-house initiation needs the rest. Each is a
sub-task against the **unchanged** SPEC-RUNNER §3 `HarnessDriver` interface — driver +
`houses.yaml` block + fixtures, **no core changes**:

| Sub-task | Driver | Notes |
|---|---|---|
| 3.1a | `gemini-cli` (Google) | non-interactive prompt file; usage from transcript |
| 3.1b | `generic-cli` for DeepSeek (opencode) | argv template + usage regex from config |
| 3.1c | `generic-cli` for GLM (opencode) | as above |
| 3.1d | `generic-cli` for Qwen (qwen-code) | as above |

**Acceptance per house** (all deterministic, no PM judgment needed):
1. `fund checkconfig --strict` passes with the house enabled (key present, models priced,
   driver known).
2. `fund harness check <house>` → binary, auth, egress-through-proxy, smoke call OK, and
   **no key material in any output**.
3. `fund harness smoke <house>` completes a throwaway `monitor`-role stage with parseable
   usage (`cost_source != 'estimated'` — a house whose costs can only be estimated is
   flagged for the PM, not silently accepted).
4. The house completes one real `verifier` stage on the pilot ticker, producing a
   schema-valid `stage_result.yaml` with at least one correction, and its corrections land
   attributed in the DB.

---

## 10. Test plan (TDD)

`tests/unit/initiation/`, `tests/integration/initiation/`. Unit tests use `FakeRunner`
(SPEC-CORE §9.1) / the fake harness binary (SPEC-RUNNER §10.1), a temp git dossier repo,
`FakeMarketData`, in-memory SQLite, and the autouse socket block. **The entire pipeline
is provable with zero LLM spend.**

### 10.1 Stage graph and rotation

- `verify_count=2` → author(lead) · verifier(c1) · verifier(c2) · finalizer(lead), all
  harness/heavy, sequential deps. `verify_count=1` → 3 stages. `verify_count=3` → 5.
- `include_data_checker=true` inserts a **light**-model data_checker before the finalizer.
- Verifiers are never the lead. With one contributor and `verify_count=2`, the plan
  degrades to 1 verifier **and records a `plan_note`** that surfaces in the gate payload.
- With zero contributors: run creation is refused **unless** `--allow-single-house`, in
  which case the lead verifies its own work and the gate payload says so (the 2.4 pilot
  path).
- Rotation: three initiations across two coverages pick different verifier pairs; the
  chosen houses are frozen into stage rows (a later `houses.yaml` edit does not change a
  completed run's attribution).

### 10.2 Dossier contract validators (one test per rule)

- `dossier.md`: missing required section → fail; empty section → fail; "not applicable"
  with a reason → pass; frontmatter `stance` ≠ `stage_result.recommendation` → fail.
- `valuation.md`: scenario probs summing to 0.95 → fail; `target` above `bull` → fail;
  `upside_pct` inconsistent with `target/pinned` → fail; scenario-weighted value ≠
  workbook blended target → fail (**the doctrine's weighting-reconciles rule, in code**);
  a currency that is not the coverage currency and is not labelled → fail.
- `tripwires.yaml`: duplicate id → fail; `kind: price` without a `price` block → fail;
  `severity: thesis` with `action: digest` → fail; 2 tripwires (below `min_tripwires`) →
  fail; zero thesis-severity tripwires → fail; retiring a tripwire keeps the row with
  `status: retired` and a `retired_by_run`.
- `lessons.md`: a stage that appends → pass; a stage that edits or deletes an existing
  line → fail (diffed against the previous commit).
- `predictions.yaml`: a stage that writes it → fail (**system-generated mirror only**);
  the mirror regenerated at acceptance matches the ledger exactly.

### 10.3 Pipeline behavior (FakeRunner / fake harness)

- **Happy path**: propose → initiate → 4 stages succeed → run `waiting_pm`, coverage
  `decision_pending`, exactly one open gate with the §7 payload (corrections counted by
  attributed house; artifact ids resolvable; `pm_summary_md` non-empty).
- **`decide: active`** → coverage `active`; branch merged into `main`;
  `dossier_index.main_commit` updated; `coverage_levels` written from `proposed_levels`
  (and a PM override in the answer body wins); predictions still `open`;
  `predictions.yaml` mirror committed; outbox contains a channel-creation event and a
  report-delivery event; the coverage lock is released.
- **`decide: watch`** → same, state `watch`.
- **`decide: reject`** → coverage `rejected`; branch present but **not** merged (assert
  `main` is unchanged); this run's predictions all `superseded`; no channel outbox event;
  the ledger shows the predictions were **never scored** (scoring job skips them — tested
  jointly with SPEC-TRACKREC).
- **Answer idempotency**: replaying the same answer returns the stored response and makes
  exactly one set of side effects; a *different* answer to an answered gate → 409.
- **Verifier correction flow**: verifier 1 emits 3 corrections attributed to stage 1 →
  3 `corrections` rows with `attributed_house = lead`; a retried verifier stage does
  **not** double-count them (idempotency key, §6); a correction attributed to seq 9
  (nonexistent) lands with `attributed_house = NULL` and a warning.
- **Dropped-correction gate**: verifier 2 revises TP to 2,750; the finalizer's
  `stage_result` still says 2,900 → finalizer **fails** with the named omission; on retry
  with 2,750 it passes and the ledger registers 2,750.
- **Price re-pin**: the last verifier's `pinned_price` is checked against a *fresh*
  `FakeMarketData` close; a stale pin (older than `max_price_age_days`) fails.
- **`blocked` finalizer**: `status: blocked` with a cell reference → stage passes gates,
  does **not** retry, run reaches `waiting_pm` with the blocked reason in the payload.
- **Budget cap**: fake costs push the run past the cap after verifier 2 → run
  `waiting_pm` with a `budget_cap` gate; `raise_cap` resumes at the **finalizer** (author
  and verifiers are not re-run; assert stage statuses unchanged and no new attempts on
  them); `cancel` → coverage `failed`, branch retained. The coverage lock is held
  throughout (a `deep_review` queued meanwhile stays `queued`).
- **Terminal stage failure**: verifier 2 exhausts `max_attempts` → run `failed`, coverage
  `failed`, desk outbox row carries the last `validation_errors`, branch retained; `fund
  run retry` starts a fresh run.
- **Re-initiation**: an `exited` coverage re-proposed → same slug and directory; the
  author's `task.md` contains the prior dossier's `as_of` and the "verify, don't copy"
  instruction; the prior run's `open` predictions are `superseded` by the new
  registration.
- **Concurrency**: two initiations for the same ticker cannot both run (coverage lock);
  the second stays `queued`.

### 10.4 Integration (`@pytest.mark.integration`, opt-in)

- One real multi-house initiation behind a manual flag (`--harness=gpt,gemini,deepseek`,
  spends money): assert the artifact set exists, the reconciliation gate passes on a real
  workbook, corrections land attributed across ≥2 houses, cost is captured non-estimated
  for every house, and the PDF renders. This is the **PM gate 3.3 rehearsal**.
- Postgres: gate answering is race-free (two concurrent `active` answers → one applies,
  one 409s; exactly one merge).

---

## 11. Module layout

```
core/orchestrator/graphs/initiation.py     # the plan (pure)
core/initiation/{registration,corrections,gate_payload,levels}.py
core/dossier/{contracts,mirror,scaffold}.py    # schemas + predictions.yaml generator
runner/gates/dossier.py                        # the validators in §3
runner/drivers/{gemini_cli,generic_cli}.py     # §9 sub-tasks
config/fund.yaml.example                        # dossier.min_tripwires, reconcile.*, runs.initiation.*
```

---

## 12. Preliminary — pending PM gates

This spec is written **before** PM gate 2.4 (pilot initiation) and 3.3 (multi-house
pilot). These assumptions are the ones those gates can invalidate; each is isolated so a
change is cheap:

1. **The artifact set and gate list are right.** 2.4 grades a real report against the
   Yakult standard. If the PM finds the gates let bad work through (or block good work),
   the fix is **doctrine/role-prompt work first, gate config second, code last** — the
   gate list (§4) is config-driven (`fund.yaml roles.<role>.required_artifacts`,
   `reconcile.required_figures`) precisely so a gate can be added without a code change.
2. **Sequential verify passes.** If 3.3 shows verifiers mostly correcting *different*
   things (little dependency), `initiation.parallel_verify: true` halves wall-clock. Do
   not enable it before the evidence; the last verifier's fresh re-pin depends on running
   last.
3. **Registration at the finalizer only.** If the PM wants each verifier's revised numbers
   in the ledger as first-class superseding rows (a richer per-stage trail), that is an
   additive change (register at each stage + supersede), and §5's boundary rule is what
   would move.
4. **Rotation by least-recently-used.** If 3.3 shows a house is systematically better at
   verifying a sector, rotation may need a competence weighting — a planner change only.
5. **`verify_count` default 2.** The pilot may show 1 is enough for watch-tier names or
   that 3 is needed for complex ones; it is already per-run config.
6. **Single-house pilot mode** (`--allow-single-house`) exists only because 2.4 may run
   with one authenticated house. It must never become the default: the whole thesis of
   the pipeline is that a *different* house verifies.
7. **pgvector** (deferred to phase 4) does not touch this spec — the initiation pipeline
   uses no embeddings.

---

## 13. Spec Authoring Checklist

- **Side-effect cost.** An initiation is the most expensive thing the fund does:
  `1 + verify_count + (data_checker?) + 1` harness stages, envelope **$10–40 per run**
  (`budget_cap_usd`, enforced per-run and per-house-per-day), at ~1–2 initiations/week.
  Per stage: one sandboxed CLI session (many provider calls inside it — bounded by the
  budget kill, not by a call count), **one** market-data call per report-producing stage
  for the price-pin gate, and a local `soffice` recalc (CPU only). `verify_count` is the
  single biggest cost lever: 1 → ~⅔ the cost, 3 → ~1⅓. Deliberate **cost avoidances**:
  predictions arrive structured, so there is **no LLM extraction call** (v2 spent one per
  report); the `predictions.yaml` mirror is generated by code, not a model; corrections
  attribution is code; the gates are code. Retries re-spend a stage (that is why
  `max_attempts` is 2–3 and why the retry prompt names the omissions — a cheap retry is
  one that fixes exactly what failed). DB writes: ~40–120 rows/run. No embedding, news,
  or search spend originates here.
- **Concurrency model.** No new in-process shared state. Per-ticker serialization is the
  `coverage_run_locks` row (SPEC-DOMAIN §4.10), **held from run start through
  `waiting_pm`** — so a second initiation, a deep review, or an event analysis on the same
  ticker waits in `queued` instead of racing the unmerged branch; monitor ticks (read-only)
  proceed. Each run owns its own worktree/branch (`run/<id>`), and the **runner is the
  only git writer**, so concurrent runs on different tickers cannot collide. Gate
  answering is guarded by the gate's unique `idempotency_key` (answer-once) plus a
  row-level lock, so two PM clients (chat and CLI) cannot both merge the branch. The
  stage rotation "state" is *derived* from prior stage rows, not stored as a mutable
  cursor, so two concurrent planners cannot corrupt it.
- **LLM-as-filter threat model.** Model output here reaches the PM as a *recommendation*
  and reaches the ledger as *registered predictions*, and the content it reasons over
  (web, filings, news) is untrusted. Deterministic backstops: the **stage gates**
  (schema, artifacts, reconciliation, price-pin, placeholders, references, dossier
  contract) are code — a stage cannot argue its way past them; the **price-pin gate asks
  the market-data service**, so an injected or hallucinated price fails; **scenario
  weights and the blended target must reconcile to the workbook**, so a model cannot
  state a target its own arithmetic does not support; **confidence and recommendation are
  never synthesized** — missing ⇒ the stage fails, named (v2's fabricated "Neutral/50%"
  footer is impossible by construction); **the finalizer cannot drop a verifier's
  correction** (§4.1.3 is a numeric diff across stage results, not a judgment);
  **attribution fields are code-owned**, so a model cannot smear another house or forge
  its own identity; and **no LLM can open, widen, or answer the PM gate** — the allowed
  answers are a closed set and the actor must be in the PM allowlist. Everything an agent
  can reach is deny-by-default (SPEC-RUNNER §5), so prompt-injected instructions to
  exfiltrate or fetch elsewhere fail at the proxy and alert. The exempt path, stated
  honestly: **judgment** — an agent can still write a well-formed, well-cited, *wrong*
  thesis. The defenses against that are human and procedural (verify passes by different
  houses, the corrections log, the PM gate, the scored ledger), not code, and that is the
  design's deliberate choice.
- **Identity-key ownership.** This spec mints: **tripwire ids** (`[a-z0-9-]`, unique
  within `tripwires.yaml`, *stable across runs*, retired-not-reused — they are the key
  SPEC-MONITORING's `events.dedupe_key` and the tripwire hit-rate metric depend on, so
  changing a tripwire's meaning under a stable id would silently corrupt both);
  `predictions.idempotency_key` (over the **stage id**, ours, not model text);
  `corrections.idempotency_key` (**amendment to SPEC-DOMAIN §4.14**, over the stage id +
  the correction's own fields — without it a retried verifier double-charges another
  house's error rate); the `scenario_group` uuid (code-minted, grouping bull/base/bear).
  Borrowed keys, read never re-derived: `coverage.dossier_slug` (owned by
  `core/domain/dossier_slug.py`; the dossier directory, worktree, and channel name all
  come from that column) and stage seq→id resolution for `attributed_stage` (the
  orchestrator owns stage rows). `predictions.yaml` deliberately holds **no** key of its
  own: it is a mirror, so it cannot become a competing identity for a prediction.
- **Test isolation.** Every path in this spec is provable without an external service:
  `FakeRunner`/fake harness binary stands in for the houses; the dossier repo is a temp
  git repo (local); market data is `FakeMarketData` (which the price-pin gate calls);
  Postgres is in-memory SQLite; artifacts go to a tmp `ARTIFACTS_DIR`; the outbox is
  asserted on directly instead of running an adapter. No provider SDK, news source, or
  search endpoint is reachable from any unit test, and the autouse socket guard in
  `tests/unit/conftest.py` catches the miss this spec most invites — a validator (e.g. the
  references gate) reaching out to check a URL, which is exactly why URL liveness is
  deliberately **not** a gate (SPEC-RUNNER §6.1 rule 7) and is an opt-in integration
  check instead. Real houses, real workbooks (`soffice` recalc), and the real multi-house
  pilot are `@pytest.mark.integration` and require an explicit flag because they spend
  money.
