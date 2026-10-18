"""M7 conversation memory: projects, folders, fork lineage, locale/style.

Revision ID: 0004_m7
Revises: 0003_m4
Create Date: 2026-10-18
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0004_m7"
down_revision = "0003_m4"
branch_labels = None
depends_on = None


def _columns(bind, table: str) -> set[str]:
    inspector = sa.inspect(bind)
    inspector.clear_cache()
    try:
        return {column["name"] for column in inspector.get_columns(table)}
    except Exception:
        return set()


def _tables(bind) -> set[str]:
    return set(sa.inspect(bind).get_table_names())


def upgrade() -> None:
    bind = op.get_bind()
    tables = _tables(bind)

    if "project" not in tables:
        op.create_table(
            "project",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("name", sa.String(), nullable=False),
            sa.Column("description", sa.String(), nullable=True),
            sa.Column("color", sa.String(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=True),
            sa.Column("updated_at", sa.DateTime(), nullable=True),
        )
        op.create_index("ix_project_name", "project", ["name"])

    if "conversation_folder" not in tables:
        op.create_table(
            "conversation_folder",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("name", sa.String(), nullable=False),
            sa.Column("project_id", sa.Integer(), sa.ForeignKey("project.id"), nullable=True),
            sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("created_at", sa.DateTime(), nullable=True),
            sa.Column("updated_at", sa.DateTime(), nullable=True),
        )
        op.create_index("ix_conversation_folder_name", "conversation_folder", ["name"])
        op.create_index("ix_conversation_folder_project_id", "conversation_folder", ["project_id"])

    have = _columns(bind, "conversation")
    additions = [
        ("folder_id", sa.Column("folder_id", sa.Integer(), nullable=True)),
        ("project_id", sa.Column("project_id", sa.Integer(), nullable=True)),
        ("forked_from_id", sa.Column("forked_from_id", sa.Integer(), nullable=True)),
        ("forked_from_message_id", sa.Column("forked_from_message_id", sa.Integer(), nullable=True)),
        ("locale", sa.Column("locale", sa.String(), nullable=True)),
        ("style", sa.Column("style", sa.String(), nullable=True)),
        ("updated_at", sa.Column("updated_at", sa.DateTime(), nullable=True)),
        ("archived", sa.Column("archived", sa.Boolean(), nullable=False, server_default="0")),
        ("pinned", sa.Column("pinned", sa.Boolean(), nullable=False, server_default="0")),
    ]
    for name, column in additions:
        if name not in have:
            op.add_column("conversation", column)

    for index_name, column in (
        ("ix_conversation_folder_id", "folder_id"),
        ("ix_conversation_project_id", "project_id"),
        ("ix_conversation_forked_from_id", "forked_from_id"),
        ("ix_conversation_updated_at", "updated_at"),
    ):
        try:
            op.create_index(index_name, "conversation", [column])
        except Exception:
            pass


def downgrade() -> None:
    have = _columns(op.get_bind(), "conversation")
    for name in (
        "pinned",
        "archived",
        "updated_at",
        "style",
        "locale",
        "forked_from_message_id",
        "forked_from_id",
        "project_id",
        "folder_id",
    ):
        if name in have:
            op.drop_column("conversation", name)
    tables = _tables(op.get_bind())
    if "conversation_folder" in tables:
        op.drop_table("conversation_folder")
    if "project" in tables:
        op.drop_table("project")
