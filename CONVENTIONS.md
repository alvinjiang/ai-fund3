# Conventions

This file is the human-contributor mirror of the durable contract. The authoritative
source is **`AGENTS.md`** (the agent contract — opencode reads it at session start); if
anything here disagrees with `AGENTS.md`, `AGENTS.md` wins. Keep this file short.

## Workflow (carried over from v2 — it worked)

- **Spec → implement.** Each feature gets a self-contained `specs/SPEC-*.md` first
  (strongest reasoning tier), then the implementation follows it.
- **Commit to `main`.** No PR process; branch (`spec-<topic>`) only when work needs
  isolation, and merge back as soon as it is green. The review gate is a reviewing-agent
  pass over `main` against `docs/BUILD_REVIEW_CHECKLISTS.md`.
- **TDD.** Run `pytest` after each meaningful change.
- **Every change gets a dated `NOTES.md` entry** (date, reason, affected files).
  `README.md` is updated whenever a change makes it stale.

## Environment setup

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"        # runtime + dev tooling (ruff, pre-commit, pytest)
pre-commit install             # installs the git hook
pytest                         # unit tests (network blocked by default)
ruff check . && ruff format .  # lint + format
```

The host runs Python 3.12+ (developed on 3.14; `requires-python = ">=3.12"`). ruff is
pinned to `0.8.x` to match `.pre-commit-config.yaml`; both local and pre-commit ruff
read this repo's `pyproject.toml`, so config is single-source.

## Code

- Python 3.12+. `structlog` for logging. `pydantic-settings` for **all** config and
  secrets.
- No model names, prices, or budgets hardcoded anywhere — registry + pricing config
  only (`design/05` §6).
- Operator config and secrets live outside the deploy tree (`/etc/ai-fund/`); a
  `git reset` can never clobber them.
- Every mutating API action is authorized (PM-only allowlist) and audit-logged.
  Durable side effects carry idempotency keys.
- No comments unless explicitly requested; let names and tests carry intent.

## Secrets (Absolute Rules — see AGENTS.md)

- Never read `.env`/`.env.*`/`secrets/`/`credentials/`/`*.pem`/`*.key`. Use
  `.env.example` or the settings module for configuration shape.
- Never print/log/echo any env var ending in `_KEY`, `_TOKEN`, `_PASSWORD`, `_SECRET`.
- Never commit `.env`, real secrets, or anything matched by `.gitignore`.
- Never read secrets outside the settings module. Code references `settings.foo_key`,
  never `os.getenv("FOO_KEY")`.
- Tests and examples use fake values only (`sk-test-...`, `dummy-token`, `replace_me`).

## Tests

- Unit tests use fakes/mocks for Postgres, LLM providers, market data, news, search,
  and the harness/sandbox. They must not require real external services.
- Unit tests block outbound network sockets by default (autouse socket fixture in
  `tests/unit/conftest.py`). Use `@pytest.mark.allow_network` only for explicit,
  reviewed exceptions; `@pytest.mark.integration` for opt-in external-service tests.
- Pytest config (markers + import mode) lives in `pyproject.toml` `[tool.pytest.ini_options]`.
- Deterministic money path: prices, risk metrics, position/cash accounting, and trade
  confirmation are code, never LLM output (`design/01` principle 6).

## Specs

- New `specs/SPEC-*.md` must address `docs/SPEC_AUTHORING_CHECKLIST.md` before merge:
  side-effect cost, concurrency model, LLM-as-filter threat model, identity-key
  ownership, test isolation. Mark `N/A` only with a reason.

## Retired v2 concepts (do not re-introduce before Phase 7 cutover)

Redis Streams / dispatcher processes, persona identities, the debate engine,
channel-membership-as-watchlist, one-pass report generation. See `design/06`.
