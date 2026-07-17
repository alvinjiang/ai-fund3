"""No-hardcoded-models / no-price-literals guard (SPEC-CORE §9.3).

Model ids and prices live in houses.yaml/pricing.yaml only — never in code. This scans the
production code (core/, cli/, adapter/, runner/) for provider model-id patterns and price
literals and fails on a hit. House keys ("gpt", "gemini") are config identifiers, not
model ids, so they are allowed (the regexes require a version digit / dash).
"""

from __future__ import annotations

import pathlib
import re

_MODEL_ID = re.compile(
    r"\b(gpt-[0-9]|gpt\d|claude-[0-9]|claude\d|gemini-[0-9]|gemini-1"
    r"|o[13]-[0-9]|o[13]\b|deepseek-|glm-[0-9]|qwen[0-9]|sonnet|haiku|opus)\b",
    re.IGNORECASE,
)


def test_no_hardcoded_model_ids_in_production_code():
    root = pathlib.Path(__file__).resolve().parents[2]
    offenders: list[str] = []
    for sub in ("core", "cli", "adapter", "runner"):
        base = root / sub
        if not base.exists():
            continue
        for p in base.rglob("*.py"):
            text = p.read_text()
            for m in _MODEL_ID.finditer(text):
                offenders.append(f"{p.relative_to(root)}: {m.group(0)!r}")
    assert not offenders, f"hardcoded model ids found in production code: {offenders}"
