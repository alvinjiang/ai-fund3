"""Shared house -> coverage -> run -> stage fixture for repo tests."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from core.db import models
from core.db.repo import coverage as cov_repo
from core.db.repo import runs as runs_repo
from core.db.schemas import StageIn


@pytest.fixture()
def chain(session):
    for k in ("gpt", "gemini", "deepseek", "glm", "qwen"):
        session.add(models.House(key=k, display_name=k.upper(), provider="openai"))
    session.flush()
    c = cov_repo.propose(session, "2267", "tse", "Yakult", "JPY", "pm1")
    session.flush()
    r = runs_repo.create_run(
        session,
        coverage_id=c.id,
        type="initiation",
        trigger="pm",
        params={"lead_house": "gpt"},
        priority=100,
        budget_cap_usd=None,
        doctrine_version_id=None,
    )
    runs_repo.add_stages(
        session,
        r.id,
        [StageIn(seq=1, role="author", house="gpt", substrate="harness", max_attempts=3)],
    )
    runs_repo.start_run(session, r.id)
    stage = session.query(models.RunStage).filter_by(run_id=r.id).one()
    return SimpleNamespace(coverage=c, run=r, stage=stage)
