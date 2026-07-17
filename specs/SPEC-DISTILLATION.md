# SPEC-DISTILLATION — Monthly doctrine distillation (Claude meta)

**Phase:** 7.1 (SPEC) · **Implements:** PROMPTS.md 7.1 · **Branch:** `spec-distillation`
**Status:** **SPEC ONLY — do not build in this phase.** PROMPTS marks 7.1 as SPEC+BUILD;
the BUILD half is deliberately deferred to a separate session (this session is spec-tier
only). Nothing here is implemented yet.
**Depends on:** SPEC-DOMAIN (`doctrine_versions`, runs, corrections, predictions),
SPEC-CORE (scheduler, gates), SPEC-RUNNER (harness stage, gates), SPEC-TRACKREC (metrics
rendering), SPEC-INITIATION (`lessons.md`)
**Preliminary pending PM gates 2.4/3.3** and the operator decision in §3 (doctrine repo
layout).

This is the **learning loop**, formalized from the PM's hand-maintained "Further notes
from past mistakes": collect what went wrong → cluster it → propose doctrine amendments as
a reviewable diff → **the PM approves** → the next runs load the new doctrine. Nothing
self-amends.

The distiller is **Claude, and Claude is not an analyst.** It never writes a dossier, a
prediction, or a report; it reviews the system. That firewall is enforced by code (§6),
not by prompt.

---

## 1. Scope

- The `distillation` run: trigger, inputs, stage, sandbox, outputs.
- The **evidence bundle** (gathered by code, not by a model).
- Doctrine **amendment as a git branch + PR-style diff**, plus a **system-health memo**.
- The **PM approval flow** and the **doctrine version bump**.
- Deterministic gates specific to this run type — above all the **meta firewall** and the
  **citation check**.

---

## 2. Trigger and window

- **Monthly** (`fund.yaml scheduler.distillation`, e.g. `0 4 1 * *`) or PM-triggered
  (`fund run new distillation`, `/distill`).
- `coverage_id = NULL` (a meta run — the only run type allowed to have none).
- Idempotent: `trigger_ref = distillation:{yyyy}-{mm}` (unique → a scheduler re-fire
  creates nothing).
- **Evidence window** = since the `approved_at` of the **current** doctrine version (i.e.
  since the last doctrine change actually landed), *not* since the last distillation run.
  So evidence stays in scope until a rule addressing it is merged — a rejected amendment
  does not make its incidents disappear.
- The task **also** carries any **previously rejected** amendments with the PM's rejection
  notes, under an explicit instruction: *do not re-propose these unchanged; either drop
  them or make the case differently with new evidence.*

---

## 3. Where doctrine lives (operator decision required)

Doctrine is **data the fund learns**, not code — and an approved amendment must survive a
`git reset` in the deploy tree (design/05 §6, the hard-won v2 lesson). Two supported modes,
`fund.yaml doctrine.mode`:

| Mode | Layout | Amendment flow |
|---|---|---|
| **A — standalone repo (recommended default)** | A dedicated doctrine git repo at `settings.doctrine_repo_path` (e.g. `/srv/ai-fund/doctrine.git`), with a working checkout at `settings.doctrine_dir` — **outside the deploy tree**. Seeded at `fund bootstrap` from the repo-tracked `doctrine/` (which stays as the *seed*, and can be re-seeded by hand) | the run branches `doctrine/<run_id>` in that repo; PM approval merges to its `main`; the deploy tree is never touched |
| **B — in the main repo** | doctrine stays under the app repo's `doctrine/` | the run pushes branch `doctrine/<run_id>` to `origin`; the PM merges it as a normal PR on their git host; core detects the merge commit and records the version bump |

Mode A is recommended: the PM can amend doctrine without a code deploy, a deploy can never
clobber an approved amendment, and `git archive <sha> doctrine/` (SPEC-RUNNER §4.3, the
version-pinned read-only mount) works identically in both. Mode B exists because a PM who
wants GitHub PR review for doctrine should have it.

