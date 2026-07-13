# SPEC-RUNNER — Runner, harness drivers, sandbox, stage gates

**Phase:** 2.1 (SPEC) · **Implements:** PROMPTS.md 2.1 · **Branch:** `spec-runner`
**Status:** ready for BUILD
**Depends on:** SPEC-DOMAIN (queue, stage/attempt rows), SPEC-CORE (config, budgets,
orchestrator contract)
**Consumed by:** SPEC-INITIATION (uses these gates and drivers), SPEC-MONITORING (api
substrate), SPEC-TRACKREC, SPEC-DISTILLATION

**This is the risk phase.** Harness ops — sandbox networking, non-interactive CLI
quirks, per-provider cost capture — is the novel surface. It is built before everything
else so that if harness quality or ops disappoint, the design adjusts here cheaply.

Self-contained: implementable without reading `design/`.

---

## 1. Purpose

The runner is the **only** component that executes model work. It claims stages from the
Postgres queue, prepares an isolated workspace, executes the stage on one of two
substrates, validates the output against deterministic gates, captures transcript and
cost, commits the dossier change, and completes or fails the stage.

```
 claim_stage (SKIP LOCKED)
   → prepare workspace (git worktree branch run/<id>, task.md, doctrine ro-snapshot)
   → execute:  substrate=harness → sandboxed CLI session (per-house driver)
               substrate=api     → in-process bounded tool loop
   → capture:  transcript (redacted), usage/cost, artifacts
   → validate: stage gates (schema, artifacts, reconciliation, price pin, no-fabrication)
   → commit:   dossier changes on the run branch, artifacts content-addressed
   → complete_stage | fail_stage(retryable) with named omissions
```

Everything above the dashed line is code. **No LLM decides whether a stage passed.**

### Process

`runner/main.py` runs `N` workers (config `runner.workers`, default 2; harness stages
are long and mostly idle, so N is small). Each worker is a loop with its own DB session
and a heartbeat thread. `SIGTERM` → stop claiming, finish or kill in-flight attempts
(`kill_reason='operator'`), release leases, exit (ported `infra/shutdown.py`).

---

## 2. Stage execution context

The orchestrator has already created the stage row. The runner builds:

```python
@dataclass(frozen=True)
class StageContext:
    stage_id: UUID; run_id: UUID; seq: int; attempt_no: int
    run_type: RunType; role: StageRole; substrate: Substrate
    house: str                      # registry key
    model: str                      # RESOLVED from houses.yaml (heavy|light per role) — never hardcoded
    coverage: CoverageView | None   # slug, ticker, exchange, name, currency, state, lead, levels
    workspace: Path                 # host path, bind-mounted rw into the sandbox
    doctrine_dir: Path              # host path of the pinned doctrine snapshot, mounted ro
    task_md: Path                   # generated (§4.2)
    budget_usd: Decimal             # min(remaining run cap, house remaining daily cap)
    deadline: datetime              # now + stage_timeout_s
    prior_results: list[StageResult]  # earlier stages of this run (for the retry prompt / context)
    validation_errors: list[str]    # non-empty on a retry attempt → named omissions (§6.4)
```

`model` resolution: `role in (author, verifier, finalizer, cross_check, distiller)` →
house `models.heavy`; `role in (monitor, pm_query, data_checker)` → `models.light`. The
mapping lives in `fund.yaml` (`roles.<role>.model_tier`), not in code.

---

## 3. HarnessDriver interface

Harness heterogeneity is contained in a small driver per house. A driver is ~a page of
code plus config; a new house = new driver + registry entry, **no core changes**.

```python
class HarnessDriver(ABC):
    key: ClassVar[str]                       # "codex-cli" | "gemini-cli" | "generic-cli" | …

    @abstractmethod
    def launch_spec(self, ctx: StageContext, secrets: SecretEnv) -> LaunchSpec:
        """argv + env + cwd + stdin for a NON-INTERACTIVE session. Never puts a secret on argv."""

    @abstractmethod
    def parse_usage(self, transcript: Path, exit_code: int) -> UsageReport | None:
        """Tokens/cost from the CLI's own output. None if the CLI reports nothing."""

    def usage_api(self, ctx: StageContext, window: tuple[datetime, datetime]) -> UsageReport | None:
        """Provider usage API, where the provider has one. Default: None."""

    def health_check(self) -> HealthResult:
        """Binary present, version, auth valid. Reports presence only — never a key value."""

    # provided by the base class, not overridden:
    def launch(self, ctx, sandbox) -> HarnessSession       # sandbox.run(launch_spec)
    def monitor(self, session) -> HarnessStatus            # running | exited(+incremental usage)
    def collect(self, session) -> HarnessResult            # exit_code, transcript, usage
    def kill(self, session, reason: KillReason) -> None    # SIGTERM → grace → SIGKILL; container rm
```

