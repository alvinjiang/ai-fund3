"""Output formatting for the ``fund`` CLI (SPEC-CORE §6 / §8 module layout).

Simple text-table rendering for human-readable CLI output; ``--json`` bypasses this.
"""

from __future__ import annotations

from typing import Any


def table(rows: list[dict[str, Any]], columns: list[str]) -> str:
    """Render a list of dicts as a fixed-width text table."""
    if not rows:
        return "(no rows)"
    widths = {
        c: max(len(c), max((len(str(r.get(c, ""))) for r in rows), default=0)) for c in columns
    }
    header = "  ".join(c.ljust(widths[c]) for c in columns)
    sep = "  ".join("-" * widths[c] for c in columns)
    body = "\n".join("  ".join(str(r.get(c, "")).ljust(widths[c]) for c in columns) for r in rows)
    return f"{header}\n{sep}\n{body}"
