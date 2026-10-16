"""M4 local freshness bookkeeping.

`fetched_at`, `source_mtime`, `content_hash`, and `freshness_state` already
exist from 0001_m0. This revision adds the on-disk path a local document is
re-read from, and a short reason for the last check. It does not add a web
re-fetch. That stays a later milestone.

Revision ID: 0003_m4
Revises: 0002_m3
Create Date: 2026-10-16
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0003_m4"
down_revision = "0002_m3"
branch_labels = None
depends_on = None


def _columns(bind) -> set[str]:
    inspector = sa.inspect(bind)
    inspector.clear_cache()
    return {column["name"] for column in inspector.get_columns("content_item")}


def upgrade() -> None:
    bind = op.get_bind()
    have = _columns(bind)
    if "source_path" not in have:
        op.add_column("content_item", sa.Column("source_path", sa.String(), nullable=True))
    if "freshness_reason" not in have:
        op.add_column("content_item", sa.Column("freshness_reason", sa.String(), nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    have = _columns(bind)
    if "freshness_reason" in have:
        op.drop_column("content_item", "freshness_reason")
    if "source_path" in have:
        op.drop_column("content_item", "source_path")
