"""Contract tests for the domain enums (SPEC-DOMAIN §3, amended by §12).

These lock the string values that are the single source of truth for the DB CHECK
constraints. A drift here is caught at import; a missing/extra member is a failure.
"""

from core.domain import enums


def test_coverage_state_values():
    assert {s.value for s in enums.CoverageState} == {
        "proposed",
        "initiating",
        "decision_pending",
        "active",
        "watch",
        "rejected",
        "exited",
        "failed",
    }


def test_run_type_values():
    assert {t.value for t in enums.RunType} == {
        "initiation",
        "deep_review",
        "event_analysis",
        "monitor_tick",
        "pm_query",
        "lead_review",
        "distillation",
    }


def test_run_status_values():
    assert {s.value for s in enums.RunStatus} == {
        "queued",
        "running",
        "waiting_pm",
        "succeeded",
        "failed",
        "cancelled",
    }


def test_stage_status_values():
    assert {s.value for s in enums.StageStatus} == {
        "queued",
        "running",
        "succeeded",
        "failed",
        "skipped",
    }


def test_substrate_values():
    assert {s.value for s in enums.Substrate} == {"harness", "api"}


def test_stage_role_values_includes_pm_query_amendment():
    # §12 amendment: StageRole gains PM_QUERY (SPEC-CORE §3.2/§10).
    assert {r.value for r in enums.StageRole} == {
        "author",
        "verifier",
        "data_checker",
        "finalizer",
        "monitor",
        "cross_check",
        "lead_review",
        "distiller",
        "pm_query",
    }


def test_prediction_kind_values():
    assert {k.value for k in enums.PredictionKind} == {
        "target_price",
        "entry_point",
        "scenario",
        "event_forecast",
        "stance",
    }


def test_prediction_status_values():
    assert {s.value for s in enums.PredictionStatus} == {
        "open",
        "hit",
        "miss",
        "expired",
        "superseded",
    }


def test_event_kind_values():
    assert {k.value for k in enums.EventKind} == {
        "news",
        "filing",
        "earnings",
        "price_level",
        "tripwire",
        "pm_instruction",
        "risk",
    }


def test_severity_is_single_vocabulary():
    # §3: Severity is the single severity vocabulary (SPEC-MONITORING maps materiality
    # onto it; the DB never stores materiality as a state).
    assert {s.value for s in enums.Severity} == {"thesis", "valuation", "info"}


def test_gate_kind_values():
    assert {g.value for g in enums.GateKind} == {
        "initiation_decision",
        "lead_change",
        "doctrine_amendment",
        "budget_cap",
    }


def test_actor_type_values():
    assert {a.value for a in enums.ActorType} == {"pm", "run", "scheduler", "system"}


def test_enums_are_strenums():
    # StrEnum: member string-equals its value, so CHECK values and Python compare cleanly.
    assert enums.CoverageState.ACTIVE == "active"
    assert enums.RunStatus.WAITING_PM == "waiting_pm"