**DECIDED (PM, 2026-07-18): Mode A.** The standalone repo is **instantiated by
`fund bootstrap`** (SPEC-CORE §6), not by hand: if `settings.doctrine_repo_path` does not
exist, bootstrap runs `git init --bare`, clones the working checkout to
`settings.doctrine_dir`, copies the app repo's `doctrine/` tree in, commits
(`seed doctrine v1 from ai-fund3@<sha>`), and registers the `doctrine_versions` row.
Idempotent: an existing repo is verified, never re-seeded (re-seeding after amendments
would clobber fund state; the repo-tracked `doctrine/` remains the *seed*, nothing more).

---

## 4. The evidence bundle (code gathers it; the model only reasons over it)

`core/distillation/gather.py` produces a read-only `inputs/` directory. **Every item
carries a resolvable id**, which is what makes the citation gate (§6.3) possible.

| File | Contents (since the window start) |
|---|---|
| `inputs/corrections.md` | every `corrections` row: id, run, ticker, correcting house, **attributed house**, target, was → now, source |
| `inputs/misses.md` | every prediction scored `miss`/`expired`: id, house, ticker, kind, value, confidence, realized, signed error, horizon |
| `inputs/lessons.md` | every `lessons.md` entry added across all dossiers (from the dossier repo's git log), with ticker, run, date |
| `inputs/failures.md` | stage failures and their `validation_errors` (from `stage_attempts`): which gate, which role, which house, how often — the pipeline's own failure patterns |
| `inputs/costs.md` | per-house spend, budget-cap gates hit, attempts with `cost_source='estimated'`, metered-vs-provider reconciliation drift |
| `inputs/trackrec.md` | per-house calibration, hit rates, corrections-received trends — rendered by `core/trackrec/render.py` (SPEC-TRACKREC §7), with `n` on every number |
| `inputs/rejected_amendments.md` | previously rejected proposals + the PM's notes (§2) |
| `inputs/doctrine_stats.md` | current doctrine size per file, lesson count, and each lesson's age + last-cited date (feeds pruning) |

The bundle is **facts from the database and from git** — the model cannot influence what it
is shown, and it cannot see a stock's dossier at all (the firewall, §6.2).

---

## 5. The run

| seq | Role | House | Substrate | Workspace |
|---|---|---|---|---|
| 1 | `distiller` | the **meta** house (`meta: true, assignable: false` — Claude) | harness | a worktree of the **doctrine** repo at branch `doctrine/<run_id>`, mounted rw, plus `inputs/` mounted **ro** |

Sandbox: the standard harness sandbox (SPEC-RUNNER §5) with the meta house's key only and a
deny-by-default egress allowlist (its provider endpoint; **no market-data, no dossier
mount**). Wall-clock and budget kills as usual; envelope $5–15
(`fund.yaml runs.distillation.budget_cap_usd`).

### 5.1 Required outputs

Per `doctrine/roles/distiller.md` (the role prompt already in the repo):

1. **Doctrine amendments** on the branch — primarily `doctrine/lessons/global.md`; rarely a
   role or section prompt. Each proposed rule must cite ≥1 concrete incident it would have
   prevented, be preventive and checkable, and generalize without restricting sound judgment.
2. **Pruning**: existing lessons that are obsolete, duplicated, or contradicted, flagged
   explicitly (doctrine that grows without pruning stops being read).
3. **System-health memo** — `system-health-memo.md` in the workspace (an **artifact**, not
   doctrine content): per-house calibration and corrections trends, pipeline failure
   patterns, cost anomalies, and anything in the system's design the evidence says is not
   working. Candid; the PM reads it directly.
4. `stage_result.yaml`:

```yaml
status: completed
changes: ["branch doctrine/9f1a…", "doctrine/lessons/global.md (+6/-2)"]
amendments:
  - id: a1
    file: doctrine/lessons/global.md
    rule: "Never quote a peer price from a comps table older than the report date; re-pull per ticker."
    cites: ["correction:7c2e…", "correction:9a04…", "run:3f2c…"]   # MUST resolve in the DB
    prevents: "3 comps-price corrections across 2 runs in July"
prune:
  - id: p1
    file: doctrine/lessons/global.md
    line_quote: "…obsolete lesson text…"
    reason: "superseded by a1"
memo_summary: "one paragraph"
```

---

## 6. Gates (deterministic, distillation-specific)

All of SPEC-RUNNER §6's generic gates apply (schema-valid `stage_result`, redaction,
bounded retry with named omissions), plus:

