# AI Fund v3 — Migration Map (v2 → v3)

Status: Approved direction 2026-07-07; per-module verdicts below follow the PM's
"re-evaluate each" instruction. Verdicts: **port** (move with interface cleanups),
**mine** (salvage parts), **retire** (drop; concept replaced), **new** (v3-only).

Recommendation (PM to confirm at kickoff): build v3 in a **fresh repository**
(`ai-fund3`), porting modules in with their tests, while v2 staging keeps running
until cutover (Phase 7). A fresh repo escapes the chat-event-centric coupling and the
30-spec doc sprawl; the one-branch-per-spec workflow carries over.

## Per-module verdicts

| v2 module | LOC | Verdict | Notes |
|---|---|---|---|
| `agents/analyst_agent.py` | 995 | retire | replaced by runner substrates; mine the bounded-tool-loop + prompt-assembly patterns for the api substrate |
| `agents/registry.py`, `analysis_store.py`, `conversation_store.py` | ~700 | retire | houses registry + runs/dossiers replace them |
| `agents/watch_agent.py` | | retire | monitor_tick (mostly deterministic) replaces |
| `dispatchers/` (2 procs) | ~140 | retire | adapter owns the ws connection; no Redis hop |
| `mattermost/router.py` | 876 | mine | routing rules/skip logic inform the adapter; most complexity (thread ownership, watch modes) is retired |
| `mattermost/poster.py`, `bots.py`, `auth.py`, `channel_naming.py` | ~900 | port | into the adapter; channel naming survives (channels now derived from coverage) |
| `mattermost/redis_streams.py`, `slash_processors.py`, `slash_commands.py` | ~1100 | retire | PG queue + core API replace; slash handlers become thin API calls |
| `debate/` | ~700 | retire | structured disagreement in runs replaces debates |
| `scheduler/market_scheduler.py` | 341 | port | pattern + exchange sessions; jobs now emit runs |
| `scheduler/execution.py` | 775 | mine | threshold-gating and digest logic → monitor_tick; most job bodies retired |
| `db/models.py` + migrations | 518 | mine | keep: positions, trades, pending_trade_confirmations, book_cash, cash_events, risk_configs, risk_events, portfolio_snapshots, llm_usage (+run refs), audit_log, scheduler_job_logs, news_articles. Drop: agents, analyses, conversation_turns, debates, channel_watchlist, watch_channel_modes, thread_ownership, generated_reports (dossier replaces), global_directives (doctrine replaces). Fresh alembic baseline. |
| `memory/embeddings.py` | 228 | port (conditional) | news dedup/search only; re-evaluate Phase 4 |
| `memory/context.py`, `personality.py` | ~140 | retire | dossier + doctrine replace prompt-context assembly |
| `personalities/*.md` | | retire | any useful framing folds into doctrine/dossiers |
| `prompts/equity_prompts.py`, `report_templates.py` | ~175 | retire | doctrine (seeded from Equity-Research-Prompts_2026-06-05.md) supersedes; section list cross-checked against it |
| `news/agent.py` | 1154 | mine | port fetchers (multi-provider), dedup, freshness; retire LLM scoring/posting flow (tripwire scoring replaces) |
| `news/dedup.py`, `finlight.py`, `newsdata.py` | | port | |
| `search/providers.py` | 671 | port | api-substrate tool + sandbox search endpoint |
| `data_sources/` (router, jquants, sec_edgar, fx, freshness, registries) | ~1800 | port | becomes the market-data service; single price truth |
| `risk/` (engine, manager, sizing, formatter) | ~1200 | port | unchanged math; feeds desk alerts + entry sizing advice |
| `trades/` (cash, confirmation, parser) | ~830 | port | manual confirmation via adapter |
| `reports/render.py`, `pdf.py`, `storage.py`, `hashing.py`, templates | ~600 | port | finalizer stage uses them; ironclad template → doctrine/templates |
| `reports/generator.py`, `spreadsheet.py` | ~400 | retire | pipeline + agent-built workbooks replace one-pass generation |
| `tools/market_tools.py`, `memory_tools.py` | ~965 | mine | rework as api-substrate tool set + market-data service endpoints |
| `cost/tracker.py` | 350 | port + extend | runs/stages, harness cost capture, per-house caps |
| `config/settings.py` | 329 | mine | pydantic-settings pattern; new layout (/etc/ai-fund) |
| `config/llm_factory.py` | 446 | mine | slims to house registry + provider clients for api substrate |
| `config/agent_config.py`, `books.py`, `lightweight_models.py` | ~460 | retire | houses.yaml + fund.yaml replace |
| `infra/` (retry, rate_budget, logging, shutdown, async_utils) | ~700 | port | |
| `scripts/checkconfig.py`, `llm_ping.py` | ~870 | port | adapted to house registry |
| `scripts/bootstrap_integrations.py` | 1057 | mine | Mattermost reconcile logic → adapter's `reconcile` command; Redis/PG parts retired |
| `scripts/reload_watchlist.py`, `export_watchlist.py` | | retire | coverage API + dossier repo replace |
| `deploy/systemd`, `deploy.sh` | | mine | 3 units (core, runner, adapter) + config-outside-tree; sandbox runtime added |
| `tests/` | 12,334 | mine | port tests with their modules; network-isolation convention carries over |

## Data migration (Phase 7)

- **Positions, cash, trades, risk configs**: straight copy (schemas ported).
- **Existing coverage**: for each currently-watched ticker the PM wants to keep,
  run a (possibly shortened) initiation to give it a real dossier — v2 `analyses`
  content can be attached as historical context but is not trusted as a thesis.
- **Legacy gold reports** (Yakult, LINK + any others the PM provides): imported as
  dossier `reports/` with theses back-filled by a cheap extraction pass, predictions
  registered retroactively where dated targets exist.
- **v2 `llm_usage`/`audit_log`**: archived, not migrated.

## Cutover

v2 staging keeps running through Phase 6. Cutover = freeze v2, migrate data, point
Mattermost bots/channels at v3 adapter (same Mattermost server), decommission v2
services. Rollback = repoint bots to v2 (kept installed until v3 has run two clean
weeks).
