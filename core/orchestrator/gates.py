"""Gate helpers (SPEC-CORE §3.5 / §8 module layout).

Maps run types to their terminal gate kinds and allowed answers. The engine uses these
to decide whether to open a gate at run completion. Extracted for testability.
"""

from __future__ import annotations

_TERMINAL_GATES: dict[str, tuple[str, list[str]]] = {
    "initiation": ("initiation_decision", ["active", "watch", "reject"]),
    "lead_review": ("lead_change", ["approve", "keep"]),
    "distillation": ("doctrine_amendment", ["merge", "reject"]),
}


def terminal_gate(run_type: str) -> tuple[str, list[str]] | None:
    """Return (gate_kind, allowed_answers) for runs that end at a PM gate, else None."""
    return _TERMINAL_GATES.get(run_type)
