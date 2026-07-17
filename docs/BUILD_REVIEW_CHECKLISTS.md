# BUILD-branch review checklists (Fable-tier criteria, executable by any reviewer)

Written 2026-07-18, before any BUILD begins, because the strongest review tier may not
be available later. Each section is what a reviewer must verify on that spec's branch
**beyond** "tests pass" — these are the failure modes a mid-tier builder is most likely
to introduce while every test still passes. Review the diff against the spec section
cited; when in doubt, the spec wins. Consider running `/code-review ultra` on each
branch; paste the relevant section below into the review prompt as focus areas.

## Every branch (read first)

- **Spec diff, both directions.** Anything the spec requires that is absent; anything
  present the spec does not sanction. Scope creep into a later spec's territory is a
  defect even when the code is good.
- **Tests assert outcomes, not calls.** Reject tests that mock the unit under test or
  assert "function X was called" where the spec names a state (`a coverage_transitions
  row exists`, `main is unchanged`). The spec's test plans name concrete assertions —
  check they landed *as stated*, not weakened.
- **The one-transaction rule.** Every mutating path writes its domain change +
  transition + `audit_log` + outbox row in one transaction (SPEC-DOMAIN §4.5). Look for
  a `commit()` between them, or a helper that opens its own session.
- **Decimal end to end.** No `float` on any money/price path. The v2 code being ported
  calls `float()` at provider boundaries — the port must convert to `Decimal` there.
  Grep the diff for `float(`.
- **No silent defaults.** A missing config key is a `checkconfig` failure, never
  `.get(key, sensible_default)` — the "helpful fallback" is this repo's named
  anti-pattern (AGENTS.md; v2 lesson). Same for an unpriced model (`NULL` + alert,
  never `0`) and a missing confidence (fail the stage, never fill).
- **Secrets.** No `os.getenv` outside the settings module; no secret on argv; the
  repo-wide no-hardcoded-model/price grep test exists and actually runs in CI.
- **SQLite/Postgres duality.** Raw SQL (claim, outbox, budget) must have real-Postgres
  integration tests; nothing PG-only may sit on a unit-tested path.

## spec-domain (SPEC-DOMAIN)

- Claim SQL contains the `depends_on_seq` `EXISTS` (§6.3) **and** `FOR UPDATE OF s SKIP
  LOCKED`; the reaper closes the open attempt row (`kill_reason='lease_expired'`) in the
  same transaction as the requeue.
- The illegal-transition test is **generated from the cross-product** of
  `CoverageState × Action` minus the legal table (§9.1) — a hand-maintained list rots
  exactly when a transition is added (e.g. 4b/5b).
- Prediction immutability is enforced in the repository (attempting to update
  `value`/`horizon_date`/`house` raises), with a test per forbidden column; nothing
  deletes.
- Idempotency keys are composed from **system ids** (`stage_id`), never model-supplied
  strings — read the actual sha256 composition, don't trust the variable name.
- FK policy: `CASCADE` only run→children; everything else `RESTRICT`. `houses` rows are
  disabled, never deleted.
- A queued mutating run on a locked coverage stays `queued` with **no error and no
  backoff growth**; `monitor_tick` starts regardless (§4.10). The lock survives
  `running → queued` re-entry and `waiting_pm`.
- `alembic check` (models vs migration no-diff) is an integration test that actually
  runs.

## spec-core (SPEC-CORE)

- Planner is pure and refuses a `meta` house in any analyst role; rotation is derived
  from prior stage rows, not a stored cursor.
- Gate answering: same answer replays stored response; different answer → 409; effects
  in one transaction; `allowed_answers` is code-owned.
- Budget reserve/settle under `SELECT … FOR UPDATE` on `house_budget_days`; the
  at-cap desk notice is deduped to one per house per day.
- Scheduler jobs are tested by direct invocation; every job's `trigger_ref` is
  deterministic and the partial unique index rejects the double-fire.
- Config reload is an **atomic rebind** (one module-level reference swap); runs pin
  resolved params at creation — test that a mid-run config edit changes nothing.
- Route↔CLI parity test enumerates **both directions**.
- The FakeRunner happy path asserts: transient re-entry does **not** re-execute stage 1;
  the auto cross-check fires on a registered TP move even when the prose says it isn't
  needed (§9.2 — this is the "model talks its way out" regression test).

## spec-runner (SPEC-RUNNER — the risk phase; review hardest)

- **Search the diff for repair.** Any code path that writes a default
  `confidence`/`recommendation`/`stage_result` field on the model's behalf is the
  single most likely "helpful" bug (v2's fabricated Neutral/50%). Gates fail; they never
  fill (§6.1.5).
- Secrets: `launch_spec()` puts no secret on argv (test spans **all** drivers); key
  file is tmpfs `0600` via `--env-file`; transcript redaction happens **before**
  hashing/storage — the `key_echo` scenario asserts the pattern appears nowhere
  (artifact, DB, logs).
- Egress: deny-by-default; house X's allowlist is exactly {X's provider, market-data,
  search} (2 entries without search); denials are logged and counted; the bwrap
  no-enforcement path makes `checkconfig --strict` FAIL unless the explicit loud
  opt-out is set (§5.2).
