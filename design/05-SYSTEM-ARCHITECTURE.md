# AI Fund v3 — System Architecture

Status: Approved by PM 2026-07-07.

## 1. Process model

Three long-running processes plus sandboxed, ephemeral stage executions. Single-host
(same class of deployment as v2: systemd on Ubuntu, Postgres, no Redis).

```
                 ┌────────────────────────────────────────────┐
   PM ── CLI ────▶                CORE SERVICE                │
   PM ── chat ─┐ │  FastAPI (core API) · coverage state       │
               │ │  machines · run orchestrator · scheduler   │
┌──────────────▼┐│  (APScheduler) · scoring · cost caps       │
│  MATTERMOST   ││  Postgres (single source of truth)         │
│  ADAPTER      │└───────────────┬────────────────────────────┘
│  ws in, REST  │                │ claims queued stages (PG queue,
│  out; bots =  │                │ SELECT ... FOR UPDATE SKIP LOCKED)
│  houses+desk  │ ┌──────────────▼────────────────────────────┐
└───────────────┘ │                RUNNER                     │
                  │  substrate=api: slim tool-loop (provider  │
                  │    SDKs, ~market/news/dossier tools)      │
                  │  substrate=harness: spawn sandboxed CLI   │
                  │    session per stage; capture transcript, │
                  │    artifacts, cost; enforce budgets/time  │
                  └──────┬────────────────────────────────────┘
                         │ workspace = git checkout of dossiers/<ticker> @ run branch
                  ┌──────▼────────────────────────────────────┐
                  │  SANDBOX (container/bwrap per stage)      │
                  │  harness CLI (codex/gemini/qwen/opencode) │
                  │  net: provider API + search + market-data │
                  │  mounts: workspace rw, doctrine ro        │
                  └───────────────────────────────────────────┘
```

**Why Postgres-only queue:** at ~50 tickers, tens of runs/day, and stage durations of
minutes-to-hours, a `run_stages` table with `SKIP LOCKED` claiming is sufficient,
transactional with domain state, and removes the entire Redis/consumer-group/
dead-letter operational class that dominated v2's bug log. If volume ever demands it,
the queue interface is swappable (documented seam).

## 2. The runner and harness integration

The runner is the only component that executes model work. Per stage:

1. Prepare workspace: `git worktree` of the ticker dossier at branch `run/<id>`;
   write `task.md`; mount doctrine read-only.
2. **Harness substrate**: launch the house's CLI non-interactively (e.g.
   `codex exec`, `gemini -p`, `qwen`, `opencode run`) inside a sandbox (container or
   bubblewrap) with: only that house's API key injected; network policy allowing the
   provider endpoint, the market-data service, and the search endpoint; wall-clock
   and budget limits. Capture the full session transcript to `transcript_ref`.
3. **API substrate**: run the slim in-process tool loop (ported/rewritten from v2's
   bounded JSON loop, now with a small stable tool set: market data, news, dossier
   read, workspace write).
4. Validate outputs (stage gates, 03-RESEARCH-PIPELINE.md §1): schema-check
   `stage_result.yaml`, required artifacts, reconciliation script, no-fabrication
   checks. On failure: bounded retries with the specific validation error appended to
   the prompt; then run `failed` with PM notification.
5. Commit workspace changes (`run:<id> stage:<seq> <role>@<house>`), record cost
   (provider usage APIs where available; token-parsed transcripts otherwise; both
   reconciled nightly against `llm_usage`).

Harness heterogeneity is contained in a small `HarnessDriver` interface
(launch/monitor/collect); each house's driver is ~a page of code and config, and new
houses are added by writing a driver + registry entry.

**Market data as a service:** the ported v2 `data_sources/` router is exposed inside
the sandbox as a local HTTP/MCP endpoint. Agents and monitors pull prices from the
same pinned source — one price truth for reports, monitors, and scoring (doctrine's
price-pinning rule becomes infrastructure).

## 3. Interfaces

