# SPEC-CORE implementation review

Review of `specs/SPEC-CORE.md` against the implementation on branch `spec-core-truthing`,
after the truthing pass and BUGS #1–#5.

**Method:** section-by-section audit. Every finding below is either read off a named
`file:line` or verified by running the code. Claims marked **(verified)** were reproduced
empirically in this session, not inferred from reading.

**Baseline at review time:** `pytest tests/unit` → 370 passed, 32 skipped.

**Status vocabulary:** IMPLEMENTED / PARTIAL / MISSING / DEVIATES (present but differs
from spec in a way that changes behavior).

---

## §2 Configuration

### Findings

- **§2 templates** — IMPLEMENTED. `config/{houses,fund,pricing}.yaml.example` and
  `.env.example` all exist and match the spec's documented shape.
- **§2.1 `env_file` via `AI_FUND_CONFIG_DIR`** — DEVIATES. `core/config/settings.py:21`
  hardcodes `env_file=".env"` (CWD-relative) instead of
  `os.environ.get("AI_FUND_CONFIG_DIR", "/etc/ai-fund") + "/.env"`. The operator `.env`
  at `/etc/ai-fund/.env` is never read, and `config_dir` (line 24) is a dead field no
  code consumes.
- **§2.1 field types** — DEVIATES. `database_url: str` not `PostgresDsn`;
  `core_api_url` / `market_data_url` / `search_endpoint` are `str` not `AnyHttpUrl`
  (`core/config/settings.py:25-33`). No URL validation fires.
- **§2.1 defaults** — DEVIATES. The spec makes `database_url`, `artifacts_dir`,
  `dossier_repo_path`, `doctrine_dir`, `market_data_url` required; the impl gives all
  five dev defaults, including a literal placeholder DSN
  (`postgresql://ai_fund:replace_me@localhost:5432/ai_fund`, `settings.py:25`). A
  deployment missing `DATABASE_URL` gets a placeholder instead of failing loudly — the
  silent-fallback pattern AGENTS.md forbids. Only `core_api_token` is required.
- **§2.1 `frozen`** — IMPLEMENTED + tested. `settings.py:21`, asserted in
  `tests/unit/config/test_settings.py:30-35`.
- **§2.1 `lru_cache` `get_settings()`** — PARTIAL. Hand-rolled module global +
  `reset_settings()` (`settings.py:39-51`) rather than `lru_cache`. Behaviorally
  equivalent; no test covers cache identity.
- **§2.1 `provider_keys` resolved from `houses.yaml`** — PARTIAL **(verified)**. The
  mechanism is correct (`resolve_provider_keys`, `core/config/houses.py:63-74`, keyed by
  env NAME, raises `KeyError(name)` never the value) and is tested — but it has **no
  production caller**. `grep` finds callers only in `tests/unit/config/test_houses.py`.
  `Settings.provider_keys` is `{}` at runtime; no house API key is reachable by any
  consumer.
- **§2.1 redaction helper + structlog processor** — MISSING **(verified)**. No
  `core/infra/` package; `grep -rn "def redact"` returns nothing repo-wide. The
  `*_KEY|*_TOKEN|*_PASSWORD|*_SECRET` processor and the
  `redact(value) -> "set (sha256:…)" | "missing"` helper are absent. AGENTS.md Rule 2
  is currently unenforced by code.
- **§2.1 no `os.getenv` for secrets outside settings** — DEVIATES. `cli/main.py:55`
  reads `os.environ["CORE_API_TOKEN"]` directly — a secret, read outside the settings
  module. (`core/config/checkconfig.py:19` also reads `os.environ`, but only for
  presence, which is legitimate.)
- **§2.2 `assignable`/`meta` mutually exclusive** — IMPLEMENTED + tested.
  `houses.py:41-45`; `test_houses.py:17-19`.
- **§2.2 ≥1 `enabled AND assignable`** — IMPLEMENTED + tested. `houses.py:52-56`;
  `test_houses.py:22-28`.
