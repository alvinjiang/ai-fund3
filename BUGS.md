# BUGS — spec-domain review findings

Review date: 2026-07-18. Reviewed `80e432b`/`b8fe9cf` (spec-domain, already merged to
`main`) against `specs/SPEC-DOMAIN.md` and `docs/BUILD_REVIEW_CHECKLISTS.md`.

Status at review time: unit suite `335 passed, 42 skipped`; integration suite
`9 passed` (Docker now available — run with `pytest tests/integration -m integration`).
Every finding below passes the full suite as it stands. That is the point: these are
the failure modes the checklist predicted a green build would hide.

Ordered by priority. Each item is independently actionable.

---

## 1. Coverage-lock acquisition is a SELECT-then-INSERT race

**Where:** `core/db/repo/runs.py:94-104` (`start_run`)
**Spec:** §4.10 — "Acquired by the orchestrator (`INSERT … ON CONFLICT DO NOTHING`) …
Failure to acquire is **not an error**." §9.3 — "exactly one acquires; the loser stays
`queued` (no exception, no deadlock)."

```python
existing = session.get(models.CoverageRunLock, r.coverage_id)   # plain SELECT, no row lock
if existing is not None and existing.run_id != run_id:
    return False
if existing is None:
    session.add(models.CoverageRunLock(...))                     # INSERT on a PK
```

`session.get()` takes no row lock. Two transactions can both observe `None` and both
INSERT against the `coverage_id` primary key; the loser raises `IntegrityError` at
flush/commit instead of returning `False`. There is no `except IntegrityError` anywhere
in `core/db/` (`grep -rn "ON CONFLICT\|IntegrityError" core/db/` → no hits).

**Evidence.** The race is real but the natural window is sub-millisecond:

- Shipped test `test_concurrent_start_run_only_one_acquires_lock` — passes.
- Same test rewritten with a `threading.Barrier` releasing 8 threads at once — still
  passes (`acquired=1 refused=7 errors=[]`). The threads serialize by scheduling luck.
- `start_run`'s logic inlined with `time.sleep(0.25)` between the SELECT and the INSERT,
  4 threads → `acquired=1 refused=0 errors=['IntegrityError','IntegrityError','IntegrityError']`.

So: a green test does **not** demonstrate safety here, and no realistic test will catch
it reliably. Two queue workers polling simultaneously will hit it intermittently in
production, surfacing as a confusing unhandled `IntegrityError` rather than the
documented "stay queued" behaviour.

**Fix:** use a real `INSERT … ON CONFLICT DO NOTHING` (`sqlalchemy.dialects.postgresql.insert(...).on_conflict_do_nothing(index_elements=["coverage_id"])`),
then re-read to determine whether this run owns the lock. Return `False` when it does
not. Keep it inside the caller's transaction — do not commit inside the repo.

**Also add:** a barrier-synchronized version of the integration test. It will not
reliably catch a regression, so pair it with the widened-window variant above as an
explicit, documented test of the failure mode.

---

## 2. Prediction idempotency has the same race

**Where:** `core/db/repo/predictions.py:52-56` (`register_from_stage`)
**Spec:** §4.12 item 3 — "Re-registering the same stage's results (retry, resumed run)
is a no-op via `ON CONFLICT DO NOTHING`."

Identical select-then-insert shape against `predictions.idempotency_key`. Two concurrent
replays of the same stage (exactly the retry/resume scenario the key exists for) can both
miss and the loser raises `IntegrityError`. No concurrency test exists for predictions at
all — §9.3 has them for queue claim, lease/reaper, coverage lock and outbox, but not this.

**Fix:** same as #1 — `on_conflict_do_nothing(index_elements=["idempotency_key"])`, then
re-select to return the winning row.

---

## 3. Retry backoff is not implemented — handoff dropped across two branches

**Where:** `core/db/queue.py:181` and `core/db/queue.py:210-212`
**Spec:** §6.2 — "`available_at = now() + backoff(attempts)` (exponential with jitter;
parameters from config)."

```python
stage.available_at = utc_now()  # backoff is config-driven (SPEC-CORE); no delay here
```

The chain broke in the middle:

1. `core/domain/run_sm.py:148` correctly emits the `set_available_at_backoff` side effect.
2. `core/db/queue.py:181` discards it and defers to SPEC-CORE by comment.
3. spec-core added the knobs — `backoff_base_s=30`, `backoff_max_s=3600`,
   `backoff_jitter=0.2` (`core/config/fund.py:43-45`).
