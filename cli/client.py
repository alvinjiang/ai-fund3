"""Thin httpx client over the core API (SPEC-CORE §6).

The CLI is a pure client — no direct DB access — which proves the API is complete. A
fresh ``Idempotency-Key`` is minted per call (the operator rarely retries by hand; program
callers pass their own). All errors surface as ``CoreError`` with the structured body.
"""

from __future__ import annotations

from typing import Any
from uuid import uuid4


class CoreError(Exception):
    def __init__(self, status: int, body: Any):
        self.status = status
        self.body = body
        super().__init__(f"core API error {status}: {body}")


class CoreClient:
    def __init__(self, base_url: str, token: str, pm_user: str, *, transport: Any = None):
        import httpx

        self._http = httpx.Client(
            base_url=base_url.rstrip("/"),
            headers={"Authorization": f"Bearer {token}", "X-PM-User": pm_user},
            transport=transport,
        )

    def close(self) -> None:
        self._http.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def _call(
        self,
        method: str,
        path: str,
        *,
        json: dict | None = None,
        params: dict | None = None,
        idem: str | None = None,
    ) -> Any:
        headers = {"Idempotency-Key": idem or uuid4().hex}
        r = self._http.request(method, path, json=json, params=params, headers=headers)
        if r.status_code >= 400:
            raise CoreError(r.status_code, r.json())
        return r.json() if r.content else None

    # --- coverage ---
    def propose(self, ticker: str, exchange: str, name: str, currency: str, **kw) -> dict:
        body = {"ticker": ticker, "exchange": exchange, "name": name, "currency": currency, **kw}
        return self._call("POST", "/coverage", json=body)

    def coverage_list(self, state: str | None = None) -> list:
        return self._call("GET", "/coverage", params={"state": state} if state else None)

    def coverage_show(self, slug: str) -> dict:
        return self._call("GET", f"/coverage/{slug}")

    def initiate(self, slug: str, lead: str | None = None, verify_count: int | None = None) -> dict:
        body: dict[str, Any] = {}
        if lead:
            body["lead"] = lead
        if verify_count is not None:
            body["verify_count"] = verify_count
        return self._call("POST", f"/coverage/{slug}/initiate", json=body)

    def decide(self, slug: str, decision: str, notes: str | None = None) -> dict:
        return self._call(
            "POST", f"/coverage/{slug}/decide", json={"decision": decision, "notes": notes}
        )

    def promote(self, slug: str) -> dict:
        return self._call("POST", f"/coverage/{slug}/promote", json={})

    def demote(self, slug: str) -> dict:
        return self._call("POST", f"/coverage/{slug}/demote", json={})

    def exit_coverage(self, slug: str, force: bool = False, notes: str | None = None) -> dict:
        return self._call("POST", f"/coverage/{slug}/exit", json={"force": force, "notes": notes})

    def re_propose(self, slug: str) -> dict:
        return self._call("POST", f"/coverage/{slug}/re-propose", json={})

    def set_lead(self, slug: str, house: str, rationale: str | None = None) -> dict:
        return self._call(
            "POST", f"/coverage/{slug}/lead", json={"house": house, "rationale": rationale}
        )

    def set_levels(self, slug: str, levels: list[dict]) -> dict:
        return self._call("POST", f"/coverage/{slug}/levels", json={"levels": levels})

    # --- runs ---
    def new_run(
        self, run_type: str, coverage: str | None = None, params: dict | None = None
    ) -> dict:
        return self._call(
            "POST", "/runs", json={"type": run_type, "coverage": coverage, "params": params or {}}
        )

    def runs_list(self, status: str | None = None) -> list:
        return self._call("GET", "/runs", params={"status": status} if status else None)

    def cancel_run(self, run_id: str, reason: str | None = None) -> dict:
        return self._call("POST", f"/runs/{run_id}/cancel", json={"notes": reason})

    # --- gates ---
    def gates_list(self, state: str = "open") -> list:
        return self._call("GET", "/gates", params={"state": state})

    def answer_gate(self, gate_id: str, answer: str, notes: str | None = None) -> dict:
        return self._call(
            "POST", f"/gates/{gate_id}/answer", json={"answer": answer, "notes": notes}
        )

    # --- health ---
    def health(self) -> dict:
        return self._call("GET", "/health")

    # --- read models (BUGS #1) ---
    def run_show(self, run_id: str) -> dict:
        return self._call("GET", f"/runs/{run_id}")

    def run_retry(self, run_id: str) -> dict:
        return self._call("POST", f"/runs/{run_id}/retry", json={})

    def dossier_show(self, slug: str) -> dict:
        return self._call("GET", f"/coverage/{slug}/dossier")

    def predictions_list(self, **kw) -> list:
        return self._call("GET", "/predictions", params={k: v for k, v in kw.items() if v})

    def events_list(self, **kw) -> list:
        return self._call("GET", "/events", params={k: v for k, v in kw.items() if v})

    def events_rate(self, event_id: str, rating: int) -> dict:
        return self._call("POST", f"/events/{event_id}/rate", json={"rating": rating})

    def cost(self, by: str = "house") -> list:
        return self._call("GET", "/costs", params={"by": by})

    def query(self, coverage: str, question: str) -> dict:
        return self._call("POST", "/queries", json={"coverage": coverage, "question": question})

    def config_check(self) -> dict:
        return self._call("GET", "/config/check")
