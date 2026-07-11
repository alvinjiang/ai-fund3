# AGENTS.md — ai-fund3 agent contract

> **Read this before doing anything else.** This file is the contract for human and
> AI agents editing this repository (succeeding v2's NOTES.md role; the dated work log
> lives separately in `NOTES.md`). opencode loads this file automatically at session
> start.
>
> **Execution order:** read `design/00-DECISIONS-LOG.md` → `design/01-VISION-AND-PRINCIPLES.md`
> first, then work through `PROMPTS.md` top-to-bottom (phase-ordered). v2 source for
> porting lives at `/home/alvin/aicode/ai-fund` and stays running until the Phase 7
> cutover.

## Absolute Rules

1. **Never read `.env`, `.env.local`, `.env.*`, `secrets/`, `credentials/`, or any
   `*.pem`/`*.key`/`*.p12`/`*.pfx` file** — not with `cat`/`head`/`tail`/read/grep/
   sed/awk, not via a wrapper script, not even to "verify it exists" (use `ls -la`
   for that). Use `.env.example` or the settings module for configuration shape.
2. **Never print, log, echo, or include the value of any env var whose name ends in
   `_KEY`, `_TOKEN`, `_PASSWORD`, or `_SECRET`** — not in debug output, error messages,
   tracebacks, or comments. Use the redaction helper when a value must be shown for
   diagnostics.
3. **Never commit `.env`, real secrets, or anything matched by `.gitignore`.** If a
   secret is accidentally staged, do not commit — flag it to the operator.
4. **Never write code that reads secrets outside the settings module.** All keys,
   tokens, and credentials flow through pydantic-settings (the v3 `config/settings.py`
   equivalent). Code references `settings.foo_key`, never `os.getenv("FOO_KEY")`,
   never `open(".env")`, never an alternate path.
5. **Tests and examples use fake values only** — `sk-test-...`, `dummy-token`,
   `replace_me`. Never paste a real key into a test, docstring, or commit message.
6. **When `checkconfig` reports a key is missing or invalid, do not read `.env` to
   "see what's there."** Ask the operator. The operator maintains `.env`, not the agent.

## Conventions

**Workflow (carry over from v2 — they worked):**
- Spec → implement. Each feature gets a self-contained `specs/SPEC-*.md` first
  (strongest reasoning model tier), then a BUILD branch implements it. One branch per
  spec; PR review before merge.
- Every change gets a dated `NOTES.md` entry (date, reason, affected files). `README.md`
  is updated on every feature branch.
- TDD. Run `pytest tests/` after each meaningful change.

**Specs:**
- New `specs/SPEC-*.md` must address `docs/SPEC_AUTHORING_CHECKLIST.md` before merge:
  side-effect cost, concurrency model, LLM-as-filter threat model, identity-key
  ownership, test isolation. Mark `N/A` only with a reason.

**Tests:**
- Unit tests use fakes/mocks for Postgres, LLM providers, market data, news, search,
  and the harness/sandbox. They must not require real external services.
- Unit tests block outbound network sockets by default (autouse socket fixture in
  `tests/unit/conftest.py`). Use `@pytest.mark.allow_network` only for explicit,
  reviewed exceptions; `@pytest.mark.integration` for opt-in external-service tests.
- Deterministic money path: prices, risk metrics, position/cash accounting, and trade
  confirmation are code, never LLM output (`design/01` principle 6). LLMs interpret and
  recommend; they do not compute NAV or book trades.

**Code:**
- Python 3.12+. `structlog` for logging. pydantic-settings for all config and secrets.
- No model names, prices, or budgets hardcoded anywhere — registry + pricing config
  only (`design/05` §6).
- Operator config and secrets live outside the deploy tree (e.g. `/etc/ai-fund/`); a
  `git reset` can never clobber them (`design/05` §6 — v2 hard-won lesson).
- Every mutating API action is authorized (PM-only allowlist) and audit-logged. Durable
  side effects carry idempotency keys.
- `.gitignore` excludes secrets, venvs, Python caches, IDE files, runtime logs, local DB
  dumps, build artifacts, and `proprietary/` (proprietary doctrine + report samples
  never enter git). `.env.example` documents placeholders only. `config/*.yaml.example`
  are resettable non-secret templates.

**Agent instructions:**
- `AGENTS.md` (this file) is the single agent-instruction file. Do not also create
  `CLAUDE.md`, `CODEX.md`, `.cursorrules`, or `.aider.conf.yml`.

## Retired v2 concepts (do not re-introduce before Phase 7 cutover)

Redis Streams / dispatcher processes (Postgres-only queue replaces), persona identities
(houses + dossier context replace), the debate engine (structured disagreement in runs
replaces), channel-membership-as-watchlist (coverage state drives channels), one-pass
report generation (the initiation pipeline replaces). See `design/06` for the full
retire list.

## v2 prior art (read from v2 when a SPEC step names it — do not copy into v3)

- `/home/alvin/aicode/ai-fund/db/models.py` + `docs/SPEC_PREDICTION_TRACKING.md` —
  schema prior art (Phase 1.1).
- `/home/alvin/aicode/ai-fund/{infra,data_sources,mattermost,risk,trades,cost,news,search,reports}`
  — port sources (Phases 1.4, 2.3, 5.1, 7.2).
- `/home/alvin/aicode/ai-fund/scripts/{checkconfig.py,llm_ping.py,bootstrap_integrations.py}`
  — port/mine (Phases 1.2, 5.1).