- **§2.2 every `api_key_env` present in `.env`** — PARTIAL. Not enforced at load;
  surfaced only as a checkconfig row (`checkconfig.py:44-55`) and via the uncalled
  `resolve_provider_keys`. The spec places this under `houses.py` validation.
- **§2.2 referenced model ids exist in `pricing.yaml`** — PARTIAL. Not enforced in
  `houses.py` (no access to pricing); only a checkconfig row (`checkconfig.py:31-42`).
  `reload_config` validates the three files independently and never cross-checks them
  (`core/config/store.py:45-49`), so a reload with an unpriced model succeeds.
- **§2.2 harness driver key known to the runner registry** — MISSING.
  `HarnessConfig.type` is a free `str` (`houses.py:19`); no driver registry exists. A
  typo'd `type: codx-cli` validates clean.
- **§2.2 houses upsert into the `houses` table** — MISSING **(verified)**. No upsert
  function, no `core/db/repo/houses.py`, no `fund bootstrap` command (`grep -rni
  bootstrap` over `*.py` returns nothing), no FastAPI startup/lifespan hook. The `House`
  table (`core/db/models.py:42`) is never populated from config, so insert-new /
  update-mirrored / vanished→`enabled=false`-never-deleted are all unimplemented.
- **§2.3 `queue.backoff` nested-vs-flat** — DEVIATES, **silent-fallback bug (verified)**.
  The YAML nests `backoff: { base_s, max_s, jitter }`; `QueueCfg` declares flat
  `backoff_base_s/max_s/jitter` (`core/config/fund.py:43-47`) and pydantic's default
  `extra="ignore"` drops the nested key. Reproduced: editing the template to
  `backoff: { base_s: 120, max_s: 60, jitter: 0.9 }` still loads as
  `backoff_base_s=30 backoff_max_s=3600 backoff_jitter=0.2`. **Operator backoff tuning
  is silently discarded.** The bug is masked because the code defaults happen to equal
  the template values.
- **§2.3 `lead_review.candidacy`** — MISSING **(verified)**. No field on `FundConfig`;
  dropped by `extra="ignore"` (`fund.py:54-66`). `hasattr(cfg, "lead_review")` is `False`.
- **§2.3 "every number is operator policy, not a code default"** — DEVIATES broadly.
  Only `RunPolicy.max_attempts` is required (`fund.py:20`). `budget_cap_usd`,
  `stage_timeout_s`, `verify_count`, all of `MonitorCfg` (including a hardcoded
  `house: str = "gpt"`), `AutoCrossCheck` thresholds, `QueueCfg` and `OrchestratorCfg`
  all carry code defaults, so a missing key falls back silently instead of failing
  checkconfig.
- **§2.3 exchanges / cadence / scheduler / retention** — PARTIAL. Typed as bare `dict`
  (`fund.py:58-66`); session strings, cron expressions and retention days get no shape
  validation at load.
- **§2.3 `verify_count` 1..3 bound** — MISSING. Plain `int`, no `ge=1, le=3` (`fund.py:23`).
- **§2.4 pricing resolver** — IMPLEMENTED. `price_call` (`core/config/pricing.py:35-51`)
  is the single price site; an unpriced model raises `KeyError` rather than silently
  costing zero. Note: `billable_input = max(input - cached, 0)` assumes providers report
  cached tokens *inside* the input count — an unstated convention worth pinning in the spec.
- **§2.4 `effective_from`** — MISSING. Not modeled on `ModelPrice` (`pricing.py:15-20`);
  dropped by `extra="ignore"`. No date-effective price selection, so a repricing cannot
  be staged ahead of its effective date.
- **§2.4 unpriced-at-attribution → `cost_source='estimated'`, `cost_usd=NULL`, desk
  alert** — MISSING. `price_call` raises; no caller implements the estimated-row-plus-alert
  path.
- **§2.5 frozen snapshot + atomic rebind** — PARTIAL. `core/config/store.py:18-38` gives
  a frozen dataclass and a single-reference rebind, which satisfies the concurrency shape.
