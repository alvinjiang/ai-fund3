"""CLI — route/command parity (SPEC-CORE §9.4) + CoreClient behaviour.

Parity is total in both directions: every FastAPI route has a ``fund`` command, and the
command names are distinct. The client is exercised against an ``httpx.MockTransport``
(no socket).
"""

from __future__ import annotations

import httpx
import pytest

from cli.client import CoreClient, CoreError
from cli.main import ROUTE_COMMAND_MAP


def test_every_api_route_maps_to_a_distinct_cli_command():
    from core.api.routes import router

    routes: set[tuple[str, str]] = set()
    for r in router.routes:
        methods = getattr(r, "methods", None)
        if not methods:
            continue
        for m in methods:
            routes.add((m, r.path))

    assert routes == set(ROUTE_COMMAND_MAP.keys())  # total in both directions
    # distinct command names (no two routes share a command)
    assert len(set(ROUTE_COMMAND_MAP.values())) == len(ROUTE_COMMAND_MAP)


def test_core_client_propose_sends_bearer_pm_user_and_idempotency_key():
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["auth"] = request.headers.get("authorization")
        seen["pm"] = request.headers.get("x-pm-user")
        seen["idem"] = request.headers.get("idempotency-key")
        return httpx.Response(
            201, json={"id": "x", "state": "proposed", "dossier_slug": "tse_2267"}
        )

    transport = httpx.MockTransport(handler)
    with CoreClient("http://test", "tok-secret", "pm1", transport=transport) as c:
        res = c.propose("2267", "tse", "Yakult", "JPY")
    assert res["state"] == "proposed"
    assert seen["auth"] == "Bearer tok-secret"
    assert seen["pm"] == "pm1"
    assert seen["idem"]  # a fresh Idempotency-Key was minted and sent


def test_core_client_raises_structured_error_on_4xx(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            409, json={"error": {"code": "conflict", "message": "no", "request_id": "r"}}
        )

    transport = httpx.MockTransport(handler)
    with (
        CoreClient("http://test", "tok", "pm1", transport=transport) as c,
        pytest.raises(CoreError) as exc,
    ):
        c.promote("tse_2267")
    assert exc.value.status == 409
    assert exc.value.body["error"]["code"] == "conflict"