**Core API (FastAPI, localhost + authenticated):** everything is an API call —
propose/decide/promote/demote/exit coverage, trigger runs, answer PM gates, query
dossiers/track records/costs, trade confirmation. The CLI (`fund …`) and the
Mattermost adapter are both thin clients. Nothing state-changing happens outside the
API (fixes v2's silent slash-command failures: errors return to the caller).

**Mattermost adapter:** one process. Bots = one per house (posting identity for that
house's work) + `desk` (system: digests, gates, alerts). Channel per ticker, created
and archived by coverage transitions. Slash commands and mentions → core API; posts ←
core events. All adapter logic is translation only; it holds no state beyond the ws
connection. The v2 poster/auth/channel-naming code is ported into it.

**Deliverables to PM:** initiation/review PDFs + summaries posted to the ticker
channel and stored under the dossier; decision gates presented as messages with
explicit `/decide <ticker> active|watch|reject` (buttons where Mattermost supports).

## 4. Scheduling

APScheduler inside core (ported pattern from v2), all jobs emitting runs/ticks through
the same queue: per-exchange session ticks (active tier), daily/weekly watch-tier
ticks, earnings-calendar deep reviews, quarterly review sweep, prediction scoring,
monthly distillation, cost rollups, retention cleanup. Every job execution is recorded
(ported `scheduler_job_logs`); a missed-schedule watchdog alerts the desk channel
(v2 lesson: silent scheduler death).

## 5. Deterministic subsystems (ported, per re-evaluation)

| Subsystem | Disposition (detail in 06-MIGRATION-MAP.md) |
|---|---|
| risk engine (VaR, sizing, stress) | port; runs on portfolio snapshots; advises entries/weights; alerts to desk |
| trades/cash (ledger, confirmation) | port; manual confirmation flow via adapter; LLMs never book |
| cost tracker | port + extend to runs/stages and harness sessions |
| data sources (router, jquants, EDGAR, FX, freshness) | port; exposed as market-data service |
| news fetchers + dedup | port; scoring re-targeted to tripwires |
| infra (retry, rate budget, logging/redaction, shutdown) | port |
| checkconfig / llm_ping | port, adapted to house registry |

## 6. Configuration and secrets

- Operator config lives **outside the deploy tree** (e.g. `/etc/ai-fund/`):
  `houses.yaml`, `fund.yaml` (tiers, thresholds, schedules, budgets), `pricing.yaml`,
  `.env` (secrets, 0600). Deploys can never clobber it (v2 lesson, already partially
  fixed there).
- No model names, prices, or budgets in code — registry + pricing config only
  (v2's `SPEC_NO_MODEL_DEFAULTS` carried forward).
- `checkconfig --strict` gates service start.

## 7. Security and audit

- Per-house key isolation in sandboxes; secret redaction in all logs (ported, plus
  deny-by-default egress in sandboxes so a confused agent cannot exfiltrate keys).
- PM-only authorization on state-changing API routes (ported allowlist model).
- Audit log on every coverage transition, PM decision, lead change, doctrine merge.
- Full provenance chain: recommendation → run → stages → prompts + transcripts +
  doctrine version + artifacts + cost. "Why do we hold this?" is answerable from
  the dossier and its git history alone.

## 8. Storage

- **Postgres**: domain tables (02-DOMAIN-MODEL.md), queue, ported deterministic
  tables, `llm_usage`. pgvector retained only for news dedup/search (re-evaluate in
  Phase 4; drop if tripwire scoring makes it redundant).
- **Dossier git repo** (`dossiers/`): one repo, dir per ticker; server-side bare repo
  with worktrees per run; nightly backup (bundle + offsite copy with DB backups).
- **Artifacts** (PDFs, xlsx, transcripts): content-addressed files under
  `ARTIFACTS_DIR` (ported hashing/storage), referenced from stages.

## 9. Failure modes (design answers)

| Failure | Answer |
|---|---|
| Harness session hangs / runs away | wall-clock + budget kill; stage retry; run fails visibly to desk |
| Model returns prose, no structured result | stage gate fails → bounded retry with named omission → visible failure (never silent repair) |
| Provider outage | retry policy per class; run pauses `queued`; stale-dossier answers flagged in pm_query |
| Cost blowout | per-run and per-house daily caps; run pauses `waiting_pm` at cap |
| Dossier merge conflict (concurrent runs) | per-ticker run serialization (one mutating run per ticker at a time; monitor ticks read-only) |
| Host loss | Postgres + dossier bundle + artifacts in nightly offsite backup; systemd restart-on-boot |