- Reconciliation gate: workbook **recalculated** (`soffice`), cells read
  `data_only=True`, formula-presence read `data_only=False`; tolerance from config; a
  typed summary cell fails.
- Worktree: sandbox mount is the ticker dir with **no `.git`**; sparse-checkout cone is
  `<slug>` (no `dossiers/` prefix); commit message `run:<id> stage:<seq> <role>@<house>`;
  reject retains the branch, `main` untouched.
- Cost: killed/failed attempts write their cost; `estimated` is flagged, `NULL` never
  becomes `0` (grep for `or 0` around cost math).
- API loop: the tool registry contains **no write tool** (asserted); §8's triage rule —
  a non-`nothing_material` api result is re-planned on harness, never accepted.

## spec-initiation (SPEC-INITIATION)

- Verifiers are never the lead; `--allow-single-house` is explicit, surfaced in the
  gate payload, and never a default.
- The dropped-correction gate (§4.1.3) is a numeric comparison across stage results —
  verify it catches a finalizer restoring a pre-correction TP.
- Predictions register at the finalizer only, `open`; the scoring skip for
  `decision_pending`/rejected coverage has the **joint** test with spec-trackrec.
- `predictions.yaml` written by a model → gate failure; the mirror is code-generated
  and matches the ledger exactly.
- `decide: reject` test asserts dossier `main` is unchanged **in git**, not just in the
  index row.

## spec-monitoring (SPEC-MONITORING)

- Severity flows **from `tripwires.yaml` to the event**; model `materiality` is never
  stored. Check the mapping direction in code, not just the test names.
- Invented/retired tripwire ids dropped; trust tiers gate spawns; escalation caps are
  counted from `events` **rows**, not an in-memory counter.
- Fail-closed test: garbage scoring twice → zero escalations, `scored_at IS NULL`,
  price/level events still fire.
- Ported fetchers: no HTTP client construction at import time (the named v2 miss);
  provider failure skips, tick continues.
- The `$0 quiet tick` test asserts `FakeLLM.calls == 0`.
- `dedup_miss_rate` is actually computed and logged per tick — without it the pgvector
  reversal criterion (§7) has no evidence.

## spec-adapter (SPEC-ADAPTER)

- The five rendering-safety steps (§6) each have an injection test: broadcast mentions
  stripped, leading slashes escaped, no interactive props from model text, redaction
  before post, bounded truncation.
- `is_bot` skip includes our own posts (bot-loop test terminates); the idempotency key
  is the deterministic adapter-owned hash, **not** Mattermost's `trigger_id`.
- The adapter has no DB credentials anywhere — outbox only via core API; redelivery
  dedupe via `props.ai_fund_event_id` survives crash-between-post-and-ack.
- Every command path replies — the parametrized test covers success/4xx/5xx/exception.

## spec-trackrec (SPEC-TRACKREC)

- The split regression test (1:3 mid-horizon still a hit) passes against
  spec-marketdata's `adj_factor_cum` end-to-end, not against a hand-built factor.
- Dividends: in `realized_return`, **not** in hit detection. Halt → `expired` with
  reason, no fabricated price.
- Calibration excludes `NULL` confidence (never imputes 0.5) while hit rate includes
  those rows.
- `min_n` blocks lead-review candidacy; the candidacy job creates **zero** runs.
- Lead change: open predictions keep the old house (the accountability regression
  guard); `direction`/`pinned_price` are code-written at registration.

## spec-marketdata (SPEC-MARKETDATA)

- Pins: first-writer-wins on the unique key; a changed provider answer does **not**
  change a served pin; `repin` is explicit and audited. `as_of` comes from a bar/date
  field, never `fast_info` (§2.1–2.2).
- `Decimal` conversion at every provider boundary (ported v2 code is `float`-typed).
- The J-Quants plan rule (§3.3) is a real `checkconfig` check with a failing fixture.
- Provider imports live behind the protocol (no import-time client).

---

# PM gate 2.4 / 3.3 failure triage (in order — from the standing risks)

When the pilot misses the Yakult bar, classify **before** touching anything:

1. **Judgment miss** (analysis is wrong/shallow/uncited but well-formed) → doctrine and
   role prompts first. Never fix judgment with code.
2. **Contract miss** (artifact absent, field missing, schema retries) → `task.md`
   assembly and gate *config* (`required_artifacts`, validator numbers surfaced per
   SPEC-RUNNER §4.2) → then the role prompt. Check the retry prompt named the omission
   correctly before blaming the model.
3. **Harness-ops miss** (cost `estimated`, egress denial bursts, timeouts, unparseable
   CLI output) → driver/sandbox config; consider switching first house (SPEC-RUNNER §9
   — parseable usage is a selection criterion).
4. **Only then code.**

Record every fix as a dated `NOTES.md` entry — these are the first doctrine lessons and
the distiller's seed corpus. If a gate blocked *good* work, that is equally a finding:
loosen the config, not the principle.
