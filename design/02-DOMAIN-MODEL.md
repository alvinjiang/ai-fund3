# AI Fund v3 — Domain Model

Status: Approved by PM 2026-07-07. Detail level: design; exact DDL is produced in the
implementation phases (see 07-IMPLEMENTATION-ROADMAP.md, Phase 1).

Four first-class concepts: **Coverage**, **Dossier**, **Run**, **TrackRecord**.
Supporting concepts: Event, House, Doctrine, and the ported deterministic entities
(positions, trades, cash, risk, cost).

## 1. Coverage

One record per equity the fund pays attention to.

### State machine

```
proposed ──▶ initiating ──▶ decision_pending ──▶ active
                │                   │              │  ▲
                ▼                   ├──▶ watch ◀───┘  │ (promote)
             failed                 │       │─────────┘
                                    └──▶ rejected
active ──▶ exited        (position closed & PM retires coverage)
active ──▶ watch         (demote: still interesting, no near-term entry)
watch  ──▶ exited        (drop entirely)
```

- `proposed`: PM has proposed the ticker (`/propose` or CLI). Nothing has run yet.
- `initiating`: initiation run in progress.
- `decision_pending`: final initiation report delivered; waiting on PM decision.
- `active`: full monitoring + reviews. Portfolio names and near-term-entry names.
- `watch`: light monitoring (lower frequency, news-only). "Interesting" tier.
- `watch → active` (**promote**): PM decision (entry point approaching, or bought in).
  Promotion auto-spawns a `deep_review` run to refresh the dossier, valuation, and
  tripwires; monitoring cadence switches immediately, without waiting for the review.
- `exited` / `rejected`: retained with full history; can be re-proposed later, and the
  new initiation run receives the old dossier as context.
- `failed`: initiation run failed terminally; PM can retry.

All transitions are PM actions or run completions; every transition is audit-logged
with actor and cause.

**"Tier" is not a separate field** — `active` and `watch` are states, and docs that
speak of the "active tier" / "watch tier" mean coverage in that state (the term
refers to the monitoring cadence the state implies). A coverage in `exited`/
`rejected` has no tier and no monitoring.

### Fields (illustrative)

```
coverage:
  id, ticker, exchange, name, currency, isin?
  state: (above; active|watch double as the monitoring tier)
  lead_house: fk → houses          # exactly one
  contributors: [house, ...]       # default: all enabled houses except lead
  levels: { entry: [..], target: .., stop?: .., review_price?: .. }  # PM+pipeline set
  earnings_calendar_ref, exchange_session_ref
  pm_notes
  created_at, decided_at, exited_at
```

The Mattermost ticker channel is **derived state**: created/archived by the adapter as
a consequence of coverage transitions. Channel membership never drives coverage
(reversal of v2, where inviting a bot to a channel WAS the watchlist).

## 2. Dossier

Per-ticker canonical knowledge base. Lives as a directory per ticker inside a single
git repository (`dossiers/`), versioned by commits that reference the run that made
them (`run:<id>` in commit messages). The DB stores an index (current commit, paths);
git is the audit trail.

```
dossiers/<exchange>_<ticker>/          # e.g. tse_2267/
  dossier.md         # thesis: business model, moat, bull/bear, key drivers, stance
                     # YAML frontmatter: stance, conviction, updated_by_run, as_of
  valuation.md       # current TP + method weights, scenarios (bull/base/bear with
                     # probabilities), entry point(s), horizon; links to workbook
  tripwires.yaml     # falsifiable monitor conditions (see below)
  lessons.md         # stock-specific lessons (appended by verify passes & reviews)
  predictions.yaml   # read-only mirror of registered predictions (DB is truth)
  events/            # YYYYMMDD-<slug>.md event analyses
  reports/           # initiation & review artifacts: report-content.md, .xlsx,
                     # build_workbook.py, rendered .html/.pdf, corrections log
  queries/           # notable PM Q&A worth keeping (curated by runs)
```

**Contract:** every run receives its ticker's dossier checkout as the workspace; every
run that concludes must either commit a dossier update or state explicitly that nothing
changed. The runner enforces this (a run whose stages produced no commit and no
explicit no-change declaration fails validation).

### Tripwires

The falsifiability mechanism. Written by the initiation pipeline, maintained by
reviews. Each tripwire is machine-checkable by the monitor tier:

```yaml
- id: hk-reversion
  kind: metric            # metric | news | price | filing | event
  condition: "HK retail rental reversion below -10% in any half-year result"
  check: news+filing      # what the monitor scans
  severity: thesis        # thesis | valuation | info
  action: event_analysis  # what to spawn when tripped
- id: entry-zone
  kind: price
  condition: "close < 35.00 HKD"
  check: price
  severity: info
  action: notify_pm
```

