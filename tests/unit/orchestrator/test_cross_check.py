"""Auto cross-check rule — SPEC-CORE §3.4 / §9.2."""

from __future__ import annotations

from decimal import Decimal

from core.orchestrator.cross_check import auto_cross_check


def _result(tp=None, stance=None):
    r = {"predictions": []}
    if tp is not None:
        r["predictions"].append({"kind": "target_price", "value": tp})
    if stance is not None:
        r["predictions"].append({"kind": "stance", "stance": stance})
    return r


def test_tp_move_above_threshold_triggers():
    # 3500 vs 3000 = +16.7% > 10% -> cross-check
    assert (
        auto_cross_check(
            _result(tp=Decimal("3500")),
            last_tp=Decimal("3000"),
            last_stance=None,
            event_severity=None,
            tp_change_pct=Decimal("10"),
        )
        is True
    )


def test_tp_move_below_threshold_does_not_trigger():
    # 3150 vs 3000 = +5% < 10%
    assert (
        auto_cross_check(
            _result(tp=Decimal("3150")),
            last_tp=Decimal("3000"),
            last_stance=None,
            event_severity=None,
            tp_change_pct=Decimal("10"),
        )
        is False
    )


def test_thesis_severity_triggers_regardless_of_tp():
    assert (
        auto_cross_check(
            _result(tp=Decimal("3010")),
            last_tp=Decimal("3000"),
            last_stance=None,
            event_severity="thesis",
            tp_change_pct=Decimal("10"),
        )
        is True
    )


def test_prose_cannot_suppress_cross_check_when_tp_moved():
    # The rule reads registered predictions, not prose: a result whose prose says "no
    # cross-check needed" but whose registered TP moved 20% still triggers.
    result = {
        "narrative": "no cross-check needed, nothing material",
        "predictions": [{"kind": "target_price", "value": 3600}],
    }
    assert (
        auto_cross_check(
            result,
            last_tp=Decimal("3000"),
            last_stance=None,
            event_severity=None,
            tp_change_pct=Decimal("10"),
        )
        is True
    )
