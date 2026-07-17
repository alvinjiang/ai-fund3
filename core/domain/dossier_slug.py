"""Dossier slug — SPEC-DOMAIN §4.2.

``f"{exchange}_{ticker}"`` lowercased with every run of non-alphanumerics collapsed to a
single ``_``. This is THIS module's key (checklist item 4): the dossier repo path, the
runner workspace, and the Mattermost channel name all read the stored ``dossier_slug``
column — none re-derives it from ``(exchange, ticker)``.
"""

from __future__ import annotations

import re

_NON_ALNUM = re.compile(r"[^a-z0-9]+")


def compute_dossier_slug(exchange: str, ticker: str) -> str:
    raw = f"{exchange}_{ticker}".lower()
    return _NON_ALNUM.sub("_", raw).strip("_")