```python
@dataclass(frozen=True)
class LaunchSpec:
    argv: list[str]
    env: dict[str, str]              # NON-secret env only
    secret_env_names: list[str]      # names whose values the sandbox injects from a tmpfs env-file
    cwd: str                         # path INSIDE the sandbox (/workspace)
    stdin: str | None                # prompt text if the CLI reads stdin
    prompt_file: str | None          # or a file path inside the sandbox (preferred: /workspace/task.md)

@dataclass(frozen=True)
class UsageReport:
    input_tokens: int | None; output_tokens: int | None; cached_input_tokens: int | None
    model: str | None                # OBSERVED model id, as reported by the CLI/provider
    cost_usd: Decimal | None         # only if the provider reports money directly
    source: Literal["provider_api", "transcript", "estimated"]
```

### 3.1 Shipped drivers

| Driver key | House (typical) | Invocation shape | Usage source |
|---|---|---|---|
| `codex-cli` | GPT | `codex exec --cd /workspace <prompt-file>` (non-interactive) | transcript token lines; OpenAI usage API for reconciliation |
| `gemini-cli` | Gemini | `gemini -p @/workspace/task.md` | transcript; else estimated |
| `generic-cli` | anything (DeepSeek/GLM/Qwen via opencode, qwen-code, …) | argv **template** from `houses.yaml` (`{workspace}`, `{task_file}`, `{model}` placeholders) | transcript regex from config; else estimated |
| `claude-code` | Claude (meta only) | `claude -p @/workspace/task.md` | transcript; Anthropic usage API |

**The exact CLI flags are config, not code.** Every driver reads its argv template and
its usage-parsing regexes from the house's `harness.config` block, with the table above
as the shipped default. This is what lets the operator pick the first house at the 2.4
gate (whichever of Codex CLI / Gemini CLI authenticates fastest on the host) **without a
code change** — set `fund.yaml first_house` and go. `generic-cli` exists so a house whose
CLI nobody has written a driver for still runs.

Registry: `runner/drivers/__init__.py` maps `harness.type` → class. An unknown type is a
`checkconfig` failure (SPEC-CORE §2.2), never a runtime surprise.

---

## 4. Workspace lifecycle

### 4.1 Dossier worktree (the runner owns **all** git operations)

The dossier repo is a bare repo (`settings.dossier_repo_path`) with a working clone.
Per stage:

1. First stage of a run: `git worktree add --no-checkout <run_dir> -b run/<run_id> <base>`
   where `<base>` = the coverage's current `main` commit (recorded as
   `runs.dossier_commit_before`).
2. `git sparse-checkout set --cone dossiers/<slug>` → only this ticker's directory
   materializes. Cross-ticker leakage into a sandbox is thereby impossible.
3. The **sandbox is bind-mounted at the ticker directory**, not at the repo root, and it
   is a plain directory with **no `.git`**. The agent edits files; it cannot rewrite
   history, cannot see other tickers, cannot push.
4. After the stage exits, the runner (on the host) stages and commits **inside the
   worktree**:
   `run:<run_id> stage:<seq> <role>@<house>` → a `dossier_commits` row.
   No changes and `status != nothing_material` → gate failure (§6.1, rule 7).
