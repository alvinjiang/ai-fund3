"""Core API — SPEC-CORE §5 / §9.4 (auth, idempotency, error envelope, health).

Driven in-process via fastapi.testclient.TestClient (no real network). Marked
``allow_network`` because TestClient's anyio bridge uses a socketpair internally — that is
not an outbound connection, and the autouse guard would otherwise break it.
"""

from __future__ import annotations

import pytest

from tests.unit.api.conftest import _h

pytestmark = pytest.mark.allow_network


def _hdr(idem: str | None = None) -> dict:
    h = _h()
    if idem is not None:
        h["Idempotency-Key"] = idem
    return h


_PROP = {"ticker": "2267", "exchange": "tse", "name": "Yakult", "currency": "JPY"}


def test_propose_without_pm_identity_is_403(api):
    client, _ = api
    r = client.post(
        "/coverage",
        json=_PROP,
        headers={"Authorization": "Bearer tok-secret", "Idempotency-Key": "k1"},
    )
    assert r.status_code == 403
    assert r.json()["error"]["code"] == "forbidden"


def test_propose_without_idempotency_key_is_400(api):
    client, _ = api
    r = client.post(
        "/coverage", json=_PROP, headers={"Authorization": "Bearer tok-secret", "X-PM-User": "pm1"}
    )
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "missing_idempotency_key"


def test_propose_replay_returns_same_response_with_one_audit(api):
    from core.db import models

    client, factory = api
    r1 = client.post("/coverage", json=_PROP, headers=_hdr("k-replay"))
    assert r1.status_code in (200, 201)
    first = r1.json()
    assert first["state"] == "proposed"
    assert first["dossier_slug"] == "tse_2267"

    with factory() as s:
        n_after_first = s.query(models.AuditLog).filter_by(action="coverage.propose").count()
    assert n_after_first >= 1  # the transition + the API request each audit the mutation

    r2 = client.post("/coverage", json=_PROP, headers=_hdr("k-replay"))
    assert r2.json() == first  # identical replay

    # the replay must write no new audit row (idempotent)
    with factory() as s:
        n_after_replay = s.query(models.AuditLog).filter_by(action="coverage.propose").count()
    assert n_after_replay == n_after_first


def test_same_key_different_body_is_409(api):
    client, _ = api
    client.post("/coverage", json=_PROP, headers=_hdr("k-conflict"))
    r = client.post(
        "/coverage",
        json={"ticker": "9999", "exchange": "tse", "name": "Other", "currency": "JPY"},
        headers=_hdr("k-conflict"),
    )
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "idempotency_conflict"


def test_illegal_transition_is_409_structured(api):
    client, _ = api
    client.post("/coverage", json=_PROP, headers=_hdr("k-illegal"))
    r = client.post("/coverage/tse_2267/promote", json={"notes": "x"}, headers=_hdr("k-promote"))
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "conflict"


def test_unknown_slug_is_404(api):
    client, _ = api
    r = client.get("/coverage/no_such_slug")
    assert r.status_code == 404


def test_health(api):
    client, _ = api
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"
