"""Postgres-only queue behaviour — SPEC-DOMAIN §6.3 / §9.3.

SKIP LOCKED exclusivity under real concurrency, stage dependency gating, and lease/reaper
on a live Postgres. These cannot be proven on SQLite.
"""

from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor

from core.db import models, queue
from core.db.repo import coverage as cov_repo
from core.db.repo import runs as runs_repo
from core.db.schemas import StageIn
from core.db.types import utc_now
from core.domain.backoff import BackoffPolicy

NO_DELAY = BackoffPolicy(base_s=0, max_s=0, jitter=0.0)


def _seed_run(session, n_stages=5):
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
        [
            StageIn(seq=i, role="author", house="gpt", substrate="harness")
            for i in range(1, n_stages + 1)
        ],
    )
    runs_repo.start_run(session, r.id)
    session.commit()
    return r


def test_concurrent_claim_exclusivity(factory):
    setup = factory()
    _seed_run(setup, n_stages=5)
    setup.close()

    claimed: list = []
    guard = threading.Lock()

    def worker(wid):
        s = factory()
        try:
            # drain until nothing is claimable; SKIP LOCKED guarantees no double-claim
            while True:
                stage = queue.claim_stage(s, wid, lease_seconds=300)
                if stage is None:
                    s.commit()
                    break
                sid = stage.id
                s.commit()
                with guard:
                    claimed.append(sid)
        finally:
            s.close()

    with ThreadPoolExecutor(max_workers=3) as ex:
        list(ex.map(worker, ["w1", "w2", "w3"]))

    assert len(claimed) == 5  # every stage drained
    assert len(set(claimed)) == 5  # each exactly once — no SKIP LOCKED double-claim


def test_dependency_gating(factory):
    s = factory()
    # build a 2-stage run where stage 2 depends on stage 1
    for k in ("gpt",):
        s.add(models.House(key=k, display_name=k.upper(), provider="openai"))
    s.flush()
    c = cov_repo.propose(s, "2267", "tse", "Yakult", "JPY", "pm1")
    s.flush()
    run = runs_repo.create_run(
        s,
        coverage_id=c.id,
        type="initiation",
        trigger="pm",
        params={"lead_house": "gpt"},
        priority=100,
        budget_cap_usd=None,
        doctrine_version_id=None,
    )
    runs_repo.add_stages(
        s,
        run.id,
        [
            StageIn(seq=1, role="author", house="gpt", substrate="harness"),
            StageIn(seq=2, role="verifier", house="gpt", substrate="harness", depends_on_seq=1),
        ],
    )
    runs_repo.start_run(s, run.id)
    s.commit()

    # stage 1 claimable, stage 2 NOT (dependency not satisfied)
    first = queue.claim_stage(s, "w1", lease_seconds=300)
    assert first is not None and first.seq == 1
    s.rollback()  # release stage 1 so we can mark it without claiming

    stage1 = s.query(models.RunStage).filter_by(run_id=run.id, seq=1).one()
    stage1.status = "succeeded"
    s.commit()

    # now stage 2 is claimable
    second = queue.claim_stage(s, "w2", lease_seconds=300)
    assert second is not None and second.seq == 2
    s.rollback()


def test_reaper_requeues_expired_lease(factory):
    s = factory()
    _seed_run(s, n_stages=1)
    stage = queue.claim_stage(s, "w1", lease_seconds=300)
    s.commit()
    # force lease into the past
    s2 = factory()
    row = s2.get(models.RunStage, stage.id)
    row.lease_expires_at = utc_now().replace(microsecond=0)
    s2.commit()
    reaped = queue.reap_expired(s2, utc_now(), backoff=NO_DELAY)
    s2.commit()
    assert stage.id in reaped
    assert s2.get(models.RunStage, stage.id).status == "queued"
