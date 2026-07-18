# Implementor prompt — SPEC-CORE remediation

Hand this to the implementing agent. It assumes `docs/SPEC-CORE-REVIEW.md` is the
authoritative gap list; this prompt sequences the work and pre-settles the decisions the
spec leaves open. Do not restate the review — read it.

---

## Context

You are implementing remediation for `specs/SPEC-CORE.md` on the `ai-fund3` repo. A
section-by-section review (`docs/SPEC-CORE-REVIEW.md`, 2026-07-18) audited §2–§9 against
the implementation. Findings marked **(verified)** in that document were reproduced by
running the code — treat them as facts, not hypotheses.

**Read first, in order:** `AGENTS.md` → `docs/SPEC-CORE-REVIEW.md` → `specs/SPEC-CORE.md`
→ `BUGS.md`. The review's "Synthesis" section explains why the work is ordered the way it
is below.

**The headline finding:** SPEC-CORE describes a service, but neither `engine.tick` nor
`build_scheduler` has a production caller and nothing reads `/etc/ai-fund/*.yaml` from
disk. What exists is a well-tested library that no process invokes. Phase 1 fixes that;
several later findings cannot even be observed until it lands.

---

## Ground rules

`AGENTS.md` binds. Three rules were added *because of* this review and apply with
particular force to your work:

- **Rule 2 — spec-coverage tests assert behavior, never existence.** If your test would
  still pass with the function body replaced by `pass`, it is not a test. `assert
  callable(f)` and `assert set(REGISTRY) == {...}` are banned.
- **Rule 5 — no dead mechanisms.** `tests/unit/test_no_dead_mechanisms.py` enforces this
  mechanically. Each phase below should *delete* entries from its `KNOWN_DEAD` list. If
  you add a public symbol to `core/orchestrator/`, `core/scheduler/` or `cli/format.py`
  without wiring it, that test fails — correctly. Do not add allowlist entries to make it
  pass; wire the symbol.
- **Rule 6 — declared-but-unimplemented is worse than absent.** Phase 5 exists entirely
  because seven state-machine side effects sat declared, no-op'd, and "tested" for three
  review rounds.

Also binding: TDD (test first, watch it fail, then implement). Every change gets a dated
`NOTES.md` entry using the "implemented: X; deferred: Y; missing: Z" form — **never the
word "complete"** unless every named mechanism has a test proving it fires. Run
`pytest tests/unit` after each meaningful change; the baseline is 371 passed / 32 skipped.

**Where to commit.** Straight to `main`, one commit per phase (squash your working commits
before finishing a phase). There is no PR process — do not open one, do not wait on one.
Branch only if a phase turns into a risky refactor you might throw away, and merge it back
as soon as it is green rather than letting it accumulate.

**The review gate is a reading, not a PR:**

1. You self-review the phase against `docs/BUILD_REVIEW_CHECKLISTS.md` — use the section
   for the area you touched as the focus list.
2. You write the phase's `NOTES.md` entry (see "Reporting back" below).
3. A reviewing agent audits `main` against the spec and folds findings into `BUGS.md`.

Each phase must leave `pytest tests/unit` green on its own — a phase that ends red is not
finished, and the next phase does not start on top of it. Baseline as you pick this up:
**371 passed, 32 skipped.**

---

## Decisions (settled — do not re-litigate)

`specs/SPEC-CORE.md` fixes the process topology (§1: one `core` unit = FastAPI +
orchestrator loop + APScheduler) and the tick steps (§3.3). It is silent on the four
points below. These are **decisions, not suggestions**. If you think one is wrong, stop
and raise it with the PM before building around it — do not silently choose otherwise.

**D1 — Concurrency model.** Use a **synchronous `BackgroundScheduler` plus one daemon
thread running the tick loop**, both started from the FastAPI **lifespan** handler, with
uvicorn serving the API. Rationale: `engine.tick`, every job, and the whole repo layer are
sync and take a `Session`; wrapping them in an async scheduler buys nothing and forces
`to_thread` hops around code that is already blocking. One `sessionmaker`, one session per
unit of work, no session shared across threads.

**D2 — Advisory-lock lifecycle.** Leadership is **process-scoped, on one dedicated
long-lived connection** acquired once at startup and held for the process lifetime — not
per job, and not from a pooled connection. Rationale: §7.5 requires "two core processes →
loser idles," which is process-scoped semantics; and a session-level lock taken from a
pooled connection is exactly the bug the review found (job #1 holds the lock on its pooled
connection forever, every later job silently no-ops). If the leader process dies its
connection drops and the lock releases — that is the failover behavior we want. Keep the
API serving on non-leader instances; reads are safe. The scheduler and tick loop must not
start their work on a non-leader.
**Required fires-test:** two jobs run in sequence through `run_job` and *both* execute.
That test fails today.

