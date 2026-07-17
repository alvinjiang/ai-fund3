"""0001 baseline — every SPEC-DOMAIN §4 table (one fresh baseline; no v2 history).

upgrade creates all tables/indexes/constraints from ``Base.metadata`` via ``create_all``
(so ``alembic check`` reports zero drift), and downgrade drops them. Does NOT create the
pgvector extension (§4.24) or the retired v2 tables (§4.25 retire list). Later amendments
(SPEC-DOMAIN §12) are already folded into the models, so the baseline includes them.
"""

from __future__ import annotations

from alembic import op

import core.db.models  # noqa: F401  (registers all tables on Base.metadata)
from core.db.base import Base

revision = "0001_baseline"
down_revision: str | None = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    Base.metadata.create_all(op.get_bind())


def downgrade() -> None:
    Base.metadata.drop_all(op.get_bind())
