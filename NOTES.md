# NOTES

## 2026-07-12 — v3 repo bootstrapped (transfer from v2)

Created `ai-fund3` as a fresh repo (PM-confirmed fresh-repo decision, closing the open
item in design/00). Copied in: the 8 redesign docs → `design/`; the 8 stage role
prompts → `doctrine/roles/` (unchanged from the 2026-07-07 Fable design session); the
proprietary doctrine source (`Equity-Research-Prompts_2026-06-05.md`) and the two
gold-standard report zips (Yakult, LINK REIT) → `proprietary/` (gitignored, per the
v2 policy that proprietary doctrine stays out of git). Wrote `PROMPTS.md` as the
phase-ordered execution file.

No code yet. Process/convention memory ported from v2 (`/home/alvin/aicode/ai-fund`),
history not copied: the Absolute Rules + Editing Rules from v2's `NOTES.md` →
`AGENTS.md` (adapted to v3 — dropped Redis/dispatchers/personas, kept secrets discipline,
test isolation, no-model-defaults, config-outside-deploy-tree); v2's
`docs/SPEC_AUTHORING_CHECKLIST.md` → `docs/` (test-isolation item updated for the
harness); `pytest.ini` (network-isolation markers) and a cleaned `.pre-commit-config.yaml`
(gitleaks + pre-commit-hooks + ruff; dropped the v2-specific pytest hook). v2's dated
Implementation Decisions, code-audits, and `.claude/settings.local.json` (accumulated
permission grants) were deliberately left behind as history.

Next session: start at PROMPTS.md Phase 0.1 (repo scaffolding — design/, doctrine/roles/,
AGENTS.md, pytest.ini, .pre-commit-config.yaml, docs/SPEC_AUTHORING_CHECKLIST.md already
in place; dirs, pyproject, .env.example, README expansion, initial commit remain).

## 2026-07-12 — Phase 0.1 complete (repo scaffolding)

Executed PROMPTS.md Phase 0.1 (CHORE). Done:

- **Directory skeleton**: `core/`, `runner/`, `adapter/`, `cli/` (Python packages, empty
  `__init__.py`); `doctrine/{sections,lessons,report,templates}/` and `specs/`, `tests/`,
  `tests/unit/` (`.gitkeep` so empty dirs track).
- **`dossiers/`** initialized as a **separate git repo** with a first empty commit (per
  design/05 §8 / design/06), and added to this repo's `.gitignore` so the two repos stay
  cleanly separated (the main repo never tracks the embedded repo).
- **`pyproject.toml`** added: setuptools backend, `requires-python = ">=3.12"`, runtime
  deps limited to the convention-mandated foundational libs (pydantic, pydantic-settings,
  structlog); dev extras = ruff/pre-commit/pytest(+asyncio/mock/cov). `pytest.ini` markers
  folded into `[tool.pytest.ini_options]` (single source of truth) and `pytest.ini`
  **removed**. ruff config (line-length 100, target py312, lint select
  E/F/W/I/UP/B/SIM/C4/RUF) is read by both local and pre-commit ruff (same pyproject).
- **`.env.example`** added: fake values only, secrets-only shape per design/05 §6 (non-secret
  config belongs in `/etc/ai-fund/*.yaml`). Exact var names finalized in SPEC-CORE (1.2).
- **`README.md`** expanded: ~20-line architecture sourced from design/01 + design/05;
  Layout section updated with the new dirs.
- **`CONVENTIONS.md`** created as a tool-agnostic mirror (AGENTS.md remains the source of
  truth). Includes the venv/dev setup commands.

Decisions worth flagging:

- Host has **only Python 3.14.4**; it satisfies `>=3.12`. Verified ruff 0.8.4 installs and
  runs on 3.14 (abi3 wheels), so the operator's pinned `.pre-commit-config.yaml` rev is
  left unchanged.
- A `.venv/` (gitignored) was created for dev tooling; `pre-commit install` ran, so the
  git hook is active — keep the venv active when committing.
- Initial commit author is `alvin <ajiang@gmail.com>` (matches v2's recent commits) applied
  via per-command `git -c user.name=… user.email=…` — global `user.name` is unset and no
  git config files were modified. Amend if a different identity is preferred.

Next: Phase 0.2 (seed doctrine) — a reasoning-model session handles the SPEC steps.