4. **Nothing reads them.** `grep -rn "backoff_base_s\|backoff_max_s\|backoff_jitter"` →
   only the definitions.

So there is no backoff anywhere on the retry path, and three config knobs advertise
behaviour that does not exist. `tests/unit/domain/test_run_sm.py:138` asserts the side
effect is *emitted* — it passes while the effect is thrown away.

A stage failing against a rate-limited or degraded provider is re-claimed immediately,
every time, up to `max_attempts`. This is the behaviour §6.2's jitter requirement exists
to prevent.

**Fix:** consume the `set_available_at_backoff` side effect in `queue.py` (both
`fail_stage` and `reap_expired`), computing
`min(backoff_base_s * 2**(attempts-1), backoff_max_s)` with `±backoff_jitter` applied,
reading from the fund config. Test that `available_at` grows across successive failures.

---

## 4. `CASCADE` on four `coverage.id` foreign keys

**Where:** `core/db/models.py:97`, `:112`, `:139`, `:160`
**Spec:** §2 — "FKs: `ondelete=\"RESTRICT\"` by default; `CASCADE` only from a run to its
own children (`run_stages`, `stage_attempts`)."

| Table | Column | Current | Should be |
|---|---|---|---|
| `coverage_contributors` | `coverage_id` | CASCADE | RESTRICT |
| `coverage_levels` | `coverage_id` | CASCADE | RESTRICT |
| `coverage_transitions` | `coverage_id` | CASCADE | RESTRICT |
| `coverage_lead_history` | `coverage_id` | CASCADE | RESTRICT |

The `runs`→`run_stages`→`stage_attempts` CASCADEs (`models.py:233`, `:278`) are correct
and sanctioned.

Dormant today — nothing deletes a `coverage` row — but it is baked into the DDL. The
worst instance is `coverage_transitions`, the state-machine audit ledger that §4.5 calls
"a bug the tests must catch" if lost. Any future dedupe/cleanup script issuing
`DELETE FROM coverage` silently destroys the audit trail RESTRICT exists to protect.

**No test anywhere exercises FK delete behaviour in either direction** — nothing proves
RESTRICT blocks, nothing proves the sanctioned CASCADE cascades. That absence is why
this shipped.

**Fix:** change the four to `ondelete="RESTRICT"`, regenerate the migration (see #5),
and add an integration test asserting a `coverage` delete raises while a `run` delete
cascades to its stages and attempts.

---

## 5. The Alembic baseline is tautological — `alembic check` cannot detect drift

**Where:** `core/db/migrations/versions/0001_baseline.py:22-23`
**Spec:** §7 — "`target_metadata = Base.metadata` so `alembic check` … can run in CI."
§9.3 — "models and migration cannot drift."

```python
def upgrade() -> None:
    Base.metadata.create_all(op.get_bind())
```

The migration contains no independently authored DDL. It builds the schema *from* the
same `Base.metadata` object that `alembic check` then diffs the database against, so the
check is comparing metadata to itself. It can only fail on a SQLAlchemy-internal bug —
never on "someone edited `models.py` and forgot the migration," which is its entire
purpose. The docstring states the motive outright: *"via `create_all` (so `alembic check`
reports zero drift)"*.

`tests/integration/db/test_alembic.py` genuinely runs against real Postgres and passes —
it is the assertion underneath that is hollow.

**Fix:** regenerate the baseline as real DDL. Against an empty database:

```bash
alembic revision --autogenerate -m "0001 baseline"
```

Replace `0001_baseline.py` with the generated explicit `op.create_table(...)` /
`op.create_index(...)` calls. Then **verify the check can actually fail**: add a throwaway
column to `models.py`, confirm `alembic check` reports a diff, and revert. A baseline that
has never been observed failing is not a baseline.

Secondary: `test_alembic.py` asserts only 7 of ~30 table names exist. Once the check is
real that matters less, but enumerating all of them is cheap.

---

## 6. `claim_stage`'s `FOR UPDATE` is not scoped to `run_stages`

**Where:** `core/db/queue.py:58`
**Spec:** §6.3 — literal SQL is `FOR UPDATE OF s SKIP LOCKED`.

`.with_for_update(skip_locked=True)` has no `of=`, so it compiles to a bare
`FOR UPDATE SKIP LOCKED`. Postgres applies that to *every* table in the statement, and
the query joins `runs`:

```
FROM run_stages JOIN runs ON runs.id = run_stages.run_id
 ... LIMIT 1 FOR UPDATE SKIP LOCKED
```

