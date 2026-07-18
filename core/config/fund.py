"""fund.yaml model (SPEC-CORE §2.3).

Every number is operator policy, not a code default. The planner/orchestrator read the
run-type policies (``verify_count``, ``max_attempts``, ``budget_cap_usd``,
``stage_timeout_s``), the auto-cross-check thresholds, and the cadence/queue knobs.
"""

from __future__ import annotations

from decimal import Decimal

import yaml
from pydantic import BaseModel, ConfigDict, Field


class RunPolicy(BaseModel):
    model_config = ConfigDict(extra="ignore")
    # max_attempts is required: SPEC §4.8 ("from config at creation") + AGENTS.md's
    # no-silent-fallback rule — fund.yaml must state it for every run type, enforced at load.
    max_attempts: int
    budget_cap_usd: Decimal = Decimal("0")
    stage_timeout_s: int = 7200
    verify_count: int = 2  # initiation only
    include_data_checker: bool = False  # initiation only


class AutoCrossCheck(BaseModel):
    tp_change_pct: Decimal = Decimal("10")
    stance_change: bool = True
    thesis_tripwire: bool = True


class Escalation(BaseModel):
    auto_cross_check: AutoCrossCheck = Field(default_factory=AutoCrossCheck)


class MonitorCfg(BaseModel):
    house: str = "gpt"
    digest_min_severity: str = "info"
    digest_max_items: int = 10


class QueueCfg(BaseModel):
    lease_seconds: int = 300
    backoff_base_s: int = 30
    backoff_max_s: int = 3600
    backoff_jitter: float = 0.2


class OrchestratorCfg(BaseModel):
    tick_seconds: int = 5


class FundConfig(BaseModel):
    model_config = ConfigDict(extra="ignore")
    books: list[str] = Field(default_factory=lambda: ["main"])
    timezone: str = "UTC"
    exchanges: dict = Field(default_factory=dict)
    cadence: dict = Field(default_factory=dict)
    runs: dict[str, RunPolicy]
    monitor: MonitorCfg = Field(default_factory=MonitorCfg)
    escalation: Escalation = Field(default_factory=Escalation)
    queue: QueueCfg = Field(default_factory=QueueCfg)
    orchestrator: OrchestratorCfg = Field(default_factory=OrchestratorCfg)
    scheduler: dict = Field(default_factory=dict)
    retention: dict = Field(default_factory=dict)

    def policy(self, run_type: str) -> RunPolicy:
        """Run-type policy; raises KeyError (→ checkconfig failure) if missing."""
        if run_type not in self.runs:
            raise KeyError(f"no run policy configured for {run_type!r}")
        return self.runs[run_type]


def load_fund(yaml_text: str) -> FundConfig:
    return FundConfig.model_validate(yaml.safe_load(yaml_text))