**D3 — Transaction granularity.** `tick` opens **one session per item** (per run promoted,
per run advanced, per reap batch) and commits each before moving on, per §3.3's "one short
transaction per item, never one giant transaction." `tick` therefore takes a
`session_factory`, not a `Session`. Existing engine functions keep their
`(session, ...)` signatures — only `tick` changes.

**D4 — Runner push vs pull.** `advance_run` currently calls `runner.drain(session)`,
inverting the spec's pull model where runners claim independently. **The production tick
loop must never drive a runner.** Keep the drain call reachable only via explicit
injection so the phase-1.3 `FakeRunner` tests keep working, but the service entrypoint
passes no runner. Add a test asserting the production loop performs no drain.

---

## Phase 1 — Core service entrypoint

**This unblocks everything else.** Nothing in SPEC-CORE currently runs.

1. `core/service.py` (or `core/__main__.py`) exposing a `main()` wired to a
   `[project.scripts]` entry: load settings → load config from disk → acquire leadership
   (D2) → build scheduler → start the tick thread → serve FastAPI. Per D1, start the
   background work from the lifespan handler.
2. The **config read path** (§2.5), which does not exist today: honour
   `AI_FUND_CONFIG_DIR` in `Settings.model_config` (currently hardcoded to `".env"`), read
   the three YAMLs from disk, populate `provider_keys` via the already-written
   `resolve_provider_keys`, and rebind atomically. Add `SIGHUP` and `fund config reload`.
3. Fix the **advisory-lock release bug** per D2.
4. Remove the dev defaults from required `Settings` fields (`database_url`'s placeholder
   DSN, `artifacts_dir`, `dossier_repo_path`, `doctrine_dir`, `market_data_url`) so a
   misconfiguration fails loudly instead of connecting somewhere wrong.
5. Ship the systemd unit with `ExecStartPre=fund checkconfig --strict`, and fix
   `checkconfig.main()`, which currently ignores `--strict` and fabricates a token.

**Done when:** the service starts against a real Postgres, a scheduled job fires, a second
job fires after it (proving D2), `engine.tick` promotes a queued run, and `SIGHUP` swaps
config without dropping in-flight runs. `KNOWN_DEAD` loses `tick` and `build_scheduler`.

---

## Phase 2 — The two live bugs

Small, independent, and shippable ahead of Phase 1 if you prefer — but **do not let them
wait**, since finding 1 is a live exposure.

1. **Read routes are entirely unauthenticated** (verified: all seven read paths return 200
   with no `Authorization` header). §5.2 requires the bearer token on reads. Fix at the
   router include (`core/api/app.py:62`) so it cannot be forgotten per-route, and add a
   test that a read route 401s without a token and 200s with one.
2. **`GET /gates?state=` is silently ignored** — the `Query` has no `alias="state"`, so the
   public param is literally `state_` and `fund gates-list` can never see non-open gates.
   Fix the alias; add a test that filters to a non-open state and gets a different result
   set. Audit every other `Query` param for the same mistake.
3. While here: `request_id` is missing from success bodies, and the 500 handler passes `""`
   (`app.py:58`). Add a test that drives an unhandled exception.

---

## Phase 3 — Wire the disconnected mechanisms

Each item is "the code exists and is tested; nothing calls it." Deleting its `KNOWN_DEAD`
entry is part of the definition of done.

- `queue.reap_expired` into `tick` step 4 — lease expiry currently never fires in
  production. Drive it through `FakeRunner`'s **`hang_roles`**, which has zero call sites
  today, so §9.1's lease-expiry scenario is finally exercised end-to-end.
- `gates.terminal_gate` into `engine.finalize_run`, which special-cases `initiation` only —
  so **`lead_review` and `distillation` currently complete as `succeeded` without ever
  opening their gate.** Test each of the three run types opens its own gate.
- `budgets.run_over_budget` / `pause_for_budget` replacing the engine's private duplicates,
  and move the check **before** each stage attempt: the existing test documents a run
  spending 2× its cap.
- **`raise_cap` / `cancel` gate answers**, which nothing consumes — a budget-paused run is
  currently stranded forever holding its coverage lock.
- `calendar.exchange_sessions` into scheduler registration, converting `"pre:23:00"` into a
  trigger, so `session_tick` and `watch_tick` become reachable at all.
- **Contributor rotation**: derive `last_used_at` from prior stage rows instead of passing
  an empty map, which currently collapses ordering to alphabetical and calcifies verify
  pairs. Test that two consecutive initiations on one coverage rotate.