5. Subsequent stages of the same run reuse the same worktree/branch (each stage builds on
   the previous stage's commit — that is the entire point of verify passes).
6. Run end: `succeeded` + PM accepts → merge `run/<id>` into `main` (fast-forward or a
   merge commit) → `runs.dossier_commit_after`, `dossier_index.main_commit` updated.
   Rejected/failed/cancelled → branch **retained**, never merged (an audit trail of what
   was proposed). Worktree pruned (`git worktree remove`), branch kept.

New ticker (first initiation): the runner creates `dossiers/<slug>/` on the branch with
the contract's empty scaffolding (SPEC-INITIATION §3 defines the files).

### 4.2 `task.md` generation

Assembled by `runner/task.py` in this order (design/04 §5 — personas removed):

1. **Role prompt** — `doctrine/roles/<role>.md`, verbatim from the pinned snapshot.
2. **Doctrine core + sections + global lessons** — `core.md`, the section prompts the run
   type needs (all 14 for `initiation`), `lessons/global.md`. For harness stages these
   are *mounted* (§4.3) and `task.md` references them by path rather than inlining
   (context economy); for api stages the digest is inlined.
3. **Coverage context** — ticker, exchange, name, trading currency, state (active/watch),
   PM levels, and **position context if held** (size, book, average cost, P&L) read from
   the deterministic ledger — never from an LLM.
4. **Dossier** — for harness stages, the workspace *is* the dossier (path given). For api
   stages, an inlined digest (dossier.md, valuation.md, tripwires.yaml).
5. **Stock-specific lessons** — `dossiers/<slug>/lessons.md`.
6. **Task** — trigger context (the tripwire that fired, the PM's question, the event), the
   required outputs for this role, the `stage_result.yaml` schema, and the hard deadline
   and budget.
7. **On a retry only** — the *named omissions* block (§6.4).

`task.md` is written into the workspace and **stored as an artifact** (`kind='task'`), so
"what exactly were we asked?" is answerable years later.

### 4.3 Doctrine snapshot (read-only, version-pinned)

`git archive <doctrine_version.commit_sha> doctrine/ | tar -x -C <tmp>` → a snapshot dir
mounted **read-only** at `/doctrine`. The stage cannot modify doctrine, and the exact
rules it ran under are reproducible from `runs.doctrine_version_id`.

### 4.4 Layout inside the sandbox

```
/workspace          rw   (the ticker's dossier dir on branch run/<id>; contains task.md)
/doctrine           ro   (pinned snapshot: core.md, sections/, roles/, lessons/, report/, templates/)
/tmp                rw   (tmpfs, size-capped)
everything else     absent
```

---

## 5. Sandbox

```python
class Sandbox(ABC):
    def run(self, spec: LaunchSpec, *, mounts: Mounts, net: NetPolicy,
            limits: Limits, secrets: SecretEnv) -> SandboxSession
    def poll(self, session) -> SandboxStatus
    def kill(self, session, reason) -> None
    def cleanup(self, session) -> None
```

Two implementations (`runner/sandbox/{container,bwrap,fake}.py`), selected by
`fund.yaml sandbox.type`:

### 5.1 `container` (default; rootless podman or docker)

- Image: `ai-fund-harness:<tag>`, built by `deploy/harness-image/Dockerfile`, containing
  the harness CLIs and nothing else of ours. Built and pinned by the operator; the image
  tag is config.
- `--network ai-fund-sbx` (an internal network with **no default route**), `--dns none`,
  `--read-only` root, `--tmpfs /tmp:size=…`, `--memory`, `--pids-limit`, `--cpus`,
  `--cap-drop ALL`, `--security-opt no-new-privileges`, non-root UID.
- Mounts: `/workspace` rw, `/doctrine` ro.
- **Egress allowlist via a forward proxy.** The sandbox's only reachable endpoint is the
  runner-managed egress proxy (`runner/egress/proxy.py` or a pinned squid/tinyproxy
  container). `HTTPS_PROXY`/`HTTP_PROXY` are set in the container env; the network has no
  other route. The proxy allows `CONNECT` only to:
  1. the **provider endpoints of this house** (from `houses.yaml`, resolved hostnames),
  2. the **market-data service** (localhost service, reachable from the sandbox network),
  3. the **search endpoint** (`settings.search_endpoint`),
  and denies everything else. **Every denial is logged and counted**; a denial burst
  raises a desk alert (it means either a misconfigured house or an agent trying to reach
  somewhere it should not).
- **Per-house key injection**: only that house's key, written to a `0600` file on a
  tmpfs and passed as `--env-file` (never on argv — `ps` is world-readable; never baked
  into the image; never in a log). Stage A of house X cannot see house Y's key.

### 5.2 `bwrap` (fallback for hosts without a container runtime)

`bwrap --unshare-all --share-net --die-with-parent --ro-bind /doctrine … --bind
/workspace … --tmpfs /tmp --clearenv` with the same proxy env vars, plus a network
namespace whose only route is the proxy (`slirp4netns` with an allowlist, or an
iptables OUTPUT rule set owned by the runner's UID). **This is the weaker mode**: if the
host cannot enforce the egress allowlist, `checkconfig --strict` FAILS unless
`sandbox.allow_unrestricted_egress: true` is set explicitly by the operator (an audited,
loud opt-out — a confused agent with an unrestricted network and an API key is exactly
the exfiltration path the sandbox exists to prevent).

### 5.3 Limits and kills

- **Wall clock**: `limits.timeout_s = stage_timeout_s` (config per run type). On expiry →
  `kill(reason='wall_clock')`, attempt closed `status='timeout'`.
- **Budget**: the runner polls `driver.monitor()` (cheap: reads the transcript tail /
  container stats) every `runner.poll_seconds`. Incremental usage → priced (§7) → if
  `attempt_cost + run_cost_so_far > ctx.budget_usd` → `kill(reason='budget')`. The run
  then pauses at a `budget_cap` PM gate (SPEC-CORE §3.5); it never silently continues on
  a cheaper model or a shorter analysis.
- **Killed attempts still record their cost** (`stage_attempts.cost_usd`), because they
  spent it.

---

## 6. Stage gates (deterministic; the quality backstop)

`runner/gates/` — each gate is a pure function `(ctx, workspace) -> list[GateError]`.
A stage passes only if **every** applicable gate returns empty. Gates never repair,
never synthesize, never ask a model anything.

### 6.1 Gate list

1. **`stage_result.yaml` present and schema-valid.** A stage that "answers in prose"
   fails here. Pydantic model (`runner/schemas.py`):

```yaml
status: completed            # completed | blocked | nothing_material
changes: ["report-content.md: rebuilt comps table", ...]        # one line each
corrections:                 # verify / data_checker / finalizer roles
  - target: "report-content.md#comps"
    was: "Meiji price ¥4,027"
    now: "¥3,763 (close 2026-06-09, source: TSE via market-data)"
    attributed_stage: 1      # a SEQ WITHIN THIS RUN (resolved by code; never a raw id)
    source: "https://…"
predictions:                 # author / verifier / finalizer
  - kind: target_price       # target_price | entry_point | scenario | event_forecast | stance
    value: 2900
    currency: JPY
    horizon_date: 2027-06-30
    confidence: 0.65         # 0..1 — STATED BY THE MODEL; absent means absent
    scenario_label: base     # scenario kind only
    scenario_prob: 0.55
    rationale_ref: "report-content.md#valuation"
pinned_price:                # report-producing roles
  value: 2745
  currency: JPY
  as_of: 2026-06-09          # trading date
  source: "market-data:tse"
recommendation: buy          # buy | accumulate | hold | reduce | sell | avoid (author/finalizer)
escalate: { reason: "..." }              # optional
disagreement:                            # cross_check only
  stance_vs_lead: "..."
  material: true
  key_numbers: { my_tp: 2400, lead_tp: 2900 }
  resolves_if: "FY27 China JV margin disclosure"
lead_change_proposal:                    # lead_review only
  proposed_house: gemini                 # or "keep"
  rationale: "..."
notes: "..."                             # free text, redacted, length-capped
```

Unknown top-level keys are rejected (a typo must not silently drop a prediction).
`status: blocked` is a legitimate, *passing* outcome for a stage that hit an unresolvable
problem (e.g. the finalizer finds the workbook itself wrong — doctrine says **stop**, do
not patch around it); it does not retry, it surfaces to the PM.

2. **Required artifacts by role** (table in `fund.yaml roles.<role>.required_artifacts`,
   defaults below). Missing file → fail.

| Role | Required in the workspace |
|---|---|
| `author` (initiation) | `report-content.md`, `workbook.xlsx`, `build_workbook.py`, `references.md`, `dossier.md`, `valuation.md`, `tripwires.yaml`, `stage_result.yaml` |
| `verifier` | all of the author's set (still present) + `corrections.md` (appended) |
| `data_checker` | `corrections.md`, `stage_result.yaml` |
| `finalizer` | the above + `reconciliation.yaml`, rendered `*.html` and `*.pdf` named `NAME_SYM_EXCH-YYYYMMDD`, `pm-summary.md` |
| `author` (event_analysis) | `events/<date>-<slug>.md`, `stage_result.yaml` (+ dossier edits or `nothing_material`) |
| `monitor` / `pm_query` / `lead_review` / `distiller` | `stage_result.yaml` (+ role-specific: distiller → branch + memo) |

3. **Report↔workbook reconciliation** (`runner/gates/reconcile.py` — a script, not a
   model; the port of the Yakult discipline):
   - `reconciliation.yaml` (produced by the stage) maps each headline figure →
     `{report_value, sheet, cell}`: rating, target, upside, every multiple, every
     financial-summary line.
   - The workbook is **recalculated** (`soffice --headless --convert-to xlsx`) and the
     named cells read with `openpyxl(data_only=True)`.
   - Compare: numeric within `reconcile.tolerance_pct` (default 0.5%), strings exact.
     Any mismatch → fail, naming the cell.
   - **One-computed-layer check**: every cell named in `reconciliation.yaml` must contain
     a *formula* (read with `data_only=False`, starts with `=`), not a typed number.
     A typed summary cell is a doctrine violation ("that is a bug") and fails.
   - If LibreOffice is unavailable, the recalc step degrades to formula-presence checking
     and the gate emits a **WARN** recorded on the attempt — but `checkconfig --strict`
     requires `soffice` on the host, so this degradation should never happen in prod.

4. **Price-pin check** (`runner/gates/price_pin.py`): `stage_result.pinned_price` is
   compared against the **market-data service** for that ticker and trading date.
   Mismatch beyond tolerance, or an `as_of` older than `reconcile.max_price_age_days`
   → fail. This is the deterministic backstop for the doctrine's single-pinned-price rule:
   a hallucinated or stale price cannot survive the gate, because the gate asks the price
   service, not the model.

5. **No-invented-confidence / no-synthesized-recommendation**: for roles that must state
   them (`author`, `finalizer`), a missing `recommendation` or `confidence` **fails the
   stage with the omission named** — code never fills in a default (v2's fabricated
   "Neutral/50%" footers). Present-but-out-of-range fails too.

6. **Placeholder / unverified scan** (`runner/gates/placeholders.py`): the report and
   workbook are scanned for `TBD`, `TODO`, `XXX`, `<insert`, `lorem`, `PLACEHOLDER`,
   `N/A` in a numeric column, and for empty required cells. Hit → fail. The literal
   string `unverified` is **allowed** (doctrine mandates it as the honest marker).

7. **References gate**: `references.md` exists, has ≥ `reconcile.min_references` entries,
   and **every** entry carries a URL and an ISO retrieval date. (URL liveness is *not*
   checked in the gate — a flaky network must not fail a good report. An optional
   `@pytest.mark.integration` link-checker and a nightly job report dead links instead.)

8. **Dossier-change contract**: a concluding stage must leave a dossier change **or**
   declare `status: nothing_material`. Silence is a failure (design/02 §2).

### 6.2 Gate outcomes

`GateError(code, message, path?)` — e.g.
`("missing_artifact", "workbook.xlsx not found", None)`,
`("reconcile_mismatch", "report EV/EBITDA 11.2 vs workbook Summary!D12 = 10.4", "workbook.xlsx")`,
`("price_pin_mismatch", "pinned ¥2,745 as of 2026-06-09; market-data says ¥2,712", None)`,
`("missing_confidence", "author stage must state confidence for each prediction", None)`.

### 6.3 Cross-house integrity

Fields that decide *who is accountable* are never taken from model output:
`stage_attempts.house/model`, `predictions.house`, `corrections.correcting_house` come
from the stage row the orchestrator created. `corrections[].attributed_stage` is a **seq
within this run**, resolved by code to a stage id; an unresolvable seq is dropped to NULL
with a warning (SPEC-DOMAIN §4.14). A model cannot smear another house's record, and
cannot claim another house's work.

### 6.4 Bounded retry with named omissions

On a gate failure with `attempts < max_attempts`, the stage is requeued
(`available_at = now + backoff`) and the **next attempt's `task.md` carries exactly what
failed**:

```md
## Your previous attempt was rejected by the stage gates

Fix exactly these, then re-emit the required outputs. Do not restyle or re-litigate
anything else:

- `missing_artifact`: build_workbook.py was not produced.
- `reconcile_mismatch`: report EV/EBITDA 11.2 vs workbook Summary!D12 = 10.4.
- `missing_confidence`: prediction 1 (target_price) has no confidence.
```

Retries are **never silent repairs**: the model fixes its own output, or the stage fails
visibly to the desk after `max_attempts` (SPEC-CORE §3.3). `validation_errors` is stored
on every attempt row, which is what makes "which house needs how many attempts" a
measurable, PM-visible number from day one.

---

## 7. Transcript and cost capture

### 7.1 Transcript

The full session (stdout+stderr, and the CLI's own session log where it writes one) is
captured to a file, passed through the **secret redactor** (`core/infra/logging.redact`,
ported: known secret values from settings + `sk-`/`AIza`/bearer-shaped patterns), then
stored as a content-addressed artifact (`kind='transcript'`) and referenced from the
attempt row. **Redaction happens before hashing and before any log line is emitted**
(AGENTS.md Rule 2).

### 7.2 Cost, per provider, in three tiers

1. **Provider usage API** where one exists (`driver.usage_api`) — authoritative;
   `cost_source='provider_api'`.
2. **Transcript parse** (`driver.parse_usage`) — most CLIs print a token/usage summary;
   the regexes are config, not code. `cost_source='transcript'`.
3. **Estimate** — token counts from the prompt/response text via the provider's
   tokenizer (or a 4-chars≈1-token fallback). `cost_source='estimated'` and the attempt is
   **flagged**: an estimated cost is a data-quality bug to fix in the driver, not a
   normal state. The desk gets a daily count of estimated-cost attempts.

Money = `pricing.yaml` lookup of the **observed** model id × tokens (SPEC-CORE §2.4). An
unpriced model → `cost_usd = NULL`, `cost_source='estimated'`, desk alert — never a
silent zero (a silent zero would defeat every cap).

Writes per attempt: one `llm_usage` row (`purpose='stage'`, run/stage/attempt/house refs)
and `stage_attempts.cost_usd`; the orchestrator rolls up to stage and run (SPEC-DOMAIN
§4.9). The nightly `cost_rollup` job reconciles metered vs provider-reported spend and
alerts on drift (SPEC-CORE §4).

---

## 8. API substrate (the slim tool loop)

`runner/api_substrate/loop.py` — a **bounded** JSON tool loop (mined from v2's
`agents/analyst_agent.py` pattern, without personas or chat coupling). Used by
`monitor_tick`, `pm_query`, and (optionally) `lead_review` and low-severity
`event_analysis`.

- Provider clients are built from `houses.yaml` (`provider`, `base_url_env`,
  `api_key_env`) — an OpenAI-compatible client covers OpenAI/DeepSeek/GLM/Qwen; Google
  and Anthropic have their own. Model id comes from the house's `light`/`heavy` slot.
- **Stable, small tool set** (no dynamic tools): `market_data.get_price(ticker)`,
  `market_data.get_history(ticker, range)`, `market_data.get_fx(pair, date)`,
  `dossier.read(path)` (read-only, this ticker only). **No workspace writes and no
  network fetch tools** on this substrate: the api loop's job is to reason over supplied
  context, and everything it can reach is a service *we* control.
- Bounded: `max_iterations` (config), `max_tokens`, hard deadline, and the same budget
  kill as harness stages.
- Structured output: the response is parsed into the same `stage_result` schema and put
  through the same gates (the applicable subset). A schema failure retries once with the
  parse error named, then fails the stage.

---

## 9. Health and first-house selection (PM gate 2.4)

- `fund harness check [house]` → runs each driver's `health_check()` inside the sandbox:
  binary present + version, auth valid, provider endpoint reachable through the egress
  proxy, a 5-token smoke call. Reports `OK/FAIL` per house; **prints no key material**.
- `fund harness smoke <house> --ticker <slug>` → runs a single throwaway `monitor`-role
  stage end to end (workspace, sandbox, gates, cost capture) with a tiny budget. This is
  the operator's "is this house usable?" command and the fastest path through the 2.4
  gate.
- The **first house is chosen by config, not code**: `fund.yaml first_house: <key>`.
  Ship `codex-cli` and `gemini-cli` drivers; the operator authenticates whichever is
  fastest on the host, points `first_house` at it, and runs the pilot. If both
  authenticate, prefer the one whose `harness check` reports a usable non-interactive
  mode and parseable usage output (cost capture is the harder half of harness ops).

---

## 10. Test plan (TDD)

`tests/unit/runner/`, `tests/integration/runner/`. Unit tests use a **fake harness
binary** and a **fake sandbox**; the autouse socket fixture blocks the network, so a
driver that tries to reach a real provider in a unit test fails loudly.

### 10.1 Fake harness binary

`tests/fixtures/fake_harness.py` — a small Python script the fake sandbox executes as a
subprocess (no container, no network). Behavior driven by an env var / a `scenario` file
in the workspace:

| Scenario | What it does | Expected runner behavior |
|---|---|---|
| `ok_author` | writes report/workbook/build script/references/dossier files + valid `stage_result.yaml`; prints a usage line | stage `succeeded`, cost parsed (`cost_source='transcript'`), dossier commit created |
| `prose_only` | prints an essay, writes no `stage_result.yaml` | gate 1 fails → retry with named omission → after `max_attempts`, stage `failed` |
| `bad_schema` | `stage_result.yaml` with an unknown key / a string confidence | gate 1 fails, error names the field |
| `no_confidence` | valid result, `confidence` omitted on the author's target_price | gate 5 fails; **the DB must contain no synthesized confidence** |
| `reconcile_mismatch` | report says EV/EBITDA 11.2, workbook cell computes 10.4 | gate 3 fails, error names the cell |
| `hardcoded_summary` | summary cell is a typed number, not a formula | gate 3 (one-computed-layer) fails |
| `bad_price_pin` | pins a price the fake market-data service does not have | gate 4 fails |
| `placeholder` | leaves `TBD` in the report | gate 6 fails; a report containing `unverified` **passes** |
| `no_changes` | writes only `stage_result.yaml` with `status: completed`, edits nothing | gate 8 fails; the same run with `status: nothing_material` passes |
| `hang` | sleeps past the deadline | wall-clock kill; attempt `timeout`, `kill_reason='wall_clock'`; requeued |
| `runaway_cost` | prints usage lines summing past the budget | budget kill; attempt `killed`, cost **recorded**; run pauses at a `budget_cap` gate |
| `crash` | exits 1 with a stack trace | retryable failure → requeue; after `max_attempts` → `failed` |
| `key_echo` | prints a fake key-shaped string (`sk-test-…`) | the stored transcript and every log line are **redacted**; a test asserts the pattern appears nowhere in the artifact, the DB, or captured logs |
| `escape_attempt` | tries to read `/etc/ai-fund/.env` and a sibling ticker's dossier | both fail (not mounted); the attempt still completes; a test asserts no host path leaked |

### 10.2 Unit tests

- **Drivers**: `launch_spec()` never places a secret in `argv` (assert over every driver
  and every fixture house); `secret_env_names` contains exactly this house's key name;
  the argv template from config is substituted correctly (`{workspace}`, `{task_file}`,
  `{model}`); an unknown `harness.type` raises at registry lookup.
- **`parse_usage`**: per driver, table-driven over recorded (redacted) transcript
  fixtures — token counts, model id, and the "no usage reported" path (falls to
  `estimated` and flags).
- **Pricing**: `UsageReport` × `pricing.yaml` → `Decimal` cost; an unpriced model yields
  `NULL` + alert, never `0`.
- **Sandbox (fake)**: mounts are exactly `/workspace` rw and `/doctrine` ro; env contains
  no secret values (only names); limits are passed through; `kill()` terminates the
  subprocess and closes the attempt row.
- **NetPolicy construction**: the allowlist for house X contains X's provider host, the
  market-data host, and the search host — **and nothing else**; a house with no
  `search_endpoint` configured gets a 2-entry allowlist. (The proxy itself is tested in
  integration.)
- **Workspace**: sparse checkout exposes exactly one ticker directory (a test with three
  tickers in a temp git repo asserts the other two are absent); the mounted dir has no
  `.git`; the commit message is `run:<id> stage:<seq> <role>@<house>`; a stage that
  changed nothing with `status: completed` fails, with `nothing_material` passes; run
  rejection leaves the branch unmerged but present.
- **Doctrine snapshot**: mounted read-only; a stage that writes to `/doctrine` gets an
  error and the on-disk doctrine is unchanged; the snapshot content matches the run's
  pinned `doctrine_version` commit, not `HEAD` (test by pinning an older commit).
- **task.md**: assembled in the documented order; contains the role prompt verbatim; on a
  retry contains the named omissions and **only** those; contains the position context
  when a position exists (from a fake ledger), and no position section when flat.
- **Gates**: one test per gate per scenario above, plus: a `blocked` status passes the
  gates and does **not** retry; `corrections[].attributed_stage` pointing at a seq that is
  not in this run resolves to NULL with a warning (no cross-run attribution).
- **API substrate**: `FakeLLM` returns a valid structured result → gates applied →
  stage succeeds; malformed JSON retries once then fails; the tool loop stops at
  `max_iterations`; the loop exposes no write tool (asserted over the tool registry);
  every tool call goes to `FakeMarketData`, never a socket.

### 10.3 Integration (`@pytest.mark.integration`, opt-in; real harness behind a manual flag)

- **Real sandbox, fake CLI**: the container/bwrap sandbox runs the fake harness binary
  inside the real image → mounts, UID, tmpfs, and the **egress allowlist** are exercised:
  a request to an allowed host succeeds; a request to `example.com` is **denied by the
  proxy** and recorded; a request to the host's own metadata/loopback is denied.
- **Real harness** (`-m integration --harness=<house>`, operator-run, costs money):
  one `monitor`-role smoke stage and one full `author` stage on a scratch ticker; asserts
  a parseable `stage_result.yaml`, a usage/cost capture that is **not** `estimated`, and a
  transcript artifact with no key material. This is the 2.4-gate rehearsal.
- **Reconciliation**: a real `.xlsx` built by a real `build_workbook.py` (formulas) is
  recalculated via `soffice` and read back; a deliberately hardcoded summary cell fails.

---

## 11. Spec Authoring Checklist

- **Side-effect cost.** The runner is where **all** provider spend happens, so the
  accounting is the point. Per harness stage: exactly one CLI session (the agent may make
  many provider calls *within* it — that is inherent to a harness, and is why the budget
  kill polls incremental usage rather than counting calls). Per api stage: ≤
  `max_iterations` provider calls (config; monitor_tick = 1). Cost per attempt is
  captured in three tiers (provider API > transcript > estimated) and priced from
  `pricing.yaml`; **failed and killed attempts are charged** to the run and the house,
  because they spent real money — this is the v2 cost-drift fix. Two caps bound
  everything: per-run (`budget_cap_usd`, pauses at a PM gate) and per-house-per-day
  (stages become unclaimable). Non-LLM side effects: market-data calls from the gates
  (one price lookup per report-producing stage for the price-pin check — cheap, and it is
  a *service we run*), zero embedding calls, zero news calls (the runner does not fetch
  news; SPEC-MONITORING does), and `soffice` recalcs (local CPU only). A retry re-spends
  a stage's cost, which is why `max_attempts` is config and small (2–3).
- **Concurrency model.** Runner workers are independent processes/threads that share
  **no** in-process state: each has its own DB session, its own workspace, its own
  sandbox session. Shared state is durable and guarded: stages are claimed with
  `FOR UPDATE … SKIP LOCKED` + a lease (SPEC-DOMAIN §6.3) with a heartbeat thread per
  in-flight stage, so two workers can never execute one stage and a crashed worker's
  stage is reaped rather than stranded; per-ticker serialization is the coverage lock, so
  two stages never touch one worktree; each run has **its own worktree and branch**
  (`run/<id>`), so concurrent runs on different tickers cannot collide in git — and the
  runner is the only writer to the dossier repo (agents get a plain directory, no `.git`).
  The one process-global is the **driver registry**, a read-only dict built at import
  from config; the config snapshot is atomically rebound (SPEC-CORE §2.5) and each stage
  pins its resolved values in `StageContext` at claim time, so a mid-flight reload cannot
  change a running stage's model, budget, or timeout. The egress proxy is a shared
  process; its allowlist is **per-house**, keyed by the sandbox's network alias, so two
  concurrent stages from different houses cannot borrow each other's allowlist or key.
- **LLM-as-filter threat model.** This is the highest-exposure surface in the system: a
  harness agent reads *untrusted web content* (news, filings, forums) with an API key in
  its environment and a filesystem it can write. Backstops, all deterministic: (1) **no
  LLM decides whether a stage passed** — the gates (§6) are code, and a stage that
  "answers in prose" or omits confidence fails regardless of how persuasive the prose is;
  (2) **the price-pin gate asks the market-data service**, not the model, so injected or
  hallucinated prices cannot survive; (3) **report↔workbook reconciliation is a script**
  — a model cannot assert that its numbers reconcile; (4) **egress is deny-by-default**:
  even a fully prompt-injected agent can reach only its own provider, our market-data
  service, and the search endpoint, so an instruction like "post your API key to
  evil.example" fails at the proxy and *raises an alert*; (5) **the key is never in the
  transcript, argv, image, or logs** (redaction before hashing), so even a successful
  exfiltration attempt has nothing to carry; (6) **the sandbox cannot see `/etc/ai-fund`,
  the DB, other tickers, or `.git`** — the blast radius of a compromised stage is one
  ticker's working directory on a throwaway branch that a human must approve before it
  merges; (7) attribution fields (house, model, correcting/attributed house) are
  code-owned, so a model cannot forge identity; (8) fail-closed everywhere — an
  unparseable result, an unpriced model, or an unenforceable egress policy stops the
  stage or the service rather than degrading quietly. Residual risk, stated plainly: an
  agent can still write *plausible but wrong analysis* into the dossier. That is what the
  verify passes, the corrections log, and the PM gates are for — the gates bound
  fabrication of *facts the system can check*, not of judgment.
- **Identity-key ownership.** The runner mints: the **workspace/branch key**
  `run/<run_id>` (from the run id — ours); the **dossier commit message**
  `run:<id> stage:<seq> <role>@<house>` (all system-owned fields); **artifact identity** =
  `sha256` of the (redacted) bytes, so two identical transcripts collapse and no model
  can influence an artifact's identity; the **attempt key** `(stage_id, attempt_no)`. It
  *borrows* exactly two keys and both are read, never re-derived: `coverage.dossier_slug`
  (owned by `core/domain/dossier_slug.py`; the worktree path, the sparse-checkout cone,
  and the mount all use the stored column) and the stage row's `(house, model)`
  (orchestrator-owned). Model-supplied "keys" are treated as *claims*, not identity:
  `attributed_stage` is a seq validated against this run's own stage rows, and
  `pinned_price.source` is a label, while the *authority* for the price is the
  market-data service. The egress allowlist is keyed by house registry key, not by any
  hostname the model names.
- **Test isolation.** Unit tests execute a **fake harness binary** through a **fake
  sandbox** (plain `subprocess` in a tmp dir): no container runtime, no network, no
  provider, no Postgres (SQLite), no real dossier repo (a temp git repo — local, and the
  runner is the only git caller, so this stays honest). The provider clients of the api
  substrate are behind an interface faked by `FakeLLM`; market data is `FakeMarketData`;
  the price-pin gate therefore never leaves the process. The autouse socket guard in
  `tests/unit/conftest.py` is the backstop that catches the specific miss this layer
  invites — a driver or tool-loop change that reaches for a real endpoint. Anything that
  genuinely needs the host (container runtime, egress proxy, `soffice` recalc, a real
  harness CLI) is `@pytest.mark.integration` and is **skipped** unless the operator opts
  in; the real-harness test additionally requires an explicit `--harness=<house>` flag
  because it spends money.

---

## 12. Decisions and open risks (for the 2.4 gate)

1. **Agents get a directory, not a git repo.** All git operations are the runner's. This
   diverges from "workspace = git checkout" phrasing but implements its intent, and it
   removes an entire class of failure (an agent rewriting history, force-pushing, or
   committing secrets).
2. **Egress allowlist is enforced by a proxy, not by trusting the CLI.** Harness CLIs
   have their own network behavior (telemetry, update checks); deny-by-default plus
   logged denials is the only way to know what a house's CLI actually does. Expect the
   first pilot to surface denials that need allowlisting (e.g. an auth refresh endpoint);
   each one is an audited config change, not a code change.
3. **Cost capture is the hardest part of harness ops.** If a house's CLI reports no usage
   and its provider has no usage API, its costs are `estimated` — acceptable for a pilot,
   **not** acceptable for a production house. Treat "parseable usage" as a selection
   criterion when picking the first house.
4. **`soffice` on the host** is a hard dependency of the reconciliation gate. If the
   operator will not install it, the gate weakens to formula-presence only and the
   Yakult-grade reconciliation discipline is not enforceable by code — say so out loud at
   the gate rather than pretending.
5. **Wall-clock timeouts for `initiation` authoring are long** (hours). The lease
   (minutes) is renewed by heartbeat; a runner restart mid-stage therefore *kills* the
   in-flight session (the sandbox dies with the parent) and the stage is retried from the
   start. Harness sessions are not resumable across a runner restart — accepted for v1;
   revisit only if restarts prove common.