### 6.1 Diff-scope gate

The branch's diff must touch **only** paths under `doctrine/`. Any change to `dossiers/`,
code, config, or tests → **stage fails**. (In Mode B, the diff is additionally forbidden to
touch anything outside `doctrine/` in the app repo.)

### 6.2 Meta firewall (the structural one)

- The `distiller` role may only run on a house with `meta: true, assignable: false`; the
  planner refuses any other house, and a `meta` house is never selectable as a lead or a
  contributor (SPEC-DOMAIN invariant 4).
- A distillation run **writes no `predictions` and no `corrections` rows** — the
  registration path is not even called for this run type; a `stage_result` containing a
  `predictions` block is a **gate failure**, not a silently-ignored field.
- The distiller's sandbox has **no dossier mount** and no market-data access. It cannot
  analyze a stock even if instructed to. If the evidence suggests a stock-level problem, it
  says so *in the memo* and leaves the analysis to the analyst houses.

### 6.3 Citation gate (the anti-fabrication backstop)

**Every `amendments[].cites` entry must resolve to a real row** — `correction:<id>`,
`run:<id>`, `prediction:<id>`, or `stage:<id>` — that exists **and falls inside the evidence
window**. An unresolvable, out-of-window, or invented citation fails the stage with the
offending id named. A rule that cannot point at an incident that actually happened does not
enter doctrine. (This is the single most important gate in the spec: doctrine is what every
future run obeys, so a hallucinated justification would propagate into every report the fund
ever writes.)

### 6.4 Volume gates

- `amendments` ≤ `fund.yaml distillation.max_amendments` (default 8) and the diff ≤
  `max_diff_lines` (default 300). Exceeding either fails with the cap named — forcing
  prioritization, which is the whole point of a distillation.
- `prune` entries must quote a line that **exists** in the current file (else fail).

### 6.5 Doctrine-integrity gate

After applying the branch: every required doctrine file still exists (`core.md`,
`roles/*.md` for all 8 roles, `lessons/global.md`, `report/full-report.md`,
`templates/`), all 14 section prompts are still present, and every file still parses. A
distillation that deletes a section prompt or empties a role fails. **`verifier.md` is
protected**: any diff touching it requires `stage_result.amendments[].file` to name it
explicitly *and* the PM gate payload flags it in bold (the PM's proven multi-pass formula is
the highest-leverage text in the system; it may be amended, but never quietly).

---

## 7. PM approval and the version bump

The run ends `waiting_pm` with a `doctrine_amendment` gate.

**Payload:** branch name; the unified diff (artifact + an inline preview capped at
`gate.max_diff_preview_lines`); each amendment with its **resolved** citations rendered as
links (run/ticker/correction); the prune list; the memo summary (full memo as an artifact);
cost; and a **bold flag** if any role/section prompt (especially `verifier.md`) is touched.
`allowed_answers: ["merge", "reject"]`.

- **`merge`** (one transaction): merge `doctrine/<run_id>` → doctrine `main`; insert a
  `doctrine_versions` row (`commit_sha` = the merge commit, `label` = the next `v<N>`,
  `approved_by`, `approved_at`, `distillation_run_id`, `is_current=true`) and clear the
  previous `is_current` (the partial unique index guarantees exactly one current version);
  run `succeeded`; outbox → desk: *"doctrine v3 in effect — 4 lessons added, 2 pruned; memo
  attached."*
- **`reject`**: the branch is **retained** (audit trail; the evidence stays in the next
  window), no version bump, the PM's notes are stored and fed back into the next run's
  `inputs/rejected_amendments.md`.
- **Partial approval** is not a gate answer in v1. The branch is an ordinary git branch: a
  PM who wants only some amendments edits the branch (or the PR, in Mode B) and merges it —
  core records whatever commit actually landed. Do not build a partial-apply mechanism
  before the PM asks for one.

**Apply:** every run pins `runs.doctrine_version_id` at creation (SPEC-CORE §3.1) and the
runner mounts that exact commit read-only (SPEC-RUNNER §4.3). The next run after a merge
therefore loads the new doctrine automatically, and every report remains reproducible under
the rules it was written with.

---

