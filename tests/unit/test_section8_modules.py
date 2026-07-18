"""§8 organizational modules — fires-tests proving each works."""

from __future__ import annotations

from decimal import Decimal

from cli import format as fmt
from core.config.fund import FundConfig
from core.orchestrator import budgets, gates
from core.scheduler import calendar


def test_run_over_budget():
    from uuid import uuid4

    from core.db import models

    run = models.Run(
        id=uuid4(),
        type="initiation",
        status="running",
        trigger="pm",
        mutates_dossier=True,
        params={},
        budget_cap_usd=Decimal("10"),
        cost_usd=Decimal("15"),
    )
    assert budgets.run_over_budget(run) is True
    run.cost_usd = Decimal("5")
    assert budgets.run_over_budget(run) is False


def test_terminal_gate_mapping():
    assert gates.terminal_gate("initiation") == (
        "initiation_decision",
        ["active", "watch", "reject"],
    )
    assert gates.terminal_gate("lead_review") == ("lead_change", ["approve", "keep"])
    assert gates.terminal_gate("distillation") == ("doctrine_amendment", ["merge", "reject"])
    assert gates.terminal_gate("monitor_tick") is None


def test_exchange_sessions():
    from core.config.fund import RunPolicy

    fund = FundConfig(
        runs={"monitor_tick": RunPolicy(max_attempts=2)},
        exchanges={"tse": {"sessions": ["pre:23:00", "post:06:10"]}},
    )
    assert calendar.exchange_sessions(fund, "tse") == ["pre:23:00", "post:06:10"]
    assert calendar.exchange_sessions(fund, "nyse") == []
    assert calendar.all_exchanges(fund) == ["tse"]


def test_format_table():
    out = fmt.table(
        [{"name": "gpt", "state": "active"}, {"name": "claude", "state": "meta"}],
        ["name", "state"],
    )
    assert "gpt" in out and "claude" in out and "active" in out
    assert fmt.table([], ["x"]) == "(no rows)"
