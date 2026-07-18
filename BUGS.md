# BUGS — open items

**Round 4**, after the SPEC-CORE implementation review (`docs/SPEC-CORE-REVIEW.md`,
2026-07-18). This file is the reviewer's tracker. The implementor reports back in
`NOTES.md` — decisions taken, work done, and anything the specs don't answer — and does
not edit this file; findings get folded in here on the next review pass.

Work is sequenced in `docs/PROMPT-SPEC-CORE-REMEDIATION.md`; the **Phase** column below
points at it. Items marked **(verified)** were reproduced by running the code.

| # | Item | Status | Phase |
|---|---|---|---|
| 1 | Missing API routes + CLI commands | DONE | — |
| 2 | `requeue_run` has no production caller | DONE | — |
| 3 | §8 organizational modules | **REOPENED** | 3 |
| 4 | Per-house daily budgets | DEFERRED (scope widened) | 6 |
| 5 | Route/CLI parity test bidirectionality | DONE | — |
| 6 | Read routes are unauthenticated | **OPEN — live exposure** | 2 |
| 7 | Advisory lock is never released | **OPEN — live** | 1 |
| 8 | No core service entrypoint | **OPEN — blocker** | 1 |
| 9 | No config read path | **OPEN** | 1 |
| 10 | `GET /gates?state=` silently ignored | **OPEN — live** | 2 |
| 11 | `queue.backoff` silently discarded | **OPEN — live** | 6 |
| 12 | Seven SM side effects declared, unimplemented | **OPEN** | 5 |
| 13 | Disconnected mechanisms (9 items) | **OPEN** | 3 |
| 14 | Test-integrity gaps | **OPEN** | 4 |

---

## 1. ~~Missing API routes + CLI commands~~ — DONE

Verified in review: 26 routes ↔ 26 commands, parity real, and the read models are genuine
`select()`s against real tables — not stubs that 200 with `[]`. Claim holds up.

Still pending their owning specs: `GET /track-record` (SPEC-TRACKREC),
`POST /queries/{id}/keep`, `GET /runs/{id}/artifacts/{kind}`.

## 2. ~~`requeue_run` has no production caller~~ — DONE

Verified: fires, and `test_transient_failure_requeues_run_then_resumes` fails if removed.
One caveat for Phase 3 — the trigger is "a backoff timer is pending," not "a stage failed
retryably," so a reaper-requeued stage also requeues the run, and the run churns
`queued→running→queued` every tick until backoff expires. Not wrong, but noisy.

## 3. §8 organizational modules — **REOPENED**

Created with tests, but **none of the four has a production caller**, which fails this
repo's own definition-of-done (AGENTS.md rule 5). `gates.terminal_gate`,
`budgets.run_over_budget`, `budgets.pause_for_budget`, `calendar.exchange_sessions`,
`calendar.all_exchanges` and `cli.format.table` are all dead; the engine keeps private
duplicates of the budget helpers, and `--json` is a no-op in both `_emit` branches.

Now enforced mechanically by `tests/unit/test_no_dead_mechanisms.py` — closing this item
means deleting those entries from its `KNOWN_DEAD` list.

Review: §3.5, §4, §6. → Phase 3.

## 4. Per-house daily budgets (§3.5) — DEFERRED, **scope widened**

The previous note read as though only reserve/settle was missing. Also absent: the
`available_at = next UTC midnight` unclaimable window, the run staying `queued`, and the
once-per-house-per-day desk notice (`budget:{house}:{day}`). **Nothing reads or writes
`house_budget_days`** — only the table and the config field exist.

Review: §3.5, §7.3. → Phase 6.

## 5. ~~Route/CLI parity test bidirectionality~~ — DONE

Verified: genuinely bidirectional, would catch real drift, and did. Two structural limits
for Phase 4 — it reflects `(method, path)` off the router, so a handler body of `return []`
passes; and it checks the argparse subparser exists, not that `run()` has a dispatch
branch, so a command missing from `run()` falls through to `return 2` undetected.

---

## 6. Read routes are entirely unauthenticated — **OPEN, live exposure (verified)**

§5.2 requires the bearer token on reads. `create_app` includes the router with no
dependency (`core/api/app.py:62`); `require_pm` is on mutations only. Driving the
in-process app with **no `Authorization` header at all**: `/coverage`, `/runs`, `/gates`,
`/predictions`, `/events`, `/costs`, `/health` all return 200. No test covers it, so CI
cannot see it.

Highest-priority item in this file. Fix at the router include so it cannot be forgotten
per-route. Review: §5.2. → Phase 2.

## 7. Advisory lock is never released — **OPEN, live (verified)**

No `pg_advisory_unlock` anywhere in `core/`. `run_job` takes a **session-level** lock per
fire from a **pooled** `sessionmaker`, and returning a connection to the pool neither
unlocks nor closes it. Job #1 holds the lock on its pooled connection indefinitely; job #2,
handed a different connection, gets `False` and silently no-ops — permanently.

**Net effect once item 8 lands: the scheduler runs exactly one job, then goes quiet.** The
integration test masks this by `close()`ing explicitly in its own `finally`. Decision D2 in
the prompt settles the fix (process-scoped leadership on one long-lived connection).

Review: §4.4. → Phase 1.

