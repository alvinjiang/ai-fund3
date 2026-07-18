"""Dossier index repository — SPEC-DOMAIN §4.17 / §2 (module layout).

git is the dossier audit trail; these are the DB index rows the orchestrator/runner use
for fast reads and the per-ticker serialization check. ``upsert_index`` is the one writer
of the mirrored display columns (stance / target_price / tripwire_count) — the dossier
file remains the truth, these are conveniences.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from core.db import models


def get_index(session: Session, coverage_id: UUID) -> models.DossierIndex | None:
    return session.get(models.DossierIndex, coverage_id)


def get_by_slug(session: Session, slug: str) -> models.DossierIndex | None:
    return session.scalar(select(models.DossierIndex).where(models.DossierIndex.slug == slug))


def upsert_index(
    session: Session,
    *,
    coverage_id: UUID,
    slug: str,
    main_commit: str | None = None,
    updated_by_run_id: UUID | None = None,
    stance: str | None = None,
    conviction: str | None = None,
    as_of: Any = None,
    target_price: Any = None,
    tripwire_count: int | None = None,
    files: dict | None = None,
) -> models.DossierIndex:
    row = session.get(models.DossierIndex, coverage_id)
    if row is None:
        row = models.DossierIndex(coverage_id=coverage_id, slug=slug)
        session.add(row)
    # mutate only the fields the caller actually passed
    for name, value in {
        "main_commit": main_commit,
        "updated_by_run_id": updated_by_run_id,
        "stance": stance,
        "conviction": conviction,
        "as_of": as_of,
        "target_price": target_price,
        "tripwire_count": tripwire_count,
        "files": files,
    }.items():
        if value is not None:
            setattr(row, name, value)
    session.flush()
    return row


def record_commit(
    session: Session,
    *,
    coverage_id: UUID,
    commit_sha: str,
    branch: str,
    message: str,
    run_id: UUID | None = None,
    stage_id: UUID | None = None,
    parent_sha: str | None = None,
    files_changed: dict | None = None,
    merged_to_main: bool = False,
) -> models.DossierCommit:
    row = models.DossierCommit(
        coverage_id=coverage_id,
        run_id=run_id,
        stage_id=stage_id,
        commit_sha=commit_sha,
        branch=branch,
        parent_sha=parent_sha,
        message=message,
        files_changed=files_changed,
        merged_to_main=merged_to_main,
    )
    session.add(row)
    session.flush()
    return row
