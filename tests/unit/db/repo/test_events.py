"""Events repository — SPEC-DOMAIN §4.16 / §8 / §9.2.

``record_event`` is idempotent via ``dedupe_key`` (unique constraint) so a re-run
monitor tick cannot spawn a second event_analysis. Returns None on the dedupe hit.
"""

from __future__ import annotations

from datetime import UTC, datetime

from core.db.repo import events as events_repo

_OCCURRED = datetime(2026, 1, 2, tzinfo=UTC)


def test_record_event_inserts(session, chain):
    ev = events_repo.record_event(
        session,
        coverage_id=chain.coverage.id,
        kind="price_level",
        severity="info",
        title="entry hit",
        dedupe_key="price_level:c:l:20260102",
        payload={"level_id": "x"},
        occurred_at=_OCCURRED,
    )
    assert ev is not None
    assert ev.action is None


def test_record_event_dedupe_returns_none_and_inserts_nothing(session, chain):
    args = {
        "coverage_id": chain.coverage.id,
        "kind": "tripwire",
        "severity": "thesis",
        "title": "t",
        "dedupe_key": "tripwire:c:t1:20260102",
        "payload": {"tripwire_id": "t1"},
        "occurred_at": _OCCURRED,
    }
    first = events_repo.record_event(session, **args)
    second = events_repo.record_event(session, **args)
    assert first is not None
    assert second is None  # dedupe hit -> no insert


def test_mark_handled_sets_handling_run(session, chain):
    ev = events_repo.record_event(
        session,
        coverage_id=chain.coverage.id,
        kind="tripwire",
        severity="thesis",
        title="t",
        dedupe_key="tripwire:c:t2:20260102",
        payload={},
        occurred_at=_OCCURRED,
        action="event_analysis",
    )
    events_repo.mark_handled(session, ev.id, run_id=chain.run.id)
    session.refresh(ev)
    assert ev.handled_by_run_id == chain.run.id