## 8. Test plan (TDD — for the deferred BUILD session)

`tests/unit/distillation/`. Fakes: fake harness binary (SPEC-RUNNER §10.1) playing the
distiller, a temp doctrine git repo, SQLite with a fixture ledger, `FakeClock`. No network.

### 8.1 Evidence bundle

- The window is computed from the **current doctrine version's `approved_at`**, not the last
  run: a rejected distillation does not shrink the next window.
- `inputs/*.md` contain exactly the rows in-window (boundary rows tested), each with a
  resolvable id; a correction attributed to `NULL` appears but is marked unattributed.
- `inputs/rejected_amendments.md` carries the PM's prior rejection notes.
- The bundle is generated with **zero** LLM calls (`FakeLLM.calls == 0`) and zero
  market-data calls.

### 8.2 Gates

- **Diff-scope**: a fake distiller that edits `dossiers/tse_2267/dossier.md` → stage
  **fails**; the dossier repo is unchanged.
- **Meta firewall**: a `stage_result` with a `predictions` block → fails; the planner refuses
  to schedule a `distiller` stage on an assignable house; a `meta` house cannot be planned as
  a lead/contributor/verifier anywhere.
- **Citation gate**: an amendment citing `correction:deadbeef` (nonexistent) → fails, naming
  the id; citing a real correction **outside** the window → fails; citing a real in-window
  correction → passes.
- **Volume**: 9 amendments with `max_amendments=8` → fails with the cap named; a 400-line
  diff → fails; a `prune` quoting a line that is not in the file → fails.
- **Integrity**: a diff deleting `doctrine/sections/comps.md` → fails; a diff touching
  `doctrine/roles/verifier.md` **passes the gate** but the gate payload carries the bold flag
  (asserted).
- Redaction: a memo containing a key-shaped string is redacted before the artifact is stored.

### 8.3 Approval flow

- `merge` → branch merged; a new `doctrine_versions` row is current; the previous is not;
  exactly one `is_current` (integration test asserts the partial unique index); desk outbox
  event names the added/pruned counts.
- A run created **after** the merge pins the **new** `doctrine_version_id`; a run created
  before it keeps the old one (and its stage mount is the old commit — reproducibility).
- `reject` → no version bump, branch retained, notes stored and surfaced in the next run's
  bundle.
- Gate answering is idempotent (a replayed `merge` does not merge twice or create two
  versions).
- Monthly trigger idempotency: two scheduler fires in one month create one run.

---

## 9. Preliminary — pending decisions and gates

1. **Doctrine repo layout (§3) must be chosen by the operator before BUILD.** Mode A
   (standalone repo outside the deploy tree) is recommended and is what the rest of the spec
   assumes.
2. **BUILD is deferred.** PROMPTS marks 7.1 SPEC+BUILD; this session produced the spec only.
   The BUILD session should treat §6 (gates) as the acceptance criteria — the value of
   distillation is entirely in whether a bad amendment can reach doctrine.
3. **Monthly cadence is a guess.** With ~1–2 initiations/week, a month may yield too few
   incidents to cluster meaningfully. If the first two runs produce thin amendments, move to
   quarterly (or trigger on volume: "≥ N corrections since the last version").
4. **Amendment caps (8 / 300 lines)** are guesses. Watch whether the distiller spends them on
   trivia; if so, the fix is the role prompt, not the cap.
5. **Gates 2.4/3.3 come first.** Their fixes are recorded as dated `NOTES.md` entries and are
   "the first doctrine lessons" — the distiller's first real input. If the PM ends up hand-
   writing those lessons (likely, and correct), the first distillation's job is mostly
   *pruning and generalizing* them, not inventing new ones.
6. **No auto-apply, ever.** If a future session proposes letting a high-confidence amendment
   merge itself, the answer is no: principle 9 ("Doctrine learns" → *the PM approves*) is
   what keeps a model from rewriting the rules it is judged by.

---

## 10. Spec Authoring Checklist

