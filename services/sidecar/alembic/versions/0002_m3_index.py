"""M3 index tables: FTS5 chunk text and a blob vector store.

sqlite-vec's virtual table is created at index time, because loading the
extension is optional and must not fail a migration on a machine without it.

Revision ID: 0002_m3
Revises: 0001_m0
Create Date: 2026-10-15
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0002_m3"
down_revision = "0001_m0"
branch_labels = None
depends_on = None

FTS_DDL = """
CREATE VIRTUAL TABLE IF NOT EXISTS fts_chunks USING fts5(
  text,
  item_id UNINDEXED,
  chunk_id UNINDEXED,
  tokenize='trigram'
)
"""


def _names(bind) -> set[str]:
    inspector = sa.inspect(bind)
    inspector.clear_cache()
    return set(inspector.get_table_names())


def upgrade() -> None:
    bind = op.get_bind()
    op.execute(FTS_DDL)
    if "chunk_vector" not in _names(bind):
        op.create_table(
            "chunk_vector",
            sa.Column("chunk_id", sa.Integer(), primary_key=True),
            sa.Column("embedding", sa.LargeBinary(), nullable=False),
            sa.Column("dim", sa.Integer(), nullable=False),
        )


def downgrade() -> None:
    bind = op.get_bind()
    names = _names(bind)
    if "chunk_vector" in names:
        op.drop_table("chunk_vector")
    if "fts_chunks" in names:
        op.execute("DROP TABLE fts_chunks")
