"""No-hardcoded-models / no-price-literals guard (SPEC-CORE §9.3).

Model ids and prices live in houses.yaml/pricing.yaml only — never in code. This scans the
production code (core/, cli/, adapter/, runner/) for provider model-id tokens and price
literals and fails on a hit. House keys ("gpt", "gemini") are config identifiers, not
model ids — the regexes require a version segment, so they pass.

The fires-test (``test_guard_actually_fires_on_a_known_bad_fixture``) proves the guard
catches a real model id (``gpt-4o``, which the previous regex missed because of a trailing
``\\b``) and a dollar price. A guard that has never been observed firing is not a guard.
"""

from __future__ import annotations

import pathlib
import re
import tempfile

_MODEL_ID = re.compile(
    r"(gpt-[0-9][0-9a-z\-]*|claude-[0-9][0-9a-z\-]*|gemini-[0-9][0-9a-z\-]*"
    r"|o[13]-[0-9][0-9a-z\-]*|deepseek-[a-z0-9][a-z0-9\-]*|glm-[0-9][0-9a-z\-]*"
    r"|qwen[0-9][0-9a-z\-]*|sonnet|haiku|opus)",
    re.IGNORECASE,
)
# Dollar-amount price literals (unambiguous; field names like `input_per_mtok` do not match).
_PRICE_LITERAL = re.compile(r"\$\d+\.\d+")


def find_offenders(paths):
    """Return ``[file: 'token']`` for any model-id or price literal in the given files."""
    out: list[str] = []
    for p in paths:
        text = pathlib.Path(p).read_text()
        for m in _MODEL_ID.finditer(text):
            out.append(f"{p}: model id {m.group(0)!r}")
        for m in _PRICE_LITERAL.finditer(text):
            out.append(f"{p}: price {m.group(0)!r}")
    return out


def _production_paths(root: pathlib.Path):
    for sub in ("core", "cli", "adapter", "runner"):
        base = root / sub
        if not base.exists():
            continue
        yield from base.rglob("*.py")


def test_no_hardcoded_model_ids_or_prices_in_production_code():
    root = pathlib.Path(__file__).resolve().parents[2]
    offenders = find_offenders(_production_paths(root))
    assert not offenders, f"hardcoded model ids/prices found in production code: {offenders}"


def test_guard_actually_fires_on_a_known_bad_fixture():
    """Proves the regex catches what it claims: a gpt-4o-style id AND a dollar price."""
    with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False) as f:
        f.write(
            "# fake production code\n"
            'MODEL = "gpt-4o"   # the previous \\\\b-anchored regex missed this\n'
            "PRICE = $0.015     # a literal dollar price\n"
        )
        path = f.name
    try:
        offenders = find_offenders([path])
    finally:
        pathlib.Path(path).unlink()
    toks = " ".join(offenders)
    assert "gpt-4o" in toks
    assert "$0.015" in toks
