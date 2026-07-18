"""Bidirectional spec-coverage parity (BUGS #5 final fix).

Asserts the FULL §5 route list from SPEC-CORE.md: every route is either ``done``
(implemented in the router AND has a CLI command) or ``deferred`` (tracked with a reason).
Catches missing routes, stale deferrals, surprise routes, and missing CLI commands.

This is the meta-guard that makes the design agent's "shape but not working" pattern
structurally harder to repeat: adding a route without a CLI command fails here; removing
a route without updating the spec list fails here; implementing a "deferred" route without
flipping its status fails here.
"""

from __future__ import annotations

# (method, path) -> (status, reason_if_deferred)
# Path parameter names match the router's actual templates.
SPEC_ROUTES: dict[tuple[str, str], tuple[str, str]] = {
    # --- coverage ---
    ("POST", "/coverage"): ("done", ""),
    ("GET", "/coverage"): ("done", ""),
    ("GET", "/coverage/{slug}"): ("done", ""),
    ("POST", "/coverage/{slug}/initiate"): ("done", ""),
    ("POST", "/coverage/{slug}/decide"): ("done", ""),
    ("POST", "/coverage/{slug}/promote"): ("done", ""),
    ("POST", "/coverage/{slug}/demote"): ("done", ""),
    ("POST", "/coverage/{slug}/exit"): ("done", ""),
    ("POST", "/coverage/{slug}/re-propose"): ("done", ""),
    ("POST", "/coverage/{slug}/lead"): ("done", ""),
    ("POST", "/coverage/{slug}/levels"): ("done", ""),
    ("GET", "/coverage/{slug}/dossier"): ("done", ""),
    # --- runs and gates ---
    ("POST", "/runs"): ("done", ""),
    ("GET", "/runs"): ("done", ""),
    ("GET", "/runs/{run_id}"): ("done", ""),
    ("POST", "/runs/{run_id}/cancel"): ("done", ""),
    ("POST", "/runs/{run_id}/retry"): ("done", ""),
    ("GET", "/runs/{run_id}/artifacts/{kind}"): ("deferred", "artifact storage / streaming"),
    ("GET", "/gates"): ("done", ""),
    ("POST", "/gates/{gate_id}/answer"): ("done", ""),
    # --- research / read models ---
    ("POST", "/queries"): ("done", ""),
    ("POST", "/queries/{run_id}/keep"): ("deferred", "dossier write path"),
    ("GET", "/predictions"): ("done", ""),
    ("GET", "/track-record"): ("deferred", "SPEC-TRACKREC scoring math"),
    ("GET", "/events"): ("done", ""),
    ("POST", "/events/{event_id}/rate"): ("done", ""),
    ("GET", "/costs"): ("done", ""),
    ("GET", "/health"): ("done", ""),
    ("GET", "/config/check"): ("done", ""),
    # --- reserved (phase 7.2) ---
    ("GET", "/portfolio"): ("deferred", "phase 7.2 risk/trades port"),
    ("GET", "/risk"): ("deferred", "phase 7.2 risk/trades port"),
    ("POST", "/trades/confirm/{id}"): ("deferred", "phase 7.2 risk/trades port"),
    ("GET", "/trades"): ("deferred", "phase 7.2 risk/trades port"),
}


def _router_routes() -> set[tuple[str, str]]:
    from core.api.routes import router

    routes: set[tuple[str, str]] = set()
    for r in router.routes:
        methods = getattr(r, "methods", None)
        if not methods:
            continue
        for m in methods:
            routes.add((m, r.path))
    return routes


def test_every_done_route_is_in_the_router():
    done = {k for k, (s, _) in SPEC_ROUTES.items() if s == "done"}
    actual = _router_routes()
    missing = done - actual
    assert not missing, f"spec 'done' routes missing from router: {sorted(missing)}"


def test_no_deferred_route_is_silently_implemented():
    """If a deferred route IS in the router, its status must be flipped to 'done'."""
    deferred = {k for k, (s, _) in SPEC_ROUTES.items() if s == "deferred"}
    actual = _router_routes()
    stale = deferred & actual
    assert not stale, f"deferred routes are actually implemented — flip to 'done': {sorted(stale)}"


def test_no_surprise_routes_outside_the_spec():
    actual = _router_routes()
    known = set(SPEC_ROUTES.keys())
    extra = actual - known
    assert not extra, f"routes in router not tracked in SPEC_ROUTES: {sorted(extra)}"


def test_every_done_route_has_a_cli_command():
    from cli.main import ROUTE_COMMAND_MAP

    done = {k for k, (s, _) in SPEC_ROUTES.items() if s == "done"}
    missing = done - set(ROUTE_COMMAND_MAP.keys())
    assert not missing, f"done routes without a CLI command mapping: {sorted(missing)}"


def test_every_mapped_command_is_a_registered_subcommand():
    from cli.main import ROUTE_COMMAND_MAP, build_parser

    parser = build_parser()
    registered = set(parser._subparsers._group_actions[0].choices.keys())  # type: ignore[attr-defined]
    mapped = set(ROUTE_COMMAND_MAP.values())
    unregistered = mapped - registered
    assert (
        not unregistered
    ), f"commands in ROUTE_COMMAND_MAP but not in build_parser: {sorted(unregistered)}"


def test_every_deferred_route_has_a_reason():
    for key, (status, reason) in SPEC_ROUTES.items():
        if status == "deferred":
            assert reason, f"deferred route {key} has no reason — why is it deferred?"
