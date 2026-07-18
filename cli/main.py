"""``fund`` CLI — one thin client over the core API (SPEC-CORE §6).

No direct DB access (so the CLI proves the API is complete). ``ROUTE_COMMAND_MAP`` is the
authoritative route<->command mapping; the parity test asserts it covers every API route
in both directions (an added route without a CLI command fails CI). Output is a compact
table by default; errors surface the structured body and exit non-zero.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any

from cli.client import CoreClient, CoreError

# (HTTP method, API path template) -> the ``fund`` command that hits it. The parity test
# asserts this covers every route in core.api.routes.router in both directions.
ROUTE_COMMAND_MAP: dict[tuple[str, str], str] = {
    ("POST", "/coverage"): "propose",
    ("GET", "/coverage"): "coverage-list",
    ("GET", "/coverage/{slug}"): "coverage-show",
    ("GET", "/coverage/{slug}/dossier"): "dossier-show",
    ("POST", "/coverage/{slug}/initiate"): "initiate",
    ("POST", "/coverage/{slug}/decide"): "decide",
    ("POST", "/coverage/{slug}/promote"): "promote",
    ("POST", "/coverage/{slug}/demote"): "demote",
    ("POST", "/coverage/{slug}/exit"): "exit",
    ("POST", "/coverage/{slug}/re-propose"): "re-propose",
    ("POST", "/coverage/{slug}/lead"): "lead-set",
    ("POST", "/coverage/{slug}/levels"): "levels-set",
    ("POST", "/runs"): "run-new",
    ("GET", "/runs"): "runs-list",
    ("GET", "/runs/{run_id}"): "run-show",
    ("POST", "/runs/{run_id}/cancel"): "run-cancel",
    ("POST", "/runs/{run_id}/retry"): "run-retry",
    ("GET", "/gates"): "gates-list",
    ("POST", "/gates/{gate_id}/answer"): "gates-answer",
    ("POST", "/queries"): "query",
    ("GET", "/predictions"): "predictions-list",
    ("GET", "/events"): "events-list",
    ("POST", "/events/{event_id}/rate"): "events-rate",
    ("GET", "/costs"): "cost",
    ("GET", "/config/check"): "config-check",
    ("GET", "/health"): "health",
}


def _client(args: argparse.Namespace) -> CoreClient:
    return CoreClient(
        base_url=getattr(args, "api_url", None)
        or os.environ.get("CORE_API_URL", "http://127.0.0.1:8080"),
        token=getattr(args, "token", None) or os.environ["CORE_API_TOKEN"],
        pm_user=getattr(args, "pm_user", None) or os.environ.get("PM_USER", ""),
    )


def _emit(obj: Any, json_out: bool) -> int:
    if json_out:
        print(json.dumps(obj, default=str))
    else:
        if isinstance(obj, list):
            for row in obj:
                print(json.dumps(row, default=str))
        else:
            print(json.dumps(obj, default=str))
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="fund")
    p.add_argument("--api-url", dest="api_url")
    p.add_argument("--token", dest="token")
    p.add_argument("--pm-user", dest="pm_user")
    p.add_argument("--json", dest="json_out", action="store_true")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("health")
    sub.add_parser("gates-list")

    sp = sub.add_parser("propose")
    sp.add_argument("ticker")
    sp.add_argument("exchange")
    sp.add_argument("--name", required=True)
    sp.add_argument("--currency", required=True)

    sp = sub.add_parser("coverage-list")
    sp.add_argument("--state")
    sub.add_parser("coverage-show").add_argument("slug")

    sp = sub.add_parser("initiate")
    sp.add_argument("slug")
    sp.add_argument("--lead")
    sp.add_argument("--verify-count", type=int)

    sp = sub.add_parser("decide")
    sp.add_argument("slug")
    sp.add_argument("decision", choices=["active", "watch", "reject"])
    sp.add_argument("--notes")

    for c in ("promote", "demote", "re-propose"):
        sub.add_parser(c).add_argument("slug")

    sp = sub.add_parser("exit")
    sp.add_argument("slug")
    sp.add_argument("--force", action="store_true")
    sp.add_argument("--notes")

    sp = sub.add_parser("lead-set")
    sp.add_argument("slug")
    sp.add_argument("house")
    sp.add_argument("--rationale")

    sp = sub.add_parser("levels-set")
    sp.add_argument("slug")
    sp.add_argument("--entry", type=float)
    sp.add_argument("--target", type=float)
    sp.add_argument("--stop", type=float)
    sp.add_argument("--currency", default="JPY")

    sp = sub.add_parser("run-new")
    sp.add_argument("type")
    sp.add_argument("--coverage")

    sp = sub.add_parser("runs-list")
    sp.add_argument("--status")

    sp = sub.add_parser("run-cancel")
    sp.add_argument("run_id")
    sp.add_argument("--reason")

    sp = sub.add_parser("gates-answer")
    sp.add_argument("gate_id")
    sp.add_argument("answer")
    sp.add_argument("--notes")

    # read models (BUGS #1)
    sub.add_parser("run-show").add_argument("run_id")
    sub.add_parser("run-retry").add_argument("run_id")
    sub.add_parser("dossier-show").add_argument("slug")
    sp = sub.add_parser("predictions-list")
    sp.add_argument("--coverage")
    sp.add_argument("--house")
    sp.add_argument("--status")
    sp = sub.add_parser("events-list")
    sp.add_argument("--coverage")
    sp.add_argument("--severity")
    sp = sub.add_parser("events-rate")
    sp.add_argument("event_id")
    sp.add_argument("rating", choices=["1", "-1"], type=int)
    sp = sub.add_parser("cost")
    sp.add_argument("--by", choices=["house", "run"], default="house")
    sp = sub.add_parser("query")
    sp.add_argument("coverage")
    sp.add_argument("question")
    sub.add_parser("config-check")

    # standalone (not via API — runs locally for ExecStartPre / operator preflight)
    sp = sub.add_parser("checkconfig")
    sp.add_argument("--strict", action="store_true")

    return p


def run(args: argparse.Namespace) -> int:
    with _client(args) as c:
        try:
            if args.cmd == "health":
                return _emit(c.health(), args.json_out)
            if args.cmd == "propose":
                return _emit(
                    c.propose(args.ticker, args.exchange, args.name, args.currency), args.json_out
                )
            if args.cmd == "coverage-list":
                return _emit(c.coverage_list(args.state), args.json_out)
            if args.cmd == "coverage-show":
                return _emit(c.coverage_show(args.slug), args.json_out)
            if args.cmd == "initiate":
                return _emit(c.initiate(args.slug, args.lead, args.verify_count), args.json_out)
            if args.cmd == "decide":
                return _emit(c.decide(args.slug, args.decision, args.notes), args.json_out)
            if args.cmd == "promote":
                return _emit(c.promote(args.slug), args.json_out)
            if args.cmd == "demote":
                return _emit(c.demote(args.slug), args.json_out)
            if args.cmd == "exit":
                return _emit(c.exit_coverage(args.slug, args.force, args.notes), args.json_out)
            if args.cmd == "re-propose":
                return _emit(c.re_propose(args.slug), args.json_out)
            if args.cmd == "lead-set":
                return _emit(c.set_lead(args.slug, args.house, args.rationale), args.json_out)
            if args.cmd == "levels-set":
                levels = []
                for kind, val in [
                    ("entry", args.entry),
                    ("target", args.target),
                    ("stop", args.stop),
                ]:
                    if val is not None:
                        levels.append(
                            {
                                "kind": kind,
                                "value": val,
                                "currency": args.currency,
                                "direction": "above",
                            }
                        )
                return _emit(c.set_levels(args.slug, levels), args.json_out)
            if args.cmd == "runs-list":
                return _emit(c.runs_list(status=args.status), args.json_out)
            if args.cmd == "run-new":
                return _emit(c.new_run(args.type, args.coverage), args.json_out)
            if args.cmd == "run-cancel":
                return _emit(c.cancel_run(args.run_id, args.reason), args.json_out)
            if args.cmd == "gates-list":
                return _emit(c.gates_list(), args.json_out)
            if args.cmd == "gates-answer":
                return _emit(c.answer_gate(args.gate_id, args.answer, args.notes), args.json_out)
            # read models (BUGS #1)
            if args.cmd == "run-show":
                return _emit(c.run_show(args.run_id), args.json_out)
            if args.cmd == "run-retry":
                return _emit(c.run_retry(args.run_id), args.json_out)
            if args.cmd == "dossier-show":
                return _emit(c.dossier_show(args.slug), args.json_out)
            if args.cmd == "predictions-list":
                return _emit(
                    c.predictions_list(
                        coverage=args.coverage, house=args.house, status=args.status
                    ),
                    args.json_out,
                )
            if args.cmd == "events-list":
                return _emit(
                    c.events_list(coverage=args.coverage, severity=args.severity), args.json_out
                )
            if args.cmd == "events-rate":
                return _emit(c.events_rate(args.event_id, args.rating), args.json_out)
            if args.cmd == "cost":
                return _emit(c.cost(by=args.by), args.json_out)
            if args.cmd == "query":
                return _emit(c.query(args.coverage, args.question), args.json_out)
            if args.cmd == "config-check":
                return _emit(c.config_check(), args.json_out)
        except CoreError as exc:
            print(json.dumps(exc.body, default=str), file=sys.stderr)
            return 1
    # standalone local commands (not via the API — run before/without the service)
    if args.cmd == "checkconfig":
        from core.config.checkconfig import any_fail, format_table, run_checks
        from core.config.settings import get_settings

        try:
            from core.config.store import get_config

            cfg = get_config()
        except Exception as exc:
            print(f"config not loaded: {exc}", file=sys.stderr)
            return 1
        results = run_checks(cfg, get_settings())
        print(format_table(results))
        return 1 if args.strict and any_fail(results) else 0
    return 2


def main(argv: list[str] | None = None) -> int:
    return run(build_parser().parse_args(argv))


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
