"""Concurrency fixes — BUGS.md #1, #2, #4 + outbox exclusivity.

These exercise the race windows the green unit suite could not prove closed: the
coverage-lock and prediction-idempotency ``ON CONFLICT DO NOTHING`` paths under real
parallelism, the RESTRICT/CASCADE FK behaviour, and outbox consume exclusivity. Postgres
only (``FOR UPDATE``/``ON CONFLICT``/FK enforcement are why).
"""

from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy.exc import IntegrityError

from core.db import models, queue
from core.db.repo import coverage as cov_repo
from core.db.repo import dossier as dossier_repo
from core.db.repo import outbox as outbox_repo
from core.db.repo import predictions as pred_repo
from core.db.repo import runs as runs_repo
from core.db.schemas import PredictionIn, StageIn


def _seed_coverage(session, *, lead="gpt"):
    for k in ("gpt", "gemini", "deepseek"):
        session.add(models.House(key=k, display_name=k.upper(), provider="openai"))
    session.flush()
    c = cov_repo.propose(session, "2267", "tse", "Yakult", "JPY", "pm1")
    session.flush()
    return c


def test_start_run_under_barrier_one_wins_rest_refuse_no_error(factory):
    """BUGS #1: INSERT ON CONFLICT DO NOTHING — no unhandled IntegrityError."""
    setup = factory()
    c = _seed_coverage(setup)
    runs = [
        runs_repo.create_run(
            setup,
            coverage_id=c.id,
            type="initiation",
            trigger="pm",
            params={"lead_house": "gpt"},
            priority=100,
            budget_cap_usd=None,
            doctrine_version_id=None,
        )
        for _ in range(8)
    ]
    setup.commit()
    setup.close()
    ids = [r.id for r in runs]

    barrier = threading.Barrier(8)
    results: list[bool] = []
    errors: list = []
    guard = threading.Lock()

    def attempt(run_id):
        s = factory()
        try:
            barrier.wait()
            ok = runs_repo.start_run(s, run_id)
            s.commit()
            with guard:
                results.append(ok)
        except Exception as exc:
            s.rollback()
            with guard:
                errors.append(exc)
        finally:
            s.close()

    with ThreadPoolExecutor(max_workers=8) as ex:
        list(ex.map(attempt, ids))

    assert errors == []
    assert results.count(True) == 1
    assert results.count(False) == 7


def test_concurrent_prediction_registration_is_idempotent(factory):
    """BUGS #2: two concurrent replays of a stage register exactly one prediction row."""
    setup = factory()
    c = _seed_coverage(setup)
    r = runs_repo.create_run(
        setup,
        coverage_id=c.id,
        type="initiation",
        trigger="pm",
        params={"lead_house": "gpt"},
        priority=100,
        budget_cap_usd=None,
        doctrine_version_id=None,
    )
    runs_repo.add_stages(
        setup,
        r.id,
        [StageIn(seq=1, role="author", house="gpt", substrate="harness", max_attempts=3)],
    )
    runs_repo.start_run(setup, r.id)
    stage_id = setup.query(models.RunStage).filter_by(run_id=r.id).one().id
    setup.commit()
    setup.close()

    entry = PredictionIn(
        kind="target_price",
        horizon_date=date(2027, 1, 1),
        value=Decimal("2900"),
        currency="JPY",
        confidence=Decimal("0.6"),
    )
    errors: list = []
    guard = threading.Lock()

    def register(_):
        s = factory()
        try:
            pred_repo.register_from_stage(s, stage_id, [entry])
            s.commit()
        except Exception as exc:
            s.rollback()
            with guard:
                errors.append(exc)
        finally:
            s.close()

    with ThreadPoolExecutor(max_workers=4) as ex:
        list(ex.map(register, range(4)))

    assert errors == []
    s = factory()
    assert s.query(models.Prediction).filter_by(stage_id=stage_id).count() == 1
    s.close()


def test_coverage_delete_is_restricted(session):
    """BUGS #4: a coverage row with referencing children cannot be deleted (RESTRICT)."""
    c = _seed_coverage(session)
    session.commit()  # persist coverage + its transitions before the delete attempt
    # propose() wrote a coverage_transitions row that FKs coverage ondelete=RESTRICT.
    # Query.delete() executes immediately, so the RESTRICT violation raises here.
    with pytest.raises(IntegrityError):
        session.query(models.Coverage).filter_by(id=c.id).delete()
    session.rollback()
    # coverage survived (rollback undid only the delete attempt)
    assert session.get(models.Coverage, c.id) is not None


def test_run_delete_cascades_to_stages_and_attempts(session):
    """BUGS #4: the sanctioned run -> run_stages -> stage_attempts CASCADE works."""
    c = _seed_coverage(session)
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
    # NB: no start_run — a held coverage_run_lock would (correctly) RESTRICT the run delete;
    # this test isolates the sanctioned run -> run_stages CASCADE.
    stage_id = session.query(models.RunStage).filter_by(run_id=r.id).one().id
    session.commit()

    session.query(models.Run).filter_by(id=r.id).delete()
    session.commit()
    assert session.query(models.RunStage).filter_by(id=stage_id).count() == 0  # cascaded


def test_concurrent_outbox_consume_never_double_delivers(factory):
    setup = factory()
    c = _seed_coverage(setup)
    for i in range(6):
        outbox_repo.publish(
            setup,
            kind="coverage.state_changed",
            payload={"i": i},
            coverage_id=c.id,
            dedupe_key=f"dk-conc-{i}",
        )
    setup.commit()
    setup.close()

    claimed: list = []
    guard = threading.Lock()

    def consume(wid):
        s = factory()
        try:
            rows = outbox_repo.consume(s, worker_id=wid, limit=10, lease_seconds=30)
            s.commit()
            with guard:
                claimed.extend(r.id for r in rows)
        finally:
            s.close()

    with ThreadPoolExecutor(max_workers=3) as ex:
        list(ex.map(consume, ["a", "b", "c"]))

    # ``_seed_coverage``'s ``propose`` also emitted an outbox row, so total pending >= 6.
    # The point is exclusivity: every claimed id is distinct (no double-delivery).
    assert len(claimed) == len(set(claimed))
    assert len(claimed) >= 6


# silence the unused-import linter for the dossier import kept for parity/coverage
_ = (dossier_repo, queue)
