# AI Fund v3 Redesign — Decisions Log

Working log of decisions made with the PM (Alvin) during the redesign brainstorming
session on 2026-07-07. This file is the durable record; if the session ends, a future
agent should read this first.

## Confirmed decisions (from PM, 2026-07-07)

1. **Approach**: Design-first hybrid. Define target architecture cleanly, then map each
   existing subsystem to keep / port / rewrite. Not a big-bang rewrite, not an in-place
   refactor.
2. **Interface**: Keep Mattermost, but decoupled — core must be interface-agnostic and
   testable without Mattermost; Mattermost becomes a thin adapter.
3. **Coverage/accountability model**: **Lead + contributors** per equity.
   - Start cheap: PM suggests ONE model as initial lead at initiation (no expensive
     multi-model bake-off by default).
   - Contributor models join at initiation and on material events; they may propose a
     lead change amongst themselves; PM can always manually reassign the lead based on
     track record.
   - PM can trigger ad-hoc contributor reviews at any time (for better coverage or to
     review the lead's performance).
4. **V1 scope (all four)**: initiation pipeline; coverage lifecycle; monitoring &
   updates; prediction tracking & audit.
5. **Model roster**: OpenAI (GPT), Google (Gemini), DeepSeek / GLM / Qwen / others as
   analysts for now. **Claude is supported in the structure but NOT assigned as an
   analyst** — PM reserves Claude as "meta" (system design and review).
6. **Agent identity = model house** (e.g. "GPT", "Gemini", "DeepSeek" bots). Track
   records accrue to models. Personas/specialties (japan_specialist etc.) become prompt
   context per stock, not identities.
7. **Scale**: ~20–25 **active** tickers (portfolio + waiting-for-entry over ~3-month
   horizon) plus ~20–25 **"interesting"/watch** tickers (lighter coverage, no near-term
   entry). Two coverage tiers are explicit.
8. **Deterministic subsystems** (risk engine, trades/cash, cost tracker, data sources):
   re-evaluate each during detailed planning; port what fits.
9. **Legacy inputs**: PM provided `Equity-Research-Prompts_2026-06-05.md` (repo root)
   plus two gold-standard report zips (Yakult, LINK REIT) — these define the target
   quality bar for the initiation pipeline. Only PDF outputs are what the PM reads;
   workbooks ride along.

## Key findings from repo inventory (2026-07-07)

- Current system: Mattermost-native; 2 dispatcher processes → Redis Streams → 1 main
  process; APScheduler; Postgres+pgvector; 23 tables; ~21K app LOC + 12K test LOC;
  systemd deploy on single host; staging phase, no production run.
- Watchlist IS Mattermost channel membership (invite bot to `us_aapl` channel → DB row).
  This is the finicky add/assign flow.
- **No initiation pipeline exists** — reports are one-pass, single-agent
  (`reports/generator.py`); `SectionSpec.assigned_agent` is vestigial. No
  draft→verify→correct loop anywhere.
- **Prediction tracking**: fully spec'd in `docs/SPEC_PREDICTION_TRACKING.md` (38KB,
  "ready for implementation"), zero code exists. Schema design there is reusable.
- Multi-model per stock exists only via the immature debate engine (BUG-004/008,
  SPEC_DEBATE_CONTROL is an unimplemented seed spec).
- Identity tangle: bot persona = fixed model = channel membership, all coupled in
  `config/agent_config.py` + `channel_watchlist`.
- Deterministic subsystems (risk/engine.py VaR+sizing, trades/cash.py, cost/tracker.py,
  data_sources/) are solid, tested, LLM-free.
- Recurring pain themes: config/deploy coupling, slash-command silent failures,
  bot/channel membership breaking scheduled posts, encoding bugs, fabricated LLM output
  (invented confidence footers), secret leaks in logs.
- The uploaded Equity-Research-Prompts file contains the real doctrine: 14 section
  prompts + hard process rules (single pinned price, workbook one-source-of-truth,
  report↔workbook reconciliation, no placeholders ever, verification/corrections log,
  cite-or-mark-unverified, FX discipline, division of labour incl. "use smaller models
  for data retrieval"). Its "Further notes from past mistakes" section is a manual
  learning loop the new system should formalize.

## Approvals (PM, 2026-07-07, later same session)

10. **Architecture**: Two-tier execution chosen. Heavy research (initiation, deep
    reviews, event analysis) runs in sandboxed per-provider agent-harness sessions;
    routine monitoring uses a cheap API tool-loop; a core service orchestrates both as
    durable runs.
11. **Design approved in full** (three sections presented and approved):
    domain model (coverage/dossier/runs/track-record, tripwires, git-versioned
    dossiers), pipeline & roster (author→verify×2→finalize, tripwire monitoring,
    PM-approved lead changes, model-house identities, Claude-meta distillation), and
    architecture & migration (core+runner+adapter, Postgres-only, Mattermost as thin
    adapter, migration map).

## Report-sample findings (Yakult/LINK zips, summarized by subagent)

- Yakult 2267 (2026-06-10) is the gold standard: report-content.md, 13-sheet workbook
  + build_workbook.py, 15 dated references, formal Verification & Corrections Log
  (9 corrections), reconciliation discipline. LINK REIT is professional but lacks the
  audit trail — confirming that the verification passes are what create the quality.
- The initiation pipeline's artifact contract is modeled on the Yakult set.

## Document set (this directory)

- 00-DECISIONS-LOG.md — this file
- 01-VISION-AND-PRINCIPLES.md — intent, quality bar, 12 principles, scale, roster
- 02-DOMAIN-MODEL.md — coverage, dossier, runs, predictions, retired v2 concepts
- 03-RESEARCH-PIPELINE.md — stage contract, 7 run types, doctrine layout, cost envelopes
- 04-AGENTS-AND-MODELS.md — houses, lead/contributor mechanics, track record, learning loop
- 05-SYSTEM-ARCHITECTURE.md — processes, runner/sandboxes, interfaces, config, failure modes
- 06-MIGRATION-MAP.md — per-module keep/mine/port/retire verdicts, data migration, cutover
- 07-IMPLEMENTATION-ROADMAP.md — phases 0–7 with copy-paste prompts and PM gates

## Post-review additions (same session)

- A consistency review (subagent) found 9 issues (undefined promote transition,
  tier/state overlap, unscheduled data_checker role, distillation substrate drift,
  two table omissions, run-status re-entry edge, a typo, a naming note) — all fixed
  in place.
- The eight stage **role prompts** were drafted by Fable directly (they are the
  highest-judgment text in the system) into `redesign/doctrine-seed/roles/`;
  roadmap step 0.3 downgraded from SPEC (Opus) to CHORE (placement).

## Open items (for future sessions)

- PM to confirm fresh repo `ai-fund3` vs subtree (roadmap 0.1; fresh repo recommended).
- First harness house choice (roadmap 2.1: whichever of Codex CLI / Gemini CLI
  authenticates fastest on the host).
- `Equity-Research-Prompts_2026-06-05.md` and the two report zips remain **untracked**
  in the v2 repo on purpose (proprietary doctrine stays out of git — v2 decision
  carried forward); doctrine seeding (roadmap 0.2) reads them from disk.
- pgvector keep/drop decision deferred to Phase 4.
