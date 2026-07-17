"""API dependencies: per-request session, PM authorization, idempotency (SPEC-CORE §5.2).

Idempotency: every mutating route takes an ``Idempotency-Key`` header. A replay returns
the stored response verbatim and writes no second ``audit_log`` row; a same-key/different-
body call is a 409. The body hash is computed once in middleware (Starlette caches the
body so handlers still parse it) and stored alongside the response.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from fastapi import Depends, Header, Request
from sqlalchemy.orm import Session

from core.api.errors import ApiError
from core.db import models


def get_session(request: Request):
    """One Session per request; commit on success, rollback on error."""
    factory = request.app.state.session_factory
    session = factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def _bearer_token(authorization: str | None) -> str | None:
    if not authorization or not authorization.lower().startswith("bearer "):
        return None
    return authorization.split(" ", 1)[1].strip()


def require_pm(
    request: Request,
    authorization: str | None = Header(default=None),
    x_pm_user: str | None = Header(default=None),
) -> str:
    """Bearer token + the caller's PM identity in the allowlist. Returns the PM user id."""
    token = _bearer_token(authorization)
    if not token or token != request.app.state.core_api_token:
        raise ApiError("unauthorized", "valid bearer token required", status=401)
    if not x_pm_user or x_pm_user not in request.app.state.pm_user_ids:
        raise ApiError("forbidden", "caller is not in pm_user_ids", status=403)
    return x_pm_user


class Idempotency:
    def __init__(
        self, key: str, body_hash: str, replay: dict | None, record: models.IdempotencyKey | None
    ):
        self.key = key
        self.body_hash = body_hash
        self.replay = replay  # stored response body to return verbatim, or None to run
        self.record = record

    def store(
        self, session: Session, response: dict, status_code: int, route: str, actor_id: str
    ) -> None:
        if self.record is not None:
            return
        session.add(
            models.IdempotencyKey(
                key=self.key,
                route=route,
                actor_id=actor_id,
                response={
                    "body": response,
                    "status_code": status_code,
                    "body_hash": self.body_hash,
                },
                status_code=status_code,
                expires_at=datetime.max.replace(tzinfo=UTC),
            )
        )
        session.flush()


def idempotency(
    request: Request,
    session: Session = Depends(get_session),
    idempotency_key: str | None = Header(default=None),
) -> Idempotency:
    if not idempotency_key:
        raise ApiError("missing_idempotency_key", "Idempotency-Key header required", status=400)
    body_hash = getattr(request.state, "body_hash", "") or ""
    existing = session.get(models.IdempotencyKey, idempotency_key)
    if existing is not None and existing.response:
        if existing.response.get("body_hash") != body_hash:
            raise ApiError(
                "idempotency_conflict", "same Idempotency-Key, different body", status=409
            )
        return Idempotency(idempotency_key, body_hash, existing.response.get("body"), existing)
    return Idempotency(idempotency_key, body_hash, None, None)


def write_audit(
    session: Session,
    *,
    action: str,
    actor_id: str,
    target: str | None,
    before: Any = None,
    after: Any = None,
    request_id: str | None = None,
) -> None:
    session.add(
        models.AuditLog(
            action=action,
            actor_type="pm",
            actor_id=actor_id,
            target=target,
            before_state=before,
            after_state=after,
            request_id=request_id,
        )
    )
    session.flush()
