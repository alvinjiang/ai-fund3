# BUGS — open items

Round 3. The design-agent findings (advisory lock, APScheduler, missing jobs, model-guard,
config examples) are fixed on `spec-core-truthing`. What follows is everything still open.

---

## 1. Missing API routes + CLI commands (the biggest user-facing gap)

~12 §5 routes don't exist. The route/CLI parity test only checks that *existing* routes
have commands — it doesn't catch missing ones.

**Doable now (query existing tables via existing repos):**
- `GET /runs/{id}` — run + stages + attempts + cost
- `POST /runs/{id}/retry` — new run, same params (failed only)
- `GET /coverage/{slug}/dossier` — dossier index row
- `GET /predictions` — ledger query (?coverage=&house=&status=)
- `GET /events` — event query (?coverage=&since=&severity=)
- `POST /events/{id}/rate` — PM 👍/👎 (update pm_rating)
- `GET /costs` — llm_usage aggregation (?by=house|run|day&since=)
- `POST /queries` — create pm_query run (engine.create_run)
- `GET /config/check` — checkconfig result (no secrets)

**Needs other specs — defer:**
- `GET /track-record` — SPEC-TRACKREC scoring math
- `POST /queries/{id}/keep` — dossier write path
- `GET /runs/{id}/artifacts/{kind}` — artifact storage / file streaming

**CLI:** each route above needs a `fund` subcommand. Also missing standalone commands:
`fund checkconfig`, `fund bootstrap`, `fund config reload`.

## 2. requeue_run has no production caller

`runs_repo.requeue_run` is defined, unit-tested (lock survives running→queued), but never
called by the engine. The engine's `advance_run` drains stages via FakeRunner which
exhausts retries in one drain (immediate re-claim). True transient reentry (run goes
queued, stage available_at = now + backoff, resume on next tick) needs the engine to NOT
drain exhaustively — process one stage failure, call requeue_run, return.

## 3. §8 organizational modules not created

The spec names these modules; they don't exist (logic is inline or absent):
- `core/orchestrator/budgets.py` — per-house daily cap logic (house_budget_days)
- `core/orchestrator/gates.py` — gate helpers (extracted from engine)
- `core/scheduler/calendar.py` — exchange session calendar (drives session_tick)
- `cli/format.py` — output table formatter
- `core/api/routes/` — split routes.py into a package (coverage/runs/gates/etc.)

## 4. Per-house daily budgets (§3.5) not implemented

`house_budget_days` table exists; reserve/settle logic does not. Only the per-run cap is
enforced. A house at its daily cap should make its stages unclaimable.

## 5. Route/CLI parity test is one-directional

`test_every_api_route_maps_to_a_distinct_cli_command` checks that every *router route*
has a CLI command — but not that every §6 CLI command has a route. A missing route (item
#1) doesn't fail this test. Once the routes are added, strengthen the parity test to be
bidirectional against the spec's §5 + §6 lists.