So claiming any stage also locks its parent `runs` row. Two workers claiming two
*independent* stages of the same run contend on that shared row, and claims contend with
`start_run`/`finish_run`/`requeue_run`. `test_concurrent_claim_exclusivity` still passes
because `SKIP LOCKED` renders this as extra serialization rather than incorrectness — it
silently defeats §6.3's stated intent ("a locked row is skipped rather than blocking").

**Fix:** `.with_for_update(skip_locked=True, of=models.RunStage)` — compiles to
`FOR UPDATE OF run_stages SKIP LOCKED`. One line.

The `depends_on_seq` `EXISTS` half of the claim query is correct and verified.

---

## 7. `set_lead` never checks `houses.enabled`

**Where:** `core/domain/coverage_sm.py:53-63` (`TransitionContext`), `:204-210`
(`_check_guards`), `core/db/repo/coverage.py:289-293`
**Spec:** §5 row 14 — guard is "new house **`enabled AND assignable`**".

Only `assignable` and `meta` are checked. `TransitionContext` has no `enabled` field, and
the repo loads the `House` row but passes only `h.assignable` / `h.meta`. Missing at every
layer — this is not a deferred guard. The column exists (`core/db/models.py:49`).

Failure: an operator disables a house (dead API key, vendor sunset), `enabled` flips false
on the next `checkconfig` upsert, and `set_lead(coverage_id, that_house, ...)` still
succeeds. `coverage.lead_house` now points at a house that must receive no new work.

**Fix:** add `new_house_enabled: bool = True` to `TransitionContext`, raise in the
`SET_LEAD` guard when false, pass `h.enabled` from the repo. Add a test.

---

## 8. `price_pins` (§12 amendment) never landed

