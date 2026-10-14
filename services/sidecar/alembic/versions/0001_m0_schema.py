"""M0 schema: v1.0 tables plus v1.1 freshness fields and new tables.

Creates the full schema on an empty database. On a database that already has
v1.0 tables, adds nullable / defaulted columns and the new tables.

Revision ID: 0001_m0
Revises:
Create Date: 2026-10-14
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0001_m0"
down_revision = None
branch_labels = None
depends_on = None

def _literal(value: str) -> sa.TextClause:
    escaped = value.replace("'", "''")
    return sa.text(f"'{escaped}'")


_V11_CONTENT_COLUMNS = (
    sa.Column("fetched_at", sa.DateTime(), nullable=True),
    sa.Column("source_mtime", sa.DateTime(), nullable=True),
    sa.Column("freshness_state", sa.String(), nullable=False, server_default=_literal("unknown")),
    sa.Column("verified_at", sa.DateTime(), nullable=True),
    sa.Column("source_kind", sa.String(), nullable=True),
    sa.Column("connector_id", sa.String(), nullable=True),
    sa.Column("external_id", sa.String(), nullable=True),
)

_CONTENT_INDEXES = (
    ("ix_content_item_platform", ["platform"], False),
    ("ix_content_item_source_id", ["source_id"], False),
    ("ix_content_item_content_hash", ["content_hash"], False),
    ("ix_content_item_published_at", ["published_at"], False),
    ("ix_content_item_collected_at", ["collected_at"], False),
    ("ix_content_item_index_state", ["index_state"], False),
    ("ix_content_item_fetched_at", ["fetched_at"], False),
    ("ix_content_item_freshness_state", ["freshness_state"], False),
    ("ix_content_item_source_kind", ["source_kind"], False),
    ("ux_content_platform_source", ["platform", "source_id"], True),
)


def _inspector(bind):
    inspector = sa.inspect(bind)
    inspector.clear_cache()
    return inspector


def _names(bind) -> set[str]:
    return set(_inspector(bind).get_table_names())


def _columns(bind, table: str) -> set[str]:
    return {col["name"] for col in _inspector(bind).get_columns(table)}


def _indexes(bind, table: str) -> set[str]:
    return {idx["name"] for idx in _inspector(bind).get_indexes(table)}


def _ensure_index(bind, table: str, name: str, columns: list[str], unique: bool = False) -> None:
    if name not in _indexes(bind, table):
        op.create_index(name, table, columns, unique=unique)


def _add_missing(bind, table: str, columns: tuple[sa.Column, ...]) -> None:
    have = _columns(bind, table)
    for column in columns:
        if column.name not in have:
            op.add_column(table, column)


def upgrade() -> None:
    bind = op.get_bind()
    tables = _names(bind)

    if "content_item" not in tables:
        op.create_table(
            "content_item",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("platform", sa.String(), nullable=False),
            sa.Column("source_id", sa.String(), nullable=False),
            sa.Column("content_hash", sa.String(), nullable=False),
            sa.Column("url", sa.String(), nullable=False),
            sa.Column("title", sa.String(), nullable=True),
            sa.Column("author", sa.String(), nullable=True),
            sa.Column("author_id", sa.String(), nullable=True),
            sa.Column("published_at", sa.DateTime(), nullable=True),
            sa.Column("body_text", sa.String(), nullable=False, server_default=_literal("")),
            sa.Column("media", sa.JSON(), nullable=True),
            sa.Column("metrics", sa.JSON(), nullable=True),
            sa.Column("collected_at", sa.DateTime(), nullable=False),
            sa.Column("index_state", sa.String(), nullable=False, server_default=_literal("pending")),
            sa.Column("last_error", sa.String(), nullable=True),
            *_V11_CONTENT_COLUMNS,
        )
    else:
        _add_missing(bind, "content_item", _V11_CONTENT_COLUMNS)

    op.execute("UPDATE content_item SET fetched_at = collected_at WHERE fetched_at IS NULL")
    op.execute("UPDATE content_item SET freshness_state = 'unknown' WHERE freshness_state IS NULL")
    for name, columns, unique in _CONTENT_INDEXES:
        _ensure_index(bind, "content_item", name, columns, unique)

    tables = _names(bind)
    if "chunk" not in tables:
        op.create_table(
            "chunk",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("item_id", sa.Integer(), sa.ForeignKey("content_item.id"), nullable=False),
            sa.Column("ordinal", sa.Integer(), nullable=False),
            sa.Column("text", sa.String(), nullable=False),
            sa.Column("token_count", sa.Integer(), nullable=False),
            sa.Column("vec_rowid", sa.Integer(), nullable=True),
            sa.Column("meta", sa.JSON(), nullable=True),
        )
    _ensure_index(bind, "chunk", "ix_chunk_item_id", ["item_id"])
    _ensure_index(bind, "chunk", "ix_chunk_vec_rowid", ["vec_rowid"])
    _ensure_index(bind, "chunk", "ux_chunk_item_ordinal", ["item_id", "ordinal"], unique=True)

    tables = _names(bind)
    if "conversation" not in tables:
        op.create_table(
            "conversation",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("title", sa.String(), nullable=False),
            sa.Column("mode", sa.String(), nullable=False, server_default=_literal("personal")),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("clarify_session_id", sa.Integer(), nullable=True),
            sa.Column("source_policy", sa.JSON(), nullable=True),
        )
    else:
        _add_missing(
            bind,
            "conversation",
            (
                sa.Column("clarify_session_id", sa.Integer(), nullable=True),
                sa.Column("source_policy", sa.JSON(), nullable=True),
            ),
        )
    _ensure_index(bind, "conversation", "ix_conversation_clarify_session_id", ["clarify_session_id"])

    tables = _names(bind)
    if "message" not in tables:
        op.create_table(
            "message",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("conversation_id", sa.Integer(), sa.ForeignKey("conversation.id"), nullable=False),
            sa.Column("role", sa.String(), nullable=False),
            sa.Column("content", sa.String(), nullable=False),
            sa.Column("citations", sa.JSON(), nullable=True),
            sa.Column("provider", sa.String(), nullable=True),
            sa.Column("model", sa.String(), nullable=True),
            sa.Column("prompt_tokens", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("completion_tokens", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("latency_ms", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("decision_trace_id", sa.Integer(), nullable=True),
        )
    else:
        _add_missing(bind, "message", (sa.Column("decision_trace_id", sa.Integer(), nullable=True),))
    _ensure_index(bind, "message", "ix_message_conversation_id", ["conversation_id"])

    tables = _names(bind)
    if "collect_job" not in tables:
        op.create_table(
            "collect_job",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("platform", sa.String(), nullable=False),
            sa.Column("keyword", sa.String(), nullable=True),
            sa.Column("cron", sa.String(), nullable=True),
            sa.Column("state", sa.String(), nullable=False, server_default=_literal("idle")),
            sa.Column("last_run_at", sa.DateTime(), nullable=True),
            sa.Column("last_error", sa.String(), nullable=True),
            sa.Column("stats", sa.JSON(), nullable=True),
        )
    _ensure_index(bind, "collect_job", "ix_collect_job_state", ["state"])

    tables = _names(bind)
    if "clarify_session" not in tables:
        op.create_table(
            "clarify_session",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("conversation_id", sa.Integer(), nullable=True),
            sa.Column("keyword", sa.String(), nullable=False),
            sa.Column("tree_json", sa.JSON(), nullable=True),
            sa.Column("brief_json", sa.JSON(), nullable=True),
            sa.Column("state", sa.String(), nullable=False, server_default=_literal("active")),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
        )
    _ensure_index(bind, "clarify_session", "ix_clarify_session_conversation_id", ["conversation_id"])
    _ensure_index(bind, "clarify_session", "ix_clarify_session_state", ["state"])

    tables = _names(bind)
    if "connector_account" not in tables:
        op.create_table(
            "connector_account",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("connector_type", sa.String(), nullable=False),
            sa.Column("display_name", sa.String(), nullable=False),
            sa.Column("auth_ref", sa.String(), nullable=False),
            sa.Column("config", sa.JSON(), nullable=True),
            sa.Column("sync_cursor", sa.String(), nullable=True),
            sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
            sa.Column("last_sync_at", sa.DateTime(), nullable=True),
            sa.Column("last_error", sa.String(), nullable=True),
        )

    tables = _names(bind)
    if "decision_log" not in tables:
        op.create_table(
            "decision_log",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("conversation_id", sa.Integer(), nullable=True),
            sa.Column("question_id", sa.String(), nullable=False),
            sa.Column("qtype", sa.String(), nullable=False),
            sa.Column("input_snapshot", sa.JSON(), nullable=True),
            sa.Column("result", sa.JSON(), nullable=True),
            sa.Column("confidence", sa.Float(), nullable=False, server_default="0"),
            sa.Column("applied", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column("fallback", sa.String(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
        )

    tables = _names(bind)
    if "verify_job" not in tables:
        op.create_table(
            "verify_job",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("item_ids", sa.JSON(), nullable=True),
            sa.Column("state", sa.String(), nullable=False, server_default=_literal("pending")),
            sa.Column("stats", sa.JSON(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("finished_at", sa.DateTime(), nullable=True),
        )
    _ensure_index(bind, "verify_job", "ix_verify_job_state", ["state"])


def _drop_index(bind, table: str, name: str) -> None:
    if table in _names(bind) and name in _indexes(bind, table):
        op.drop_index(name, table_name=table)


def downgrade() -> None:
    bind = op.get_bind()
    tables = _names(bind)
    for name in ("verify_job", "decision_log", "connector_account", "clarify_session"):
        if name in tables:
            op.drop_table(name)

    if "message" in tables and "decision_trace_id" in _columns(bind, "message"):
        op.drop_column("message", "decision_trace_id")
    if "conversation" in tables:
        _drop_index(bind, "conversation", "ix_conversation_clarify_session_id")
        cols = _columns(bind, "conversation")
        if "source_policy" in cols:
            op.drop_column("conversation", "source_policy")
        if "clarify_session_id" in cols:
            op.drop_column("conversation", "clarify_session_id")
    if "content_item" in tables:
        for index_name in (
            "ix_content_item_fetched_at",
            "ix_content_item_freshness_state",
            "ix_content_item_source_kind",
        ):
            _drop_index(bind, "content_item", index_name)
        for column in (
            "external_id",
            "connector_id",
            "source_kind",
            "verified_at",
            "freshness_state",
            "source_mtime",
            "fetched_at",
        ):
            if column in _columns(bind, "content_item"):
                op.drop_column("content_item", column)
