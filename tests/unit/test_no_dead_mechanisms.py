"""Every public symbol in the orchestration modules has a non-test caller (AGENTS.md 5).

A module that is created, tested, and never called is not an implementation. Three review
rounds produced the same failure — a mechanism built in isolation, never wired, guarded by
a test that asserted its existence rather than its effect — so this guard is mechanical.

**What this catches:** a symbol referenced by *nothing* in the production tree — the
orphan-module case (BUGS #3 created four such modules, all with passing tests).

**What it does not catch:** transitive deadness. ``promote_run`` is referenced by
``engine.tick``, so it counts as wired even though ``tick`` itself has no caller. That is
deliberate — reachability analysis would flag two dozen symbols for one root cause. The
roots (``tick``, ``build_scheduler``) are themselves orphans and are listed below, so the
condition is still reported, once, where it can be fixed.

``KNOWN_DEAD`` is a **ratchet, not a permission slip**: it lists the orphans that existed
when docs/SPEC-CORE-REVIEW.md was written, each with a reason. The test fails if a *new*
orphan appears, and fails if a listed one gets wired but is left in the list. Wiring work
deletes entries; nothing should add one without a reason.
"""

from __future__ import annotations

import ast
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]

# Modules whose public surface must be wired into the running system.
GUARDED = ["core/orchestrator", "core/scheduler", "cli/format.py"]

# Symbol -> why nothing references it. Delete the entry when you wire it.
# Source: docs/SPEC-CORE-REVIEW.md (2026-07-18).
KNOWN_DEAD = {
    # The two roots. Everything they call is dead with them; fixing these two is the
    # "core service entrypoint" item at the top of the review's recommended order.
    "tick": "SPEC-CORE §3.3 — no core service entrypoint; the orchestrator loop never runs.",
    "build_scheduler": "SPEC-CORE §4 — no core service entrypoint; nothing starts APScheduler.",
    # The four §8 modules created by BUGS #3 — built, tested, never called.
    "terminal_gate": "SPEC-CORE §3.5 — engine.finalize_run special-cases initiation instead.",
    "run_over_budget": "SPEC-CORE §3.5 — engine.py keeps a private copy of this check.",
    "pause_for_budget": "SPEC-CORE §3.5 — engine.py keeps a private _pause_for_budget.",
    "exchange_sessions": "SPEC-CORE §4 — nothing converts 'pre:23:00' into a trigger yet.",
    "all_exchanges": "SPEC-CORE §4 — same: calendar-driven registration is unimplemented.",
    "table": "SPEC-CORE §6 — cli/_emit prints JSON in both branches, so --json is a no-op.",
    # Jobs that exist but are in no registry, so the scheduler cannot reach them.
    "session_tick": "SPEC-CORE §4 — calendar-driven; not in JOBS, so never registered.",
    "watch_tick": "SPEC-CORE §4 — calendar-driven; not in JOBS, so never registered.",
    "schedule_watchdog": "SPEC-CORE §7.8 — not in JOBS and emits no alert; unschedulable.",
}


def _public_defs(path: Path) -> set[str]:
    tree = ast.parse(path.read_text())
    return {
        n.name
        for n in tree.body
        if isinstance(n, ast.FunctionDef | ast.AsyncFunctionDef) and not n.name.startswith("_")
    }


def _referenced_names(sources: list[Path]) -> set[str]:
    """Every identifier *referenced* anywhere in production code.

    AST rather than text: a mention in a docstring or comment is not wiring, and
    ``_pause_for_budget`` is not a reference to ``pause_for_budget``. A ``def`` binds
    ``FunctionDef.name`` (a plain string, not a ``Name`` node), so definitions are
    naturally excluded while every use site — call, registry value, attribute, decorator,
    re-export — is counted.
    """
    seen: set[str] = set()
    for path in sources:
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.Name):
                seen.add(node.id)
            elif isinstance(node, ast.Attribute):
                seen.add(node.attr)
            elif isinstance(node, ast.alias):
                seen.add(node.asname or node.name.split(".")[-1])
    return seen


def _guarded_files() -> list[Path]:
    files: list[Path] = []
    for entry in GUARDED:
        p = REPO / entry
        files.extend(sorted(p.glob("*.py")) if p.is_dir() else [p])
    return [f for f in files if f.name != "__init__.py"]


def _production_sources() -> list[Path]:
    return [
        p
        for p in REPO.glob("**/*.py")
        if "tests" not in p.parts
        and ".venv" not in p.parts
        and "migrations" not in p.parts
        and p.name != "conftest.py"
    ]


def test_every_public_orchestration_symbol_has_a_production_caller():
    referenced = _referenced_names(_production_sources())
    dead: dict[str, str] = {}

    for file in _guarded_files():
        for name in _public_defs(file):
            if name not in referenced:
                dead[name] = str(file.relative_to(REPO))

    new_dead = {n: loc for n, loc in dead.items() if n not in KNOWN_DEAD}
    assert not new_dead, (
        "New dead mechanism(s) — implemented but never called from production code.\n"
        f"{new_dead}\n"
        "AGENTS.md rule 5: wire it, or add it to KNOWN_DEAD with a reason."
    )

    revived = sorted(set(KNOWN_DEAD) - set(dead))
    assert not revived, (
        f"These now have production callers: {revived}. "
        "Delete them from KNOWN_DEAD so the ratchet keeps tightening."
    )