**Where:** absent from `core/db/models.py`
**Spec:** §12 — "**None of this spec has been built yet, so the builder folds these in
directly**". Row: `price_pins (exchange, ticker, trading_date, value, currency, source,
pinned_at)`, unique on the first three (SPEC-MARKETDATA §2.1).

9 of the 10 §12 amendments landed correctly — including `mm_channels`/`mm_posts` and
`house_metric_snapshots`, which belong to later specs exactly as `price_pins` does. So
this is an oversight, not a scope decision.

**Fix:** add the table + unique constraint to `models.py`, folded into the regenerated
baseline from #5. Schema only — SPEC-MARKETDATA owns all semantics and writes.

---

## 9. Legal-transition tests assert side effects exist, not that they are correct

**Where:** `tests/unit/domain/test_coverage_sm.py:61`
**Spec:** §9.1 — "every legal transition in §5 returns the expected `to_state`, `cause`,
**and side-effect list**."

```python
assert isinstance(t.side_effects, list) and t.side_effects
```

Non-emptiness only. 12 of 19 transitions have zero content assertions; the 5 with
supplementary tests check subsets (e.g. `decide:active` checks `merge_branch` and
`predictions_open` but never `write_coverage_levels`, `set_decided_at`, or
`outbox:coverage.state_changed`). Dropping a side effect or typoing `outbox:gate.opened`
→ `outbox:gate_opened` passes the full suite.

This is the checklist's named "weakened test" pattern (lines 15-18).

**Fix:** put the expected side-effect list in the `LEGAL` table alongside `to_state` and
`cause`, and assert set equality.

**Not a bug, worth preserving:** the *illegal*-transition test is a genuine cross-product
(`list(CoverageState) × list(Action)`, skipping the 19 legal pairs) — the checklist's key
item, correctly implemented. Do not let a refactor erode it.

---

## Minor

- **`fail_stage` drops `kill_reason`** — `core/db/queue.py:172` calls `_close_attempt(...)`
  without forwarding `attempt.kill_reason` from the `AttemptIn` DTO
  (`core/db/schemas.py:64`). §4.9 defines `wall_clock | budget | operator | lease_expired`;
  today only the reaper's inlined `"lease_expired"` (`queue.py:208`) ever writes the
  column, and `_close_attempt`'s `kill_reason=` parameter is dead code. A runner reporting
  a `wall_clock` kill silently stores `NULL`. No test reads `kill_reason` anywhere.
- **`StageIn.max_attempts: int = 3`** — `core/db/schemas.py:31`. §4.8 says "from config at
  creation." A silent policy default is the repo's named anti-pattern (AGENTS.md); a
  caller that forgets to resolve it from config gets 3 instead of a loud failure.
- **False docstring** — `core/db/repo/outbox.py:5` claims "Concurrent consume exclusivity
  is integration-tested." No such test exists. `consume()` uses `SKIP LOCKED`, which
  SQLite silently drops, so its only coverage (`tests/unit/db/repo/test_outbox.py:31`)
  exercises a materially different query than production. Either write the Postgres test
  or delete the claim.
- **`RunAction.RESUME`** — `core/domain/run_sm.py:41,72` defines a `waiting_pm → running`
  edge. §6.1's diagram has only `waiting_pm ──▶ succeeded`, and §4.11 calls `pm_decision`
  "the answer of the **terminal** gate". Nothing calls it. It is baked into the run SM's
  `LEGAL` seed table, so the cross-product test treats it as sanctioned rather than
  flagging it. Remove it, or amend the spec deliberately.
- **Decimal fidelity on SQLite** — `core/db/types.py:35-60`. `Money`/`Price`/`Cost`/`Prob`
  are bare `Numeric` subclasses, so on SQLite SQLAlchemy round-trips through `float`
  (verified: stored as `real`/`integer`). Production is Postgres with native `NUMERIC` and
  is unaffected, and no realistic value reaches the float64 threshold — all `Price` columns
  hold per-share/target prices, `Money` holds USD NAV. **The defect is test fidelity:** the
  unit suite cannot catch a money-precision regression, and
  `tests/unit/db/test_types_base.py:41-57` asserts a 9-significant-digit value round-trips,
  passing by luck while advertising a guarantee that does not hold. Consider a
  Decimal-preserving `TypeDecorator`, and at minimum make the guard test use a value that
  actually stresses the declared `Numeric(20,6)` precision. Columns are also typed
  `Mapped[Any]` rather than `Mapped[Decimal]`, so static typing will not flag a `float`
  assignment either.
- **`README.md` not updated** — CONVENTIONS.md requires it on every feature branch. The
  branch added ~30 tables, two state machines, 5 repo modules, Alembic, and `tests/fakes/`;
  README's Layout section mentions none of `core/db/`, `core/domain/`, `alembic.ini`, or
  `tests/fakes/`. (The dated `NOTES.md` entry is present and correct.)
- **Boolean columns without `server_default`** — `runs.mutates_dossier`
  (`models.py:186`), `disagreements.material` (`models.py:449`). §2: "Booleans:
  `nullable=False` with explicit `server_default`."
- **No `core/db/repo/dossier.py`** despite §2's module layout naming it;
  `dossier_index`/`dossier_commits` have no repository module.
- **`queue.heartbeat`** requires a `lease_seconds` kwarg beyond §8's documented
  `heartbeat(stage_id, worker_id)`. Probably necessary — reconcile code and spec either way.
- **Dead branches** — `core/db/repo/coverage.py:178-183` handles `acquire_lock`/
  `release_lock` side effects the coverage SM never emits. Locking lives in
  `start_run`/`finish_run`. Misleading cruft.
- **`outbox.pending_count()`** (`core/db/repo/outbox.py:93-96`) returns an
  id-or-None existence probe, not a count. Rename or fix.

---

## Untested paths worth covering

Correct by inspection, but nothing proves them — all named in the checklist:

- `requeue_run` (`core/db/repo/runs.py:112`) has **no caller and no test** anywhere. The
  checklist explicitly names "the lock survives `running → queued` re-entry."
- Reaper writing `kill_reason='lease_expired'` in the same transaction as the requeue —
  correct in code (`queue.py:206-209`), but neither the unit nor the integration reaper
  test reads the field.
- `monitor_tick` starting against an *already-held* lock. The existing test only proves a
  `monitor_tick` run takes no lock itself.
- FK delete behaviour in either direction (see #4).

---

## Process notes

- **There is no CI.** No `.github/workflows` anywhere in the repo. The "Every branch"
  checklist requires the no-hardcoded-model/price grep test to "actually run in CI"; that
  test exists (`tests/unit/test_no_hardcoded_models.py`, added in spec-core) but nothing
  runs it automatically. Until CI exists, the integration suite in particular will keep
  silently not running.
- **The integration suite is opt-in** (`-m integration`) and had never executed before this
  review — Docker was previously unreachable. It passes now (9/9). Whatever CI gets built
  must run it, or findings #1, #2, #5 and #6 recur.
- If a fix here changes `core/db/models.py`, it must land together with the #5 baseline
  rewrite — otherwise the tautological migration hides the schema change.
