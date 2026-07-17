"""Structured errors (SPEC-CORE §5).

Every 4xx/5xx carries ``{"error": {"code", "message", "request_id"}}`` and is returned
to the caller (fixes v2's silent slash-command failures — the adapter surfaces these
in-channel). ``ApiError`` is the in-handler way to raise one; the exception handlers wrap
unhandled exceptions into a 500 with a redacted traceback (never a secret).
"""

from __future__ import annotations

from typing import Any

from fastapi.responses import JSONResponse


class ApiError(Exception):
    def __init__(self, code: str, message: str, status: int = 400, details: Any = None):
        self.code = code
        self.message = message
        self.status = status
        self.details = details
        super().__init__(message)


def error_body(code: str, message: str, request_id: str, details: Any = None) -> dict[str, Any]:
    body: dict[str, Any] = {"error": {"code": code, "message": message, "request_id": request_id}}
    if details is not None:
        body["error"]["details"] = details
    return body


def error_response(
    code: str, message: str, request_id: str, status: int, details: Any = None
) -> JSONResponse:
    return JSONResponse(error_body(code, message, request_id, details), status_code=status)