- **§2.5 load = read → validate → rebind** — PARTIAL **(verified)**. `reload_config`
  takes YAML **strings**, not paths (`store.py:40`). Nothing in the repo opens
  `/etc/ai-fund/*.yaml` — grep for those filenames in `*.py` hits only docstrings. The
  "read" half of the pipeline does not exist, so **config is never loaded in production**;
  `get_config()` raises unless a test called `set_config`.
- **§2.5 invalid file leaves previous snapshot intact** — IMPLEMENTED by construction
  (all three loads complete before `set_config`, `store.py:45-50`) but untested — there
  is no `tests/unit/config/test_store.py`.
- **§2.5 SIGHUP / `fund config reload`** — MISSING. No `signal.SIGHUP` handler, no
  `config reload` subcommand.
- **§2.6 checkconfig checks** — PARTIAL: **4 of 12**. Implemented
  (`checkconfig.py:22-63`): `assignable_house`, `models_priced`, `api_keys_present`,
  `pm_user_ids`. Missing: config dir exists, `.env` is `0600`, settings parse, DB
  reachable, `alembic current == head`, `artifacts_dir` writable, dossier repo present
  and clean, `doctrine_dir` readable + `doctrine_versions` row for the current commit,
  market-data reachable.
- **§2.6 presence-only, never print the value** — IMPLEMENTED + tested.
  `checkconfig.py:52-54`; asserted in `test_checkconfig.py:59-63`.
- **§2.6 OK/WARN/FAIL table** — PARTIAL. `format_table` works (`checkconfig.py:71-75`)
  but no check ever emits `WARN`; the advisory tier is unused.
- **§2.6 `--strict`** — DEVIATES in the module entrypoint. `cli/main.py:264` is correct
  (non-zero only under `--strict`). But `checkconfig.py:78-81` `main()` ignores `--strict`,
  always exits non-zero on FAIL, and constructs `Settings(_env_file=None,
  core_api_token="x")` — a fabricated token that bypasses the real settings load. It is
  `pragma: no cover` and would misreport if invoked.
- **§2.6 `ExecStartPre` systemd wiring** — MISSING. No unit files in the repo reference
  checkconfig.
- **§2 test coverage** — PARTIAL. `tests/unit/config/` has only `test_settings.py`,
  `test_houses.py`, `test_checkconfig.py`. No `test_fund.py`, `test_pricing.py` or
  `test_store.py`: `price_call` arithmetic, the queue-backoff shape, and reload atomicity
  are all untested. The missing `test_fund.py` is precisely why the backoff bug survived.

### §2 gaps ranked by severity

1. **No production config loader.** Nothing reads `/etc/ai-fund/{houses,fund,pricing}.yaml`;
   `get_config()` raises outside tests. §2.5's read step and the whole startup path are absent.
2. **`env_file` hardcoded to `".env"`** — `AI_FUND_CONFIG_DIR` is ignored, so the operator
   `.env` is never read.
3. **`provider_keys` never populated** — no production caller; no house key reachable at runtime.
4. **Redaction entirely absent** — AGENTS.md Rule 2 unenforced by code, and `cli/main.py:55`
   reads a secret straight from `os.environ`.
5. **Dev defaults on required settings** — a misconfiguration becomes a silent
   wrong-target connection rather than a loud failure.
6. **`queue.backoff` silently discarded** — operator retry tuning has no effect.
7. **Houses→DB upsert missing** — the `houses` table is never reconciled with config.
8. **3 of 5 §2.2 validations not enforced at load**; harness driver-key validation absent entirely.
9. **checkconfig covers 4 of 12 checks** — the missing ones (`.env` mode, alembic head, DB
   and market-data reachability, dossier/doctrine) are the ones that gate a safe service start.
10. **Missing model fields**: `pricing.effective_from`, `fund.lead_review.candidacy`,
    `verify_count` 1..3 bound.
