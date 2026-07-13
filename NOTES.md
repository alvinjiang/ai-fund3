# NOTES

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