- **Side-effect cost.** One harness stage per month on the meta house, envelope **$5–15**
  (`runs.distillation.budget_cap_usd`, enforced by the same per-run cap and budget kill as
  every other run). The evidence bundle is gathered by **code** — zero LLM calls, zero
  market-data calls, a handful of indexed DB queries and one `git log` over the dossier repo.
  The memo and the diff are artifacts (bytes on disk). No embedding, search, or news spend.
  Retries re-spend the stage, so `max_attempts` is 2. Net: the cheapest run type per unit of
  leverage in the system — and the only one whose output changes what **every** future run
  does, which is why its gates are the strictest.
- **Concurrency model.** No new in-process state. The run is a normal queued run
  (`coverage_id = NULL`, so it takes **no** coverage lock and cannot block ticker work); its
  workspace is a private worktree on `doctrine/<run_id>`, so a distillation cannot collide
  with anything else. The scheduler is single-leader (advisory lock) and the run is deduped by
  `trigger_ref = distillation:{yyyy}-{mm}`. The version bump is the one genuinely contended
  write: the merge, the `doctrine_versions` insert, and the clearing of the previous
  `is_current` happen in **one transaction**, protected by the partial unique index
  (`ux_doctrine_current`), so two concurrent approvals cannot produce two current versions.
  The gate itself is answer-once (`pm_gates.idempotency_key`). Runs pin their doctrine version
  at creation, so a merge landing mid-flight cannot change the rules a running stage is being
  judged under.
- **LLM-as-filter threat model.** This is the highest-*leverage* LLM output in the system: it
  edits the rules every other model obeys. It is also the **only** place a model proposes a
  change to its own governing constraints, so the backstops are structural rather than
  advisory: (1) **the PM approves every amendment** — nothing self-amends, and there is no
  auto-apply path to add later (§9.6); (2) **the citation gate** (§6.3) makes every proposed
  rule point at a **row that exists in our database, inside the window** — a fabricated
  incident fails the stage, so the model cannot invent a crisis to justify a rule; (3) **the
  diff-scope gate** confines it to `doctrine/` — it cannot touch a dossier, a prediction, or
  code, and its sandbox has no dossier mount, so it *cannot* analyze a stock even if
  prompt-injected; (4) **volume caps** bound how much doctrine can change at once, and the
  **integrity gate** stops deletion of a role or section prompt; (5) **`verifier.md` is
  flagged in bold** on any touch — the PM's proven multi-pass formula cannot be weakened
  quietly; (6) the evidence bundle is **code-generated**, so the model cannot choose or
  fabricate its own inputs; (7) outputs are redacted before storage and rendered as text, never
  executed. Residual risk, stated plainly: a *plausible but bad rule*, well-cited, that the PM
  merges. Nothing in code can prevent that — which is why the diff is small, human-readable,
  reversible (it is a git commit on a branch), and why pruning is part of the role.
- **Identity-key ownership.** This spec mints `runs.trigger_ref = distillation:{yyyy}-{mm}`
  (unique — one distillation per month), the branch name `doctrine/<run_id>` (from our run
  id), and the doctrine **version label** `v<N>` (monotonic, code-assigned — a model never
  names a version). `doctrine_versions.commit_sha` is git's key, and the invariant "exactly
  one `is_current`" is enforced by a partial unique index (SPEC-DOMAIN §4.18), not by
  application logic. The citations in `stage_result.amendments[].cites` are the model's
  *claims* about **our** keys (`correction:<id>`, `run:<id>`, `prediction:<id>`) — they are
  **validated against the DB**, never trusted, and never used to create anything; they are the
  clearest case in the system of "borrowed keys are claims until code resolves them." Amendment
  ids (`a1`, `p1`) are per-run handles only and are never persisted as identities.
- **Test isolation.** Everything is testable without a provider, a network, or Postgres: the
  fake harness binary plays the distiller against a **temp doctrine git repo** (local git —
  and the runner is the only git caller, so this stays honest), the evidence bundle is built
  from a SQLite fixture ledger, and the gates are pure functions over a diff and a
  `stage_result`. `FakeLLM.calls == 0` is asserted across the gather path (the bundle must
  never quietly become a summarization call). The autouse socket guard catches the miss this
  spec invites — a gather step reaching for a git *remote* (in Mode B, the push to `origin`
  is the one network call, and it is behind an injected git client that unit tests fake and
  that `@pytest.mark.integration` exercises for real). The real meta-house run is
  integration-only and costs money.
