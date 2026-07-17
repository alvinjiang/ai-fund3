"""FastAPI app factory (SPEC-CORE §5).

Bound to 127.0.0.1 in production. Auth = bearer ``core_api_token`` + PM allowlist on
mutations; every mutation carries ``Idempotency-Key`` and writes ``audit_log``. Errors are
structured and always returned to the caller. Stage execution is out of scope (phase 2);
in-process tests drive the app through ``httpx.ASGITransport`` (no port, no socket).
"""

from __future__ import annotations

import hashlib
from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from sqlalchemy.orm import sessionmaker

from core.api.errors import ApiError, error_response
from core.config.fund import FundConfig


def create_app(
    *,
    core_api_token: str,
    pm_user_ids: list[str],
    session_factory: sessionmaker,
    fund: FundConfig,
    meta_houses: set[str] | None = None,
    meta_house: str | None = None,
) -> FastAPI:
    app = FastAPI(title="ai-fund core", docs_url=None, redoc_url=None)
    app.state.core_api_token = core_api_token
    app.state.pm_user_ids = list(pm_user_ids)
    app.state.session_factory = session_factory
    app.state.fund = fund
    app.state.meta_houses = set(meta_houses or set())
    app.state.meta_house = meta_house

    @app.middleware("http")
    async def _middleware(request: Request, call_next):
        request.state.request_id = uuid4().hex
        request.state.body_hash = ""
        if request.method in ("POST", "PUT", "PATCH"):
            raw = await request.body()
            request.state.body_hash = hashlib.sha256(raw).hexdigest()
        response: JSONResponse = await call_next(request)
        response.headers["X-Request-Id"] = request.state.request_id
        return response

    @app.exception_handler(ApiError)
    async def _api_error(request: Request, exc: ApiError):
        return error_response(
            exc.code, exc.message, getattr(request.state, "request_id", ""), exc.status, exc.details
        )

    @app.exception_handler(Exception)
    async def _unhandled(_: Request, exc: Exception):
        return error_response("internal_error", "internal error", "", 500)

    from core.api import routes as _routes  # local import to avoid import cycle

    app.include_router(_routes.router)
    return app