Price/threshold tripwires are evaluated in pure code. News/filing tripwires are
evaluated by a cheap model scoring fetched items *against the tripwire text* (not
generic relevance). Severity `thesis` auto-escalates to an `event_analysis` run.

## 3. Run

Every piece of agent work, durable and typed.

```
runs:
  id, coverage_id (nullable for meta runs), type, status, trigger, priority
  created_at, started_at, finished_at
  cost_usd (rollup), summary, pm_decision?         # for runs that end in a PM gate
  dossier_commit_before, dossier_commit_after

run_stages:
  id, run_id, seq, role, house, model, substrate: harness | api
  status: queued | running | succeeded | failed | skipped
  workspace_ref, transcript_ref, artifacts_ref
  cost_usd, tokens_in/out, wall_time
  result: structured stage_result.yaml (see 03-RESEARCH-PIPELINE.md)
```

Run types (stage graphs defined in 03-RESEARCH-PIPELINE.md):

| Type | Trigger | Substrate | Cost tier |
|---|---|---|---|
| `initiation` | PM proposes ticker | harness ×3–4 stages | heavy |
| `deep_review` | earnings date, quarterly timer, PM | harness ×2 | heavy |
| `event_analysis` | tripwire/monitor escalation, PM | harness ×1 (+cross-check) | medium |
| `monitor_tick` | schedule (per exchange session) | code + one cheap API call | minimal |
| `pm_query` | PM mention/DM/CLI question | api (lead house) | light |
| `lead_review` | PM, or track-record trigger | harness/api contributors | medium |
| `distillation` | monthly timer or PM (Claude meta) | harness ×1 | medium |

Statuses: `queued → running → waiting_pm → succeeded | failed | cancelled`, plus the
re-entry edge `running → queued` (transient failure, e.g. provider outage: the run
re-queues at the current stage boundary; only terminal failures reach `failed`).
`waiting_pm` covers PM gates (initiation decision, lead-change approval, doctrine
amendment approval). Runs are resumable at stage boundaries; a failed stage can be
retried without redoing prior stages.

## 4. TrackRecord (predictions ledger)

Reuses the schema design of v2's `docs/SPEC_PREDICTION_TRACKING.md` (never
implemented), adapted to houses and runs.

```
predictions:
  id, coverage_id, run_id, house
  kind: target_price | entry_point | scenario | event_forecast | stance
  value, currency, horizon_date, confidence (0-1), scenario_probs?
  rationale_ref (dossier path / report anchor)
  created_at, status: open | hit | miss | expired | superseded
  scored_at, outcome { realized_price, realized_return, error, notes }
```

Rules:

- Predictions are **immutable**; revisions create a new row and mark the old one
  `superseded` (with the superseding run recorded). Nothing is ever edited or deleted.
- A scheduled scoring job (pure code) marks hits/misses/expiries from price history at
  horizon or on exit.
- **Corrections attribution:** verification passes emit corrections logs; each
  correction is attributed to the stage/house that authored the corrected content.
  This yields a measured in-pipeline factual-error rate per house, alongside
  market-outcome metrics.

Derived per-house metrics (views): prediction hit rate by kind/horizon, target MAE vs
realized, confidence calibration, corrections-received rate, coverage-weighted score.
Shown to the PM (`/track-record`, CLI) and injected into `lead_review` runs.

## 5. Supporting entities

- **House**: model-house registry entry (see 04-AGENTS-AND-MODELS.md): provider,
  heavy/light models, harness config, budgets, `assignable`, `meta` flags.
- **Event**: normalized observations (news item, price threshold, filing, earnings
  release, PM instruction) with materiality and links from detecting run to handling
  run.
- **Doctrine**: versioned research standards in `doctrine/` (git). DB records approved
  versions; runs record which doctrine version they used.
- **Ported deterministic entities** (from v2, re-evaluated per 06-MIGRATION-MAP.md):
  `positions`, `trades`, `pending_trade_confirmations`, `book_cash`, `cash_events`,
  `risk_configs`, `risk_events`, `portfolio_snapshots`, `llm_usage` (extended with
  run/stage refs), `audit_log`, `scheduler_job_logs`, `news_articles`.

## 6. What is deliberately gone (from v2)

- `channel_watchlist` / watch modes — coverage replaces it; channels are derived.
- Persona identities (`japan_specialist`, …) and `agents` table — houses + per-stock
  dossier context replace personas.
- `debates` — replaced by structured disagreement records inside runs (a contributor
  cross-check that materially disagrees produces a disagreement entry the PM sees;
  no theatrical debate transcripts).
- `analyses` / `conversation_turns` as the primary research store — the dossier and
  runs/stages replace them (chat history persists in Mattermost itself; notable Q&A is
  curated into the dossier).
- Redis streams, consumer groups, dead-letter queues — Postgres-backed run queue.
