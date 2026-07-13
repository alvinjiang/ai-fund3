# SPEC-TRACKREC — Scoring, calibration, deep_review, lead_review

**Phase:** 6.1 (SPEC) · **Implements:** PROMPTS.md 6.1 · **Branch:** `spec-trackrec`
**Status:** ready for BUILD (**preliminary pending PM gates 2.4/3.3**, §10)
**Depends on:** SPEC-DOMAIN (predictions, corrections, events), SPEC-CORE (scheduler,
gates), SPEC-RUNNER (stages), SPEC-INITIATION (registration rules), SPEC-MONITORING
(events, ratings)
**Consumed by:** SPEC-DISTILLATION (track-record injection into the memo), SPEC-ADAPTER
(`/track-record`)

Predictions are scored so that a house's record is a **measured fact**, not a vibe. Two
signals matter and they arrive on very different timescales: **corrections-received rate**
(the pipeline measuring a house's factual-error rate *this week*) and **prediction
outcomes** (the market measuring its judgment *next year*). Both are visible to the PM and
to the agents themselves — the point is calibration pressure, not shame.

Self-contained: implementable without reading `design/`.

---

## 1. Scope

- The **scoring job**: price-history-based hit/miss/expiry, per prediction kind, with
  supersede handling and corporate-action correctness.
- **Per-house metrics**: hit rate, target error, **calibration curve**,
  **corrections-received rate**, escalation quality, with sample-size guards.
- **`/track-record` surfaces** (API, CLI, chat).
- **`deep_review` scheduling**: earnings calendar + quarterly sweep (batched,
  budget-aware).
- The **`lead_review` run**: contributor ballots via `stage_result`, aggregation, the PM
  approval gate, handover bookkeeping.
- **Track-record injection** into `lead_review` and distillation contexts.
- **PM 👍/👎 capture** on event notes.

Out of scope: the risk engine and portfolio math (phase 7.2 port).

---

## 2. Scoring job (pure code; zero LLM cost)

`core/trackrec/scoring.py`, run by the daily `prediction_scoring` job (SPEC-CORE §4).
Its only external dependency is the **market-data service** — the same pinned price truth
the reports and monitors use.

### 2.1 What is scored, and what is skipped

Scored: `predictions` with `status='open'` whose **coverage is `active` or `watch`**.

Skipped (and why):
- `superseded` — a revised prediction is not re-scored; any score it already had is kept
  (history is preserved, per SPEC-DOMAIN §4.12).
- Coverage in `decision_pending` — registered at finalization but not yet adopted by the
  PM. If the PM rejects, they become `superseded` and are **never scored**: a thesis the
  fund never adopted must not pollute a house's record.
- Coverage `rejected`/`failed`.
- Coverage `exited` — handled once, at exit (§2.5).

### 2.2 Direction and the price series

At registration, direction is derived **by code** from the stage's `pinned_price` and the
prediction value: `up` if `value > pinned × (1 + flat_band)`, `down` if
`value < pinned × (1 − flat_band)`, else `flat` (`flat_band` = `fund.yaml scoring.flat_band`,
default 0.005). It is stored on the row (**amendment to SPEC-DOMAIN §4.12**: add
`direction String(8)` and `pinned_price Price` to `predictions`, both code-written at
registration) so no later scan needs to re-derive it — and so a later price move cannot
retroactively change what the prediction *meant*.

**Corporate actions.** The market-data service returns daily closes plus a cumulative
**adjustment factor** per date (splits, consolidations). Scoring compares a *split-adjusted
close* against a *split-adjusted target* (the registered value divided by the cumulative
factor since registration). A 1:3 split must not turn a hit into a miss. Dividends are
**not** adjusted for hit detection (a price target is a price), but total return (price +
dividends) is used for `stance` scoring and for `realized_return`. If the service reports
a halt/delisting with no price for the horizon window, the prediction is `expired` with
`outcome_notes` naming the reason — never a fabricated realized price.

### 2.3 Hit detection (daily scan)

For each open prediction inside `[created_at, horizon_date]`, evaluate the closes since
the last scan and write a `prediction_hits` row (unique per `(prediction_id, hit_on)`):

| Kind | Hit condition |
|---|---|
| `target_price`, `direction=up` | `adj_close >= adj_target` |
| `target_price`, `direction=down` | `adj_close <= adj_target` |
| `target_price`, `direction=flat` | no hits considered (scored at horizon on error only) |
| `entry_point` | `adj_low <= adj_entry` (an entry is reached intraday if the day's low touches it; if the provider gives no low, `adj_close <= adj_entry`) |
| `scenario` | no per-day hits; scored as a group at horizon (§2.4) |
| `stance` | no per-day hits; scored at horizon on realized total return |
| `event_forecast` | not price-resolvable → **PM resolves** (`fund predictions resolve <id> hit\|miss --notes`), else `expired` at horizon |

Hits are evidence, not status: a prediction stays `open` until its horizon (a target that
prints once and falls back still counts as a hit — the fund could have sold there).

### 2.4 Scoring at horizon

| Kind | `status` | `realized_price` | `error_pct` |
|---|---|---|---|
| `target_price` | `hit` if ≥1 hit row, else `miss` | best directional hit (max close for `up`, min for `down`), else close at horizon | signed `(realized − target)/target` (realized = close at horizon, **always**, so error is comparable across hits and misses) |
| `entry_point` | `hit` if the entry was reachable in-window, else `miss` | best (lowest) low, else close at horizon | as above |
| `scenario` (group) | the scenario nearest the realized close → `hit`; siblings → `miss` | close at horizon | per-row |
| `stance` | `hit` if realized total return clears the band for that stance (`fund.yaml scoring.stance_bands`: e.g. buy ≥ +5%, hold within ±10%, sell ≤ −5%), else `miss` | close at horizon | realized total return |

`realized_return` = total return (price + dividends) from `pinned_price` to horizon.
**Calibration** uses the `confidence` the model stated (NULL confidence → excluded from
calibration, never imputed).

Scenario groups additionally get a **Brier score** on the probability assigned to the
realized scenario: `brier = Σ(p_i − o_i)²` over the group (`o_i` = 1 for the realized
scenario). Stored on the group's rows (`outcome_notes.brier`), aggregated per house.

### 2.5 Exit and supersession

- Coverage `exited` → all open predictions are scored **once** at the exit price
  (`status='expired'`, realized values recorded, `outcome_notes: "coverage exited"`).
  Expired predictions count in *error* metrics but not in *hit-rate* denominators (the
  fund pulled the plug; the market never answered the question) — this asymmetry is
  documented in the metric definitions and shown in the UI.
- A new run superseding an open prediction sets the old row `superseded` (never scored
  again). **Open predictions keep their original `house` across a lead change** — the
  house that made the call owns it forever.

### 2.6 Idempotency and concurrency

The job claims a batch with `SELECT … FOR UPDATE SKIP LOCKED`, skips rows with
`scored_at IS NOT NULL`, and writes hits with a unique `(prediction_id, hit_on)`. Two
concurrent runs cannot double-score or double-count a hit. Re-running the job for a past
date is safe.

---

## 3. Metrics (`core/trackrec/metrics.py`)

Computed by query (indexed), with a nightly snapshot for trends
(**amendment to SPEC-DOMAIN**: add `house_metric_snapshots(house, as_of, scope, metrics JSONB)`
— `scope` = `all` | `coverage:<slug>`).

| Metric | Definition | Source |
|---|---|---|
| **Hit rate** | hits ÷ (hits + misses), by kind and horizon bucket (≤3m, 3–12m, >12m) | `predictions` |
| **Target MAE / MAPE** | mean absolute `error_pct` over scored `target_price` rows | `predictions` |
| **Bias** | mean *signed* `error_pct` (is the house systematically optimistic?) | `predictions` |
| **Calibration curve** | confidence bucketed into deciles → realized hit rate per bucket; plus **ECE** = Σ nᵢ/N · \|confᵢ − hitᵢ\| | `predictions` |
| **Brier (scenarios)** | mean group Brier score | `predictions` |
| **Corrections-received rate** | corrections attributed to house H ÷ **authoring stages** by H (`role ∈ {author, finalizer}`), with a 90-day trend (rising/falling) | `corrections` × `run_stages` |
| **Corrections-made rate** | corrections *filed* by H ÷ verify stages by H | `corrections` |
| **Escalation quality** | 👍 ÷ (👍 + 👎) on `event_analysis` notes authored by H | `events.pm_rating` |
| **Coverage-weighted score** | config-weighted composite of the normalized metrics above (`fund.yaml trackrec.weights`) — **advisory only**, never an automatic action | derived |

Guards that keep the numbers honest:

- **Sample-size minimums** (`fund.yaml trackrec.min_n`, default 5 per metric): below it, the
  metric renders `insufficient data (n=3)` and **cannot** trigger lead-review candidacy.
  Three misses out of four predictions is noise, and treating it as signal would make the
  system replace leads on luck.
- **Corrections-received rate** is per *authoring stage*, not per correction count alone, so
  a house that authors more is not penalized for volume — and the `corrections.idempotency_key`
  (SPEC-INITIATION §6) prevents a retried verifier stage from double-charging it.
- **Corrections-made rate is shown but never rewarded**: a verifier paid by correction count
  would pad. It exists so the PM can see a verifier that files nothing (asleep) or files
  trivia (noise); the doctrine already forbids churn.
- Expired-at-exit predictions are excluded from hit-rate denominators (§2.5).
- Every metric carries its `n` and its window in the payload. A metric without an `n` is a
  bug.

---

## 4. `/track-record` surfaces

```
GET /track-record?house=&coverage=&since=&scope=all|coverage
```

Returns per house: the metric table above (each with `n`), the calibration curve (buckets),
recent scored predictions, and the corrections trend. Filtered by coverage when asked
("how has GPT done *on this name*?").

- **CLI**: `fund track-record [--house gpt] [--coverage tse_2267]` → a table + a
  text calibration curve (`conf 0.6–0.7: 58% hit (n=12)`).
- **Chat**: `/track-record [house|ticker]` → the same, compact, posted by `desk`.
- **Agents see it too** — it is injected into `lead_review` and distillation contexts (§7),
  which is the calibration-pressure mechanism.

---

## 5. `deep_review` scheduling

Two schedulers, both idempotent, both budget-aware. Neither ever runs an LLM to decide
*whether* to schedule.

### 5.1 Earnings-driven (`earnings_sweep`, daily)

- The market-data service supplies the earnings calendar per ticker (ported
  `data_sources/`). For each `active` coverage whose results were **released** since the
  last sweep → create a `deep_review` with
  `trigger_ref = earnings:{slug}:{fiscal_period}` (unique → a re-fire creates nothing).
- `watch`-tier coverage gets an earnings-driven review only if
  `fund.yaml cadence.watch_review_on_earnings: true` (default false — the watch tier is
  news-only by design; a promotion is what buys it a review).

### 5.2 Quarterly sweep (`quarterly_sweep`)

- `active` coverage with no completed `deep_review`/`initiation` in
  `fund.yaml trackrec.review_interval_days` (default 90) → `deep_review`,
  `trigger_ref = quarterly:{slug}:{yyyy}Q{n}`.
- **Batched and budget-aware**: at most `trackrec.max_reviews_per_day` created per day,
  ordered by staleness (oldest dossier first) then by position size (names the fund
  actually owns first). Runs that cannot start because a house is at its daily cap simply
  stay `queued` (SPEC-CORE §3.5) — the sweep does not thrash.

### 5.3 The run (design/03 §2.2)

| seq | Role | House | Substrate |
|---|---|---|---|
| 1 | `author` (update) | lead | harness |
| 2 | `verifier` | rotating contributor | harness |

The lead refreshes the dossier against new filings/results: updated valuation, revised
predictions (**superseding**, never editing), refreshed tripwires, an event note in
`events/`. The verifier corrects. All dossier contracts and stage gates apply unchanged
(SPEC-INITIATION §3, SPEC-RUNNER §6).

**Material-change notification** (not a gate): if `|ΔTP| > escalation.tp_change_pct` or the
stance changed, the run's outbox payload carries **"what changed since the last review"**,
computed from the dossier **git diff** (valuation frontmatter before/after + the
tripwire delta) — code, not a model summary — and the desk gets a line. The PM does not
have to approve a review; they have to *see* it.

---

## 6. `lead_review` run

### 6.1 Trigger

- PM: `fund lead-review start <slug>` / `/lead-review <ticker>`.
- **Candidacy flag** (weekly job, `lead_review_candidacy`): a lead breaching
  `fund.yaml lead_review.candidacy` (default: 3 consecutive scored misses on that name, **or**
  corrections-received rate ≥ 2× the roster median, **both** subject to `min_n`) → a **desk
  notice** naming the evidence. **It never auto-spawns the run** (heavy work needs a human
  "go"), and it never auto-changes a lead.

### 6.2 Graph and inputs

One `lead_review` stage **per contributor** (parallel; api substrate by default, harness if
configured). Each contributor's `task.md` is built by code and contains:

1. the ticker's dossier (digest for api substrate);
2. **the lead's track record on this name** — its scored predictions with outcomes, target
   error, calibration, corrections-received rate and trend (rendered by
   `core/trackrec/render.py`, with `n` on every number);
3. **peer houses' aggregate records** (same renderer, scope=all);
4. recent run history on the name (runs, costs, failures, disagreements).

The role prompt (`doctrine/roles/lead_review.md`) governs the judgment; the numbers are
supplied by code so that no house can misquote another's record.

### 6.3 Ballots and aggregation

Each stage files `stage_result.lead_change_proposal`:

```yaml
lead_change_proposal:
  proposed_house: gemini | keep
  rationale: "cites specific predictions/corrections/events"
```

Aggregation (`core/trackrec/lead_review.py`, pure code):
- tally `keep` vs `change`, and per proposed house;
- **flag self-proposals** (a contributor proposing itself) explicitly in the payload — the
  role prompt allows them only on comparative evidence, and the PM sees the flag;
- surface **every rationale verbatim** (no synthesis — the PM reads the arguments, not a
  model's summary of them);
- produce a one-line recommendation string (`"3 of 4 contributors propose change → gemini
  (1 self-proposal)"`).

### 6.4 PM gate and handover

The run ends `waiting_pm` with a `lead_change` gate (`allowed_answers: ["approve", "keep"]`;
the answer body carries the house when several were proposed). **v1 has no auto-switching**
— the PM approves every change, always.

On `approve` (one transaction):
1. `coverage.lead_house` = new house; `coverage_lead_history` row (from, to, run, approved_by,
   rationale).
2. **Handover bookkeeping in the dossier** (the run still holds the coverage lock, so its
   branch is safe to write): the run's final system commit updates `dossier.md` frontmatter
   (`lead_house`, `updated_by_run`) and appends a dated line under a **`## Handovers`**
   section (**additive amendment to SPEC-INITIATION §3.1**: an optional, append-only
   `## Handovers` H2). Then the branch merges to `main`.
3. **Open predictions keep their original `house`** — the old lead still owns its calls, and
   its record is still scored against them (this is what makes accountability real).
4. Outbox `coverage.lead_changed` → the adapter posts the handover note and updates the
   channel header.

On `keep`: the ballots and rationales are recorded (they are evidence for the next review),
the branch merges (the dossier note records that a review happened), run `succeeded`.

---

## 7. Track-record injection (and where it must *not* go)

Rendered by `core/trackrec/render.py` into:

- **`lead_review` stages** (§6.2) — the whole point of the run.
- **`distillation`** (SPEC-DISTILLATION) — per-house calibration and corrections trends for
  the system-health memo.

It is deliberately **not** injected into `author`, `verifier`, `finalizer`, or
`event_analysis` prompts: an author who knows it is "behind on hit rate" has an incentive to
make bolder calls to catch up, and a verifier who knows the author's record has an incentive
to find corrections. Both would corrupt the measurement. Agents see their records through the
PM and through reviews — not while doing the work being measured. (This is a design choice
worth revisiting only with evidence.)

---

## 8. PM 👍/👎 on event notes

**Amendment to SPEC-DOMAIN §4.16**: `events` gains `pm_rating SmallInteger | None` (+1/−1),
`pm_rated_at`, `pm_rated_by`.

- `POST /events/{id}/rate {rating: up|down}` (PM only, idempotent — a re-rate overwrites and
  is audited).
- The adapter maps a 👍/👎 **reaction** on an event-note post to that route (the reaction is
  the cheapest possible PM interface, which is why the rating is one bit).
- Feeds the **escalation-quality** metric (§3) and the distillation memo. A house whose event
  analyses the PM consistently rates 👎 is producing noise, which is exactly the kind of thing
  no market outcome will ever tell you.

---

## 9. Test plan (TDD)

`tests/unit/trackrec/`. `FakeMarketData` with **scripted price series** (including a split, a
dividend, a halt), `FakeClock`, SQLite, `FakeRunner` for the review runs. No LLM is required
to test scoring at all — it is pure code.

### 9.1 Scoring (table-driven)

- `target_price up`: close reaches target on day 40 of a 90-day horizon → `hit`,
  `realized_price` = best hit, `error_pct` computed from the **horizon** close (so hits and
  misses are comparable).
- `target_price up`, never reached → `miss`, realized = horizon close, signed `error_pct` < 0.
- `target_price down`, reached → `hit`.
- `flat` prediction → no hits, scored on error only.
- `entry_point`: intraday low touches the entry → `hit` (even though no close does); with no
  low data, the close rule is used.
- `scenario` group: realized close nearest `base` → `base` row `hit`, `bull`/`bear` `miss`;
  the group's Brier score matches a hand-computed value.
- `stance`: realized total return +7% with `buy` band ≥+5% → `hit`; a `hold` with a +14% move
  → `miss` (outside the ±10% band); bands come from config, **not** from code constants.
- **Split**: a 1:3 split mid-horizon → the adjusted comparison still records the hit (a naive
  implementation would report a miss; this test is the regression guard).
- **Dividend**: `realized_return` includes it; hit detection does not.
- **Halt/delisting**: no price at horizon → `expired` with a reason, **no fabricated
  realized price**.
- **Coverage exit** mid-horizon → open predictions `expired`, scored at the exit price, once
  (a second job run changes nothing).
- **Superseded** rows are never scored; a superseded row that was already scored keeps its
  score.
- **`decision_pending` / `rejected`**: predictions of a rejected initiation are `superseded`
  and are **never** scored (joint test with SPEC-INITIATION).
- **Idempotency**: running the job twice on the same day writes one hit row and scores each
  prediction once; two concurrent jobs (integration, Postgres) cannot double-score.
- Zero LLM calls in the entire scoring path (assert `FakeLLM.calls == 0`).

### 9.2 Metrics

- Hit rate, MAE, and bias match hand-computed values on a fixture ledger.
- **Calibration**: a house with 10 predictions at `confidence=0.9` of which 5 hit → the 0.9
  bucket shows 50% and the ECE reflects the 0.4 gap.
- `confidence = NULL` rows are **excluded** from calibration (never imputed to 0.5) but still
  count in hit rate.
- **Corrections-received rate**: 8 corrections attributed to GPT across 4 authoring stages →
  2.0/stage; a **retried** verifier stage does not double-count (idempotency key); a
  correction with `attributed_house = NULL` counts against **nobody**.
- **Sample-size guard**: a house with n=3 shows `insufficient data` and **cannot** be flagged
  for lead-review candidacy.
- Expired-at-exit predictions are excluded from the hit-rate denominator but included in error
  metrics.
- Snapshots: the nightly job writes one `house_metric_snapshots` row per house per day; a
  trend query returns a rising/falling corrections rate.

### 9.3 Scheduling

- `earnings_sweep` creates one `deep_review` per released result; a second sweep the same day
  creates none (`trigger_ref` unique).
- `quarterly_sweep` respects `max_reviews_per_day` and orders by staleness then position size;
  a house at its daily cap leaves the run `queued`, not failed.
- `watch`-tier coverage gets no earnings review by default.
- The material-change notification fires on a 12% TP move and on a stance flip, and its
  "what changed" block is computed from the **git diff**, not from model prose (assert the
  payload matches a diff of the fixture dossier).

### 9.4 `lead_review`

- Planner: one stage per enabled contributor; the lead does **not** vote; a meta house is
  never a contributor.
- Aggregation: 3 `change → gemini`, 1 `keep` → the recommendation string is exact, every
  rationale is present verbatim, and a **self-proposal is flagged**.
- The gate opens with `allowed_answers: ["approve", "keep"]`; nothing changes until the PM
  answers (assert `coverage.lead_house` is unchanged while `waiting_pm`).
- `approve` → lead changed, `coverage_lead_history` row written, dossier `## Handovers` line
  appended and frontmatter updated, branch merged, outbox event emitted — and **open
  predictions still carry the old house** (the accountability regression guard).
- `keep` → ballots recorded, no lead change, run succeeds.
- Candidacy job: a lead with 3 consecutive misses (n above `min_n`) → **desk notice only**,
  and **no run is created** (assert zero `lead_review` runs).
- Track-record injection: the contributor's `task.md` contains the rendered table with `n` on
  every metric; an `author` stage's `task.md` contains **no** track-record block (§7).

### 9.5 Ratings

- `POST /events/{id}/rate up` → `pm_rating=+1`, audited; a re-rate overwrites; a non-PM user
  → 403. The escalation-quality metric moves accordingly. An adapter 👍 reaction maps to the
  same route (tested with `FakeMattermost`).

### 9.6 Integration (`@pytest.mark.integration`)

Postgres: concurrent scoring jobs (SKIP LOCKED) score each prediction exactly once;
`prediction_hits` unique-per-day holds under concurrency.

---

## 10. Preliminary — pending PM gates

1. **Scoring bands and horizons** (`stance_bands`, `flat_band`, horizon buckets) are config
   guesses until there are real scored outcomes; the first cohort of predictions will not
   mature for months, so expect to tune them **once, with data**, and to re-score history
   deliberately (the ledger is immutable, but a re-score writes new outcome values on the same
   rows — an audited operation, `fund predictions rescore --since`).
2. **Candidacy thresholds** (3 misses, 2× median corrections) are guesses. The corrections
   signal arrives *months* before outcome data, so it will drive the first lead reviews;
   watch for it punishing a house that draws harder names (the role prompt already says "a
   struggling lead on a hard name may still be the right lead").
3. **No track-record in authoring prompts** (§7) is a judgment call about incentives, not a
   proven fact. If the PM wants agents to self-correct with their own record in view, it is a
   prompt-assembly change — but measure the effect on boldness and on correction counts.
4. **`event_forecast` needs manual resolution** in v1. If those pile up, either drop the kind
   or give it a resolvable definition.
5. Gate 3.3 (multi-house pilot) grades **corrections-log quality**. If verifiers are found to
   file trivia, the corrections-received metric — the earliest signal in the system — is
   polluted at the source, and the fix is doctrine (verifier prompt), not scoring code.

---

## 11. Spec Authoring Checklist

- **Side-effect cost.** The scoring path makes **zero** LLM calls — it is pure code over the
  ledger plus the market-data service (batched: one history call per ticker per day,
  ~50 calls/day, a service we run; no per-prediction call). Metrics are DB queries plus one
  nightly snapshot row per house. The **cost-bearing** parts are the runs this spec
  *schedules*: `deep_review` ($5–20 each) at ~25 names × quarterly ≈ 2/week, plus
  earnings-driven reviews (~1 per name per quarter) — bounded by `max_reviews_per_day`, by
  per-house daily caps, and by staleness-ordered batching so a sweep cannot dump 25 heavy runs
  into the queue at once. `lead_review` is medium ($15 cap) and is **never auto-spawned** (the
  candidacy job posts a notice; a human starts the run) — a deliberate refusal to let a
  metric threshold spend money. Track-record rendering into prompts adds tokens (a table, not
  a corpus) to `lead_review`/`distillation` only.
- **Concurrency model.** No in-process shared state. The scoring job claims prediction batches
  with `SELECT … FOR UPDATE SKIP LOCKED` and is idempotent by `scored_at IS NOT NULL` plus the
  `(prediction_id, hit_on)` unique index, so two schedulers, a manual re-run, or a restart
  cannot double-score or double-count a hit. Schedulers are single-leader (advisory lock,
  SPEC-CORE §4.4) and their runs are deduped by `trigger_ref`. The `lead_review` gate is
  answer-once (`pm_gates.idempotency_key`), and the handover writes (coverage lead, history
  row, dossier commit, outbox) happen in **one transaction** while the run still holds the
  coverage lock — so a lead cannot change while a mutating run on that ticker is mid-flight,
  and two PM clients cannot both approve. Metrics are read-only queries; the nightly snapshot
  is an upsert keyed `(house, as_of, scope)`.
- **LLM-as-filter threat model.** Scoring is **entirely deterministic** — no LLM decides a hit,
  a miss, or a metric, and none can: the inputs are the registered row (code-written `house`,
  `direction`, `pinned_price`) and the price series from our own service. That matters because
  scoring is what makes a track record *cost* something: if a model could influence its own
  score, every other incentive in the system would rot. The two places model output *enters*
  are bounded: (1) **ballots** in `lead_review` — a contributor could argue for itself, which
  is why self-proposals are **flagged by code**, why the numbers in its task are **rendered by
  code** (it cannot misquote a peer's record), and why **the PM approves every change** (no
  auto-switching); (2) **rationales**, which are surfaced verbatim rather than synthesized, so
  no model summarizes another's argument away. Prompt injection reaches this spec only through
  a dossier a compromised run wrote; it cannot alter a score (prices come from the service) and
  it cannot change a lead (the gate is PM-only). Fail-closed: a prediction whose price data is
  missing is `expired` with a reason, never scored on a guess.
- **Identity-key ownership.** This spec **borrows** keys and owns their invariants: the
  prediction row (SPEC-DOMAIN/SPEC-INITIATION mint `idempotency_key` over the *stage id*; this
  spec never re-keys it), `corrections.idempotency_key` (SPEC-INITIATION §6 — the invariant
  "one correction, one row, even across retries" is what makes the corrections-received rate a
  real number, and it is enforced by that unique index, not here), and `runs.trigger_ref`
  (scheduler-owned: `earnings:{slug}:{fiscal_period}`, `quarterly:{slug}:{yyyy}Q{n}` — minted
  **here**, unique-indexed, so a re-fire creates nothing). Hit rows are keyed
  `(prediction_id, hit_on)` — a *trading date*, which is the market-data service's key, and
  the invariant (one close per ticker per trading date, and its adjustment factor) is that
  service's to enforce; scoring reads it and never re-derives a "date" from a timestamp.
  `house_metric_snapshots` is keyed `(house, as_of, scope)`. The one key this spec deliberately
  does **not** create: a "reviewer id" — ballots are attributed by the *stage row's* house, so
  a model cannot vote as someone else.
- **Test isolation.** The entire scoring and metrics path is unit-testable with **no external
  service**: `FakeMarketData` supplies scripted price series (splits, dividends, halts — the
  cases that break naive implementations), SQLite holds the ledger, `FakeClock` drives horizons,
  and there are **no LLM calls to mock** (a test asserts `FakeLLM.calls == 0` across the scoring
  suite). The review *runs* are exercised through `FakeRunner` with canned ballots. The autouse
  socket guard catches this layer's most likely miss — a metrics or scheduling query reaching
  for a live earnings calendar or price feed instead of the injected market-data client.
  Postgres-only guarantees (SKIP LOCKED batch claiming, the unique-per-day hit index under
  concurrency) are `@pytest.mark.integration` and skip without a test-DB URL.