- `schedule_watchdog` into `JOBS` **and make it emit a desk outbox row** — it currently
  returns a list nobody reads (§7.8, v2's silent-scheduler-death lesson).
- `cli/format.table` into `_emit`'s non-JSON branch, or delete it and the spec's "human
  table by default" claim. `--json` is currently a no-op in both branches.
- `doctrine_version_id`: no caller passes it, so **no run is pinned to a doctrine version**
  and the audit/replay guarantee is unbacked.

---

## Phase 4 — Test-integrity pass

The review lists ten tautological or existence-only tests. Replace, don't delete blindly —
each was guarding something real that deserves a genuine test.

- The ~14 mutating routes with **no auth or idempotency tests** (only `POST /coverage` is
  covered, though §9.4 says "every mutating route"). Parameterize.
- A **CLI smoke test** driving `cli.main.run()` for all 26 commands against the in-process
  app. The parity test proves the argparse subparser exists, not that `run()` has a
  dispatch branch — a command missing from `run()` falls through to `return 2` today and
  every test still passes.
- **Read-model assertions against seeded rows**, not just a 200. The parity test reflects
  `(method, path)` off the router, so a handler body of `return []` passes it.
- Replace the existence-only scheduler assertions in
  `tests/unit/scheduler/test_spec_coverage.py` (`:27`, `:31`, `:36`, `:42`), which
  currently make five `NotImplementedError` stubs read as covered.
- CLI exit codes are inverted vs spec: map 409/422 → 2, transport/5xx → 1.

---

## Phase 5 — The declared-but-unimplemented side effects

`core/db/repo/coverage.py:209` no-ops **seven** state-machine side effects behind a comment
saying they are "cross-module or handled by the caller." **No caller exists.**
`merge_branch`, `predictions_open`, `supersede_predictions`, `spawn_deep_review`,
`expire_open_predictions`, `write_coverage_levels`, `write_lead_history`.

Meanwhile `tests/unit/domain/test_coverage_sm.py:277,283,289` assert those effect *names*
appear in the SM table — strings checked against strings — so the suite reads as though
`decide: reject` supersedes predictions while nothing supersedes anything.

**Either implement each effect with a test asserting the database change, or remove the
name from the SM table.** Both are honest; the current state is not. Note `POST
/coverage/{slug}/promote` silently drops its specced `deep_review` spawn because of this.

---

## Phase 6 — Remaining gaps

Lowest urgency; take from the review's per-section ranked lists.

- **Per-house daily budgets** (BUGS #4). Its "DEFERRED" note **understates the scope** —
  reserve/settle, the midnight-UTC unclaimable window, and the once-per-house-per-day desk
  notice are all absent, and nothing reads or writes `house_budget_days`. Update the BUGS
  entry to say so.
- Split `core/api/routes.py` (810 lines) into the specced `routes/` package.
- The `queue.backoff` nested-vs-flat mismatch — **operator retry tuning is silently
  discarded today** (verified). Add the missing `tests/unit/config/test_fund.py`; its
  absence is why this survived.
- Houses→DB upsert + `fund bootstrap`; `fund config reload`.
- checkconfig covers 4 of 12 checks — prioritize the ones gating a safe start (`.env` mode
  `0600`, alembic head, DB and market-data reachability).
- The remaining shape and param gaps: `redact()` and the structlog processor;
  `pricing.effective_from`; `fund.lead_review.candidacy`; `verify_count` 1..3;
  `GET /predictions?coverage=` taking a UUID where everything else takes a slug;
  `/health` missing leader status, queue depth and last scheduler success.

---

## Reporting back — `NOTES.md` is your channel

**`NOTES.md` is how you communicate back.** Write a dated entry per phase covering:

- **What fires now that didn't before** — name the test, and say what breaks if the
  mechanism is removed. This is the evidence that the item is actually done.
- **Implementation decisions you took**, especially anywhere you departed from D1–D4 or
  from the review's reading. Say why.
- **What the specs don't answer.** If `specs/SPEC-CORE.md` is silent, ambiguous, or
  contradicts SPEC-DOMAIN, write the question down rather than guessing. Spec amendments
  are cheap; wrong guesses that look implemented are what produced this review.
- **What you deferred, and what is still missing** — explicit lists, per AGENTS.md rule 4.
  Never the word "complete" unless every named mechanism has a test proving it fires.

**Do not edit `BUGS.md`.** That is the reviewer's tracker; your `NOTES.md` entries are
folded into it on the next review pass. Item numbers there are stable references — cite
them (e.g. "closes BUGS #7") rather than editing the file.

If you find the review wrong about something, say so in `NOTES.md` with evidence. It was
written from `file:line` reading plus targeted reproduction, not from running the full
system — which did not run at the time.
