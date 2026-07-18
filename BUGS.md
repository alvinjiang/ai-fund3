# BUGS — open items

Round 3, updated. Items 1-3 are DONE on `spec-core-truthing`. Items 4-5 remain.

---

## 1. ~~Missing API routes + CLI commands~~ — DONE

Added 9 routes (GET /runs/{id}, POST /runs/{id}/retry, GET /coverage/{slug}/dossier,
GET /predictions, GET /events, POST /events/{id}/rate, GET /costs, POST /queries,
GET /config/check) + their CLI commands + CoreClient methods. Parity test updated
(26 routes ↔ 26 commands).

Routes still pending their owning specs (deferred, not overclaimed):
- GET /track-record — SPEC-TRACKREC scoring math
- POST /queries/{id}/keep — dossier write path
- GET /runs/{id}/artifacts/{kind} — artifact storage / streaming

## 2. ~~requeue_run has no production caller~~ — DONE

Engine.advance_run now detects stages with backoff-pending available_at and calls
runs_repo.requeue_run (running→queued, lock retained). Fires-test:
test_transient_failure_requeues_run_then_resumes.

## 3. ~~§8 organizational modules not created~~ — DONE

Created with fires-tests:
- core/orchestrator/budgets.py — run_over_budget + pause_for_budget
- core/orchestrator/gates.py — terminal_gate mapping
- core/scheduler/calendar.py — exchange_sessions + all_exchanges
- cli/format.py — table renderer

## 4. Per-house daily budgets (§3.5) — DEFERRED

`house_budget_days` table exists; reserve/settle logic does not. Needs the runner
(phase 2) to check/reserve against the table before launching stage attempts.

## 5. ~~Route/CLI parity test bidirectionality~~ — DONE

Comprehensive spec-coverage test (``test_spec_route_parity.py``) with 6 assertions:
- Every "done" §5 route is in the router.
- No deferred route is silently implemented (stale deferral → flip to done).
- No surprise routes outside the spec.
- Every done route has a CLI command in ROUTE_COMMAND_MAP.
- Every mapped command is a registered argparse subcommand.
- Every deferred route has a reason.

Caught a real gap: ``levels-set`` and ``runs-list`` were in ROUTE_COMMAND_MAP but never
registered as subparsers (the old parity test missed it). Fixed. Also added the standalone
``fund checkconfig`` command (§6, for ``ExecStartPre`` — was missing entirely).
