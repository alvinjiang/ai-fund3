# ai-fund3

AI Fund v3 — the redesign of the Mattermost-native v2 system. Long-only proprietary
"AI fund": the human PM (Alvin) decides the portfolio; AI analyst agents do the
research (initiation, monitoring, re-reasoning) with auditable workings. The system
advises; it does not trade.

Three structural fixes over v2: (1) a real initiation pipeline (author → verify →
correct across model houses), (2) one accountable lead house per equity with tracked
predictions, (3) a per-ticker git-versioned Dossier as the mandatory memory and output
of every run.

## Architecture (design/01, design/05)

Two-tier execution. **Heavy research** (initiation, deep review, event analysis) runs
in sandboxed per-provider agent harnesses — full search, files, spreadsheets, the
substrate that produced the PM's gold-standard reports. **Routine monitoring** runs on
cheap models through a slim in-process API tool-loop; price/threshold checks are pure
code. A single **core service** (FastAPI + APScheduler, Postgres) orchestrates both as
durable, typed **runs** with persisted prompts, transcripts, artifacts, and cost.

Three processes: core (orchestrator + API + scheduler + scoring), runner (the only
component that executes model work — substrate=api or substrate=harness), and a thin
Mattermost adapter (ws in, REST out; one bot per house + a desk bot; channel per
ticker, lifecycle driven by coverage state). Everything the chat can do, the `fund`
CLI can do — chat is an adapter over the core API, never the source of truth.

No Redis: the queue is a `run_stages` table claimed with `SELECT ... FOR UPDATE SKIP
LOCKED`, transactional with domain state. Dossiers live in their own git repo
(`dossiers/`, one dir per ticker); runs get a `git worktree` per stage. Deterministic
money paths (prices, risk, trades/cash, cost) are ported from v2 and never produced by
LLMs. Operator config and secrets live outside the deploy tree (`/etc/ai-fund/`) so a
`git reset` can never clobber them. Claude is structurally supported but reserved as
the meta role (doctrine distillation, track-record review) — it is not an analyst.

## Start here

1. Read `design/00-DECISIONS-LOG.md` then `design/01-VISION-AND-PRINCIPLES.md`.
2. Work through `PROMPTS.md` top-to-bottom (phase-ordered execution file).
3. `AGENTS.md` is the agent contract (Absolute Rules + conventions) — read before coding.

## Layout

- `design/` — the 8 approved redesign docs (the design record).
- `doctrine/` — research doctrine: `roles/` (8 stage role prompts), and (after Phase 0.2)
  `core.md`, `sections/`, `lessons/`, `report/`, `templates/`.
- `proprietary/` — doctrine source + gold-standard report zips (gitignored; never commit).
- `specs/` — self-contained specs produced by SPEC steps.
- `core/` `runner/` `adapter/` `cli/` — the v3 code (core service, harness runner,
  Mattermost adapter, CLI).
- `dossiers/` — separate git repo; one dir per ticker, git-versioned memory of every run.
- `tests/` — unit tests (network-blocked by default) + integration tests (opt-in).
- `PROMPTS.md` — the single execution file (phases 0–7).

v2 source (for porting) lives at `/home/alvin/aicode/ai-fund` and keeps running until
the Phase 7 cutover.
