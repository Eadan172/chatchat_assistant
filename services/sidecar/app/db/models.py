"""SQLModel tables: v1.0 content model plus v1.1 nullable freshness fields."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import Column, Index
from sqlalchemy import JSON
from sqlmodel import Field, SQLModel


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class ContentItem(SQLModel, table=True):
    """Normalized content row shared by every platform."""

    __tablename__ = "content_item"
    __table_args__ = (
        Index("ux_content_platform_source", "platform", "source_id", unique=True),
    )

    id: int | None = Field(default=None, primary_key=True)
    platform: str = Field(index=True)
    source_id: str = Field(index=True)
    content_hash: str = Field(index=True)
    url: str
    title: str | None = None
    author: str | None = None
    author_id: str | None = None
    published_at: datetime | None = Field(default=None, index=True)
    body_text: str = ""
    media: list[Any] = Field(default_factory=list, sa_column=Column(JSON))
    metrics: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))
    collected_at: datetime = Field(default_factory=utcnow, index=True)
    index_state: str = Field(default="pending", index=True)
    last_error: str | None = None

    fetched_at: datetime | None = Field(default=None, index=True)
    source_mtime: datetime | None = None
    freshness_state: str = Field(default="unknown", index=True)
    verified_at: datetime | None = None
    source_kind: str | None = Field(default=None, index=True)
    connector_id: str | None = None
    external_id: str | None = None


class Chunk(SQLModel, table=True):
    __tablename__ = "chunk"
    __table_args__ = (Index("ux_chunk_item_ordinal", "item_id", "ordinal", unique=True),)

    id: int | None = Field(default=None, primary_key=True)
    item_id: int = Field(foreign_key="content_item.id", index=True)
    ordinal: int
    text: str
    token_count: int
    vec_rowid: int | None = Field(default=None, index=True)
    meta: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))


class Conversation(SQLModel, table=True):
    __tablename__ = "conversation"

    id: int | None = Field(default=None, primary_key=True)
    title: str
    mode: str = "personal"
    created_at: datetime = Field(default_factory=utcnow)
    clarify_session_id: int | None = Field(default=None, index=True)
    source_policy: dict[str, Any] | None = Field(default=None, sa_column=Column(JSON))


class Message(SQLModel, table=True):
    __tablename__ = "message"

    id: int | None = Field(default=None, primary_key=True)
    conversation_id: int = Field(foreign_key="conversation.id", index=True)
    role: str
    content: str
    citations: list[Any] = Field(default_factory=list, sa_column=Column(JSON))
    provider: str | None = None
    model: str | None = None
    prompt_tokens: int = 0
    completion_tokens: int = 0
    latency_ms: int = 0
    created_at: datetime = Field(default_factory=utcnow)
    decision_trace_id: int | None = None


class CollectJob(SQLModel, table=True):
    __tablename__ = "collect_job"

    id: int | None = Field(default=None, primary_key=True)
    platform: str
    keyword: str | None = None
    cron: str | None = None
    state: str = Field(default="idle", index=True)
    last_run_at: datetime | None = None
    last_error: str | None = None
    stats: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))


class ClarifySession(SQLModel, table=True):
    __tablename__ = "clarify_session"

    id: int | None = Field(default=None, primary_key=True)
    conversation_id: int | None = Field(default=None, index=True)
    keyword: str
    tree_json: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))
    brief_json: dict[str, Any] | None = Field(default=None, sa_column=Column(JSON))
    state: str = Field(default="active", index=True)
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)


class ConnectorAccount(SQLModel, table=True):
    __tablename__ = "connector_account"

    id: int | None = Field(default=None, primary_key=True)
    connector_type: str
    display_name: str
    auth_ref: str
    config: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))
    sync_cursor: str | None = None
    enabled: bool = True
    last_sync_at: datetime | None = None
    last_error: str | None = None


class DecisionLog(SQLModel, table=True):
    __tablename__ = "decision_log"

    id: int | None = Field(default=None, primary_key=True)
    conversation_id: int | None = None
    question_id: str
    qtype: str
    input_snapshot: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))
    result: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))
    confidence: float = 0.0
    applied: bool = False
    fallback: str | None = None
    created_at: datetime = Field(default_factory=utcnow)


class VerifyJob(SQLModel, table=True):
    __tablename__ = "verify_job"

    id: int | None = Field(default=None, primary_key=True)
    item_ids: list[Any] = Field(default_factory=list, sa_column=Column(JSON))
    state: str = Field(default="pending", index=True)
    stats: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))
    created_at: datetime = Field(default_factory=utcnow)
    finished_at: datetime | None = None
