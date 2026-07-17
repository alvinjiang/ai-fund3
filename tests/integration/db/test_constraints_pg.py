"""Postgres-only constraints — SPEC-DOMAIN §9.3.

Partial unique index (at most one ``doctrine_versions.is_current``), numeric fidelity
(a Price with 6 dp survives insert/select exactly — the deterministic-money-path guard),
and per-ticker coverage lock under concurrent start_run.
"""

from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy.exc import IntegrityError

from core.db import models
from core.db.repo import coverage as cov_repo
from core.db.repo import runs as runs_repo
from core.db.schemas import StageIn


def test_partial_unique_index_at_most_one_current_doctrine(session):
    session.add(models.DoctrineVersion(commit_sha="aaa", label="v1", is_current=True))
    session.commit()
    session.add(models.DoctrineVersion(commit_sha="bbb", label="v2", is_current=True))
    with pytest.raises(IntegrityError):
        session.commit()
    session.rollback()
    # two non-current versions are allowed
    session.add(models.DoctrineVersion(commit_sha="ccc", label="v0a", is_current=False))
    session.add(models.DoctrineVersion(commit_sha="ddd", label="v0b", is_current=False))
    session.commit()


def test_prediction_price_six_dp_round_trips_exactly(session):
    for k in ("gpt",):
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
        session, r.id, [StageIn(seq=1, role="author", house="gpt", substrate="harness")]
    )
    runs_repo.start_run(session, r.id)
    stage = session.query(models.RunStage).filter_by(run_id=r.id).one()
    p = models.Prediction(
        coverage_id=c.id,
        run_id=r.id,
        stage_id=stage.id,
        house="gpt",
        kind="target_price",
        value=Decimal("123.456789"),
        currency="JPY",
        horizon_date=date(2027, 1, 1),
        confidence=Decimal("0.65"),
        idempotency_key="pk1",
    )
    session.add(p)
    session.commit()
    session.expire_all()
    refreshed = session.get(models.Prediction, p.id)
    assert refreshed.value == Decimal("123.456789")  # 6 dp, no float drift


def test_concurrent_start_run_only_one_acquires_lock(factory):
    setup = factory()
    for k in ("gpt",):
        setup.add(models.House(key=k, display_name=k.upper(), provider="openai"))
    setup.flush()
    c = cov_repo.propose(setup, "2267", "tse", "Yakult", "JPY", "pm1")
    setup.flush()
    r1 = runs_repo.create_run(
        setup,
        coverage_id=c.id,
        type="initiation",
        trigger="pm",
        params={"lead_house": "gpt"},
        priority=100,
        budget_cap_usd=None,
        doctrine_version_id=None,
    )
    r2 = runs_repo.create_run(
        setup,
        coverage_id=c.id,
        type="initiation",
        trigger="pm",
        params={"lead_house": "gpt"},
        priority=100,
        budget_cap_usd=None,
        doctrine_version_id=None,
    )
    setup.commit()
    setup.close()
    r1_id, r2_id = r1.id, r2.id

    results: list[bool] = []
    guard = threading.Lock()

    def attempt(run_id):
        s = factory()
        try:
            ok = runs_repo.start_run(s, run_id)
            s.commit()
            with guard:
                results.append(ok)
        finally:
            s.close()

    with ThreadPoolExecutor(max_workers=2) as ex:
        list(ex.map(attempt, [r1_id, r2_id]))

    assert results.count(True) == 1  # exactly one mutating run acquired the lock
    assert results.count(False) == 1
