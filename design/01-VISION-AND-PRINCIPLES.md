# AI Fund v3 — Vision and Principles

Status: Approved by PM 2026-07-07 (see 00-DECISIONS-LOG.md)

## What this is

A long-only, proprietary "AI fund" in which the human PM (Alvin) decides the portfolio
and AI analyst agents do the research: initiating coverage with institutional-grade
reports, monitoring covered equities, re-reasoning on new information, and maintaining
auditable recommendations with full workings. The system does not trade; it advises,
and it tracks the PM's actual positions to advise in context.

v3 is a redesign of the current Mattermost-native v2 system. The v2 codebase basically
works but has three structural failures this redesign fixes:

1. **No initiation pipeline.** The multi-pass workflow that produced the PM's
   gold-standard reports (author → verify → correct across different models) was never
   built. v3 makes it the front door of coverage.
2. **Nobody owns a stock.** Multiple persona-bots with fixed models diffuse
   accountability. v3 assigns one *lead* model house per equity with tracked
   predictions; other models contribute through defined roles.
3. **Knowledge doesn't accumulate.** Agents don't build on prior research. v3 makes a
   per-ticker, git-versioned **Dossier** the mandatory context and output of every
   piece of agent work.

## Quality bar

The Yakult 2267 report (2026-06-10, provided by PM) is the reference standard for
initiation research:

- Structured markdown report covering the full doctrine section set.
- Workbook with a single computed layer (formulas, not typed numbers), built
  programmatically (`build_workbook.py`), with report↔workbook reconciliation.
- References with URLs and retrieval dates; every figure traceable or marked
  "unverified" — never a plausible substitute.
- A verification & corrections log showing what later passes fixed.
- Explicit recommendation, target price, horizon, scenarios, and confidence.

## Principles

1. **The PM decides; agents advise.** Coverage entry/exit, trades, and lead changes are
   PM decisions. Agents produce reasoned, auditable recommendations.
2. **One accountable lead per equity.** Contributors and verifiers participate through
   the pipeline, but the lead signs the thesis, and its track record is on the line.
3. **Correct, don't critique.** Verification passes produce the best corrected output,
   not commentary on the previous agent. (Proven to work; avoids performative review.)
4. **Everything is a Run.** All agent work executes as durable, typed pipeline runs
   with persisted prompts, transcripts, artifacts, and cost. No untracked LLM output
   reaches the PM.
5. **The Dossier is the memory.** Every run reads the dossier and must leave it
   updated. If it isn't in the dossier, the fund doesn't know it.
6. **Deterministic money path.** Prices, thresholds, risk metrics, position/cash
   accounting, and trade confirmation are code, never LLM output. LLMs interpret and
   recommend; they do not compute NAV or book trades.
7. **Falsifiable theses.** Every thesis ships with tripwires — concrete, monitorable
   conditions that would damage it. Monitoring watches tripwires, not vibes.
8. **Predictions are scored.** Targets, entries, and scenarios are registered,
   immutable, and scored against outcomes. Track records are visible to the PM and to
   the agents themselves.
9. **Doctrine learns.** Research standards live in versioned doctrine files. Lessons
   from corrections and misses are distilled (Claude meta role) into proposed doctrine
   amendments that the PM approves. Claude is not an analyst; it reviews the system.
10. **Match substrate to work.** Heavy research runs in full agent harnesses (search,
    files, spreadsheets — what produced the quality bar). Routine monitoring runs on
    cheap models through a slim API loop. Cost tiers are designed in, not bolted on.
11. **Interface-agnostic core.** Everything Mattermost can do, the CLI can do. Chat is
    an adapter over a core API, never the source of truth.
12. **Config outlives deploys.** Operator config and secrets live outside the deploy
    tree; a `git reset` can never clobber them. (Hard-won v2 lesson.)

## Scale and cost envelope

- ~20–25 **active** equities (portfolio + waiting-for-entry, ~3-month horizon) and
  ~20–25 **watch** equities (interesting, no near-term entry).
- Heavy runs (initiation, deep review, event analysis) are per-event, budget-capped per
  run and per model house per day.
- Monitoring is scheduled and mostly deterministic (price/threshold checks are code;
  only news-vs-tripwire relevance uses a cheap model).

## Model roster

Analyst houses: **GPT (OpenAI), Gemini (Google), DeepSeek, GLM, Qwen** (extensible).
**Claude is structurally supported but not assignable as an analyst** — it is reserved
for the meta role: doctrine distillation, track-record review, and system design/review
sessions like the one that produced these documents.