## 8. No core service entrypoint — **OPEN, structural blocker (verified)**

Neither `engine.tick` nor `build_scheduler` has a production caller. There is no `core`
process, no `JOBS` entry for the loop, no systemd unit. SPEC-CORE §1 specifies one `core`
unit (FastAPI + orchestrator loop + APScheduler); what exists is a well-tested library
that no process invokes.

Consequences that are invisible until this lands: `reap_expired` never fires, so a crashed
worker's stage stays `running` forever; `session_tick`/`watch_tick` are unreachable;
`schedule_watchdog` is unschedulable. Also note `scheduler.py:7` claims systemd calls
`start()`/`shutdown()` — it does not (AGENTS.md rule 3 violation, fix while there).

Review: Synthesis, §3.3, §4. → Phase 1.

## 9. No config read path — **OPEN**

Nothing reads `/etc/ai-fund/{houses,fund,pricing}.yaml` from disk; `reload_config` takes
YAML *strings*, and `get_config()` raises unless a test called `set_config`.
`Settings.model_config` hardcodes `env_file=".env"`, ignoring `AI_FUND_CONFIG_DIR`, so the
operator `.env` is never read. `resolve_provider_keys` is written and tested but has no
caller, so `provider_keys` is `{}` at runtime and **no house API key is reachable**.

Related: required settings carry dev defaults (including a placeholder DSN), turning a
misconfiguration into a silent wrong-target connection; `redact()` and the structlog
processor don't exist, so AGENTS.md Rule 2 is unenforced by code; and `cli/main.py:55`
reads `CORE_API_TOKEN` straight from `os.environ`.

Review: §2.1, §2.5. → Phase 1.

## 10. `GET /gates?state=` is silently ignored — **OPEN, live (verified)**

The `Query` has no `alias="state"`, so the public param is literally `state_`
(`routes.py:508`) while `cli/client.py:115` sends `?state=`. **`fund gates-list` can only
ever see open gates.** `list_runs` gets this right (`alias="status"`) — audit every other
`Query` param for the same mistake.

Review: §5.1. → Phase 2.

## 11. `queue.backoff` silently discarded — **OPEN, live (verified)**

`fund.yaml` nests `backoff: { base_s, max_s, jitter }`; `QueueCfg` declares them flat, so
pydantic's `extra="ignore"` drops the nested key. Reproduced: setting
`backoff: { base_s: 120, max_s: 60, jitter: 0.9 }` still loads as `30 / 3600 / 0.2`.
**Operator retry tuning has no effect.** Masked because the code defaults equal the
template values. There is no `tests/unit/config/test_fund.py` — that absence is why it
survived.

Review: §2.3. → Phase 6 (or fold into Phase 1, since it's a config-layer fix).

## 12. Seven SM side effects declared but unimplemented — **OPEN**

`core/db/repo/coverage.py:209` no-ops `merge_branch`, `predictions_open`,
`supersede_predictions`, `spawn_deep_review`, `expire_open_predictions`,
`write_coverage_levels`, `write_lead_history` behind a comment saying they are
"cross-module or handled by the caller." **No caller exists (verified).**

Made worse by `tests/unit/domain/test_coverage_sm.py:277,283,289`, which assert the effect
*names* appear in the SM table — strings against strings — so the suite reads as though
`decide: reject` supersedes predictions while nothing does. Also why
`POST /coverage/{slug}/promote` silently drops its specced `deep_review` spawn.

Either implement each with a test asserting the DB change, or remove the name from the
table. AGENTS.md rule 6. Review: §9.2, tautology list. → Phase 5.

## 13. Disconnected mechanisms — **OPEN**

Beyond item 3: `reap_expired` (never called from `tick`), `hang_roles` (zero call sites, so
the lease-expiry scenario is untested at the orchestrator level), contributor rotation
(always passed an empty `last_used_at`, so ordering collapses to alphabetical and verify
pairs calcify), `raise_cap`/`cancel` gate answers (nothing consumes them — a budget-paused
run is stranded holding its coverage lock), `doctrine_version_id` (no caller passes it, so
no run is pinned to a doctrine version), terminal gates for `lead_review`/`distillation`
(they complete as `succeeded` without ever opening a gate), and the cross-check's stance
and severity arms (hardcoded `None`, so only the ΔTP trigger can fire).

Also: the per-run budget check runs *after* the runner drains every claimable stage, so
runs provably overspend — 2× the cap in the existing test.

Review: §3.2–§3.5, §4, §7. → Phase 3.

## 14. Test-integrity gaps — **OPEN**

Ten tautological or existence-only tests are inventoried in the review. The pattern that
matters: `assert callable(f)` and `assert set(JOBS) == {...}` make five
`NotImplementedError` stubs read as covered.

Coverage holes behind them: only `POST /coverage` has auth/idempotency tests though §9.4
says "every mutating route" (~14 uncovered); no test invokes `main()`/`run()` for any CLI
command; no test drives an unhandled exception to a 500 (which is why the empty
`request_id` at `app.py:58` survives); config reload atomicity and the houses upsert have
neither code nor tests.

AGENTS.md rules 2 and 5 now bind here, and `tests/unit/test_no_dead_mechanisms.py`
enforces the dead-symbol half mechanically. Review: §9. → Phase 4.
