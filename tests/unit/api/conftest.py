"""API test fixture: an in-process FastAPI app driven via httpx.ASGITransport (no socket)."""

from __future__ import annotations

from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

import core.db.models  # noqa: F401
from core.api.app import create_app
from core.config.fund import AutoCrossCheck, Escalation, FundConfig, MonitorCfg, RunPolicy
from core.db import models
from core.db.base import Base


def _fund() -> FundConfig:
    return FundConfig(
        runs={
            "initiation": RunPolicy(verify_count=2, max_attempts=3, budget_cap_usd=Decimal("40")),
            "deep_review": RunPolicy(max_attempts=3, budget_cap_usd=Decimal("20")),
            "event_analysis": RunPolicy(max_attempts=2, budget_cap_usd=Decimal("10")),
            "monitor_tick": RunPolicy(max_attempts=2, budget_cap_usd=Decimal("0.10")),
            "pm_query": RunPolicy(max_attempts=2, budget_cap_usd=Decimal("0.50")),
            "lead_review": RunPolicy(max_attempts=2, budget_cap_usd=Decimal("15")),
            "distillation": RunPolicy(max_attempts=2, budget_cap_usd=Decimal("15")),
        },
        monitor=MonitorCfg(house="gpt"),
        escalation=Escalation(auto_cross_check=AutoCrossCheck(tp_change_pct=Decimal("10"))),
    )


@pytest.fixture()
def api():
    # StaticPool + check_same_thread=False: TestClient runs handlers on a portal thread,
    # so the :memory: DB must be shared across threads (a fresh per-thread DB would be empty).
    eng = create_engine(
        "sqlite+pysqlite:///:memory:",
        future=True,
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(eng)
    factory = sessionmaker(bind=eng, class_=Session, expire_on_commit=False, autoflush=False)
    for k in ("gpt", "gemini", "deepseek", "glm", "qwen"):
        with factory() as s:
            s.add(models.House(key=k, display_name=k.upper(), provider="openai"))
            s.commit()
    app = create_app(
        core_api_token="tok-secret",
        pm_user_ids=["pm1"],
        session_factory=factory,
        fund=_fund(),
    )
    client = TestClient(app)
    yield client, factory
    client.close()


def _h(**extra):
    headers = {"Authorization": "Bearer tok-secret", "X-PM-User": "pm1"}
    headers.update(extra)
    return headers
