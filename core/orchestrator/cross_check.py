"""Auto cross-check rule (SPEC-CORE §3.4 / design/03 §2.3).

Appends a contributor cross-check to an ``event_analysis`` run when the author's result
materially changes the thesis: |ΔTP| > ``tp_change_pct`` vs the last registered
target_price, a stance change, or a ``thesis``-severity triggering event. The rule reads
*registered predictions and the event row* — both system-owned — so a model cannot talk its
way out of a cross-check by writing prose; it can only trigger one by moving a registered
number or stance.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any


def _extract_target_price(result: dict[str, Any]) -> Decimal | None:
    for p in result.get("predictions", []) or []:
        if p.get("kind") == "target_price" and p.get("value") is not None:
            return Decimal(str(p["value"]))
    return None


def _extract_stance(result: dict[str, Any]) -> str | None:
    for p in result.get("predictions", []) or []:
        if p.get("kind") == "stance" and p.get("stance"):
            return p["stance"]
    return result.get("stance")


def auto_cross_check(
    result: dict[str, Any],
    *,
    last_tp: Decimal | None,
    last_stance: str | None,
    event_severity: str | None,
    tp_change_pct: Decimal,
    stance_change: bool = True,
    thesis_tripwire: bool = True,
) -> bool:
    if event_severity == "thesis" and thesis_tripwire:
        return True
    new_tp = _extract_target_price(result)
    if (
        new_tp is not None
        and last_tp
        and last_tp != 0
        and abs(new_tp - last_tp) / abs(last_tp) * Decimal(100) > tp_change_pct
    ):
        return True
    if stance_change:
        new_stance = _extract_stance(result)
        if new_stance and last_stance and new_stance != last_stance:
            return True
    return False
