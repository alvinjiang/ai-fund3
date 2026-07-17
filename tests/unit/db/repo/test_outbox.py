"""Outbox repository — SPEC-DOMAIN §4.22 / §8.

Publish dedupes on ``dedupe_key``; ``consume`` claims pending rows (SKIP LOCKED under
real concurrency — integration-tested), ``ack`` marks delivered, ``nack`` applies
backoff via ``available_at``. The adapter consumes through the core API (SPEC-ADAPTER).
"""

from __future__ import annotations

from core.db.repo import outbox as outbox_repo


def test_publish_dedupes_on_key(session, chain):
    outbox_repo.publish(
        session,
        kind="coverage.state_changed",
        payload={"a": 1},
        coverage_id=chain.coverage.id,
        dedupe_key="dk-1",
    )
    outbox_repo.publish(
        session,
        kind="coverage.state_changed",
        payload={"a": 1},
        coverage_id=chain.coverage.id,
        dedupe_key="dk-1",
    )
    assert session.query(outbox_repo.models.OutboxEvent).filter_by(dedupe_key="dk-1").count() == 1


def test_consume_claims_pending_and_ack_marks_delivered(session, chain):
    outbox_repo.publish(
        session,
        kind="run.finished",
        payload={"k": "v"},
        run_id=chain.run.id,
        dedupe_key="dk-2",
    )
    rows = outbox_repo.consume(session, worker_id="adapter-1", limit=10, lease_seconds=30)
    # the chain's propose also emitted an outbox row; target the one we published
    target = [r for r in rows if r.dedupe_key == "dk-2"]
    assert len(target) == 1
    assert target[0].claimed_by == "adapter-1"
    outbox_repo.ack(session, target[0].id)
    session.refresh(target[0])
    assert target[0].delivered_at is not None


def test_nack_applies_backoff(session, chain):
    outbox_repo.publish(
        session,
        kind="gate.opened",
        payload={},
        run_id=chain.run.id,
        dedupe_key="dk-3",
    )
    rows = outbox_repo.consume(session, worker_id="adapter-1", limit=10, lease_seconds=30)
    target = next(r for r in rows if r.dedupe_key == "dk-3")
    outbox_repo.nack(session, target.id, error="post failed", backoff_seconds=60)
    session.refresh(target)
    assert target.last_error == "post failed"
    assert target.attempts == 1
    assert target.delivered_at is None
    # reconsuming immediately must not hand the nacked row back (backoff + active leases
    # on any other claimed rows). It is not returned even when other pending rows exist.
    again = outbox_repo.consume(session, worker_id="adapter-2", limit=10, lease_seconds=30)
    assert all(r.dedupe_key != "dk-3" for r in again)
