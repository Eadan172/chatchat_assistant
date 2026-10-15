"""M0 acceptance: imports, stub routes, migrations, and lazy model loading."""

from __future__ import annotations

import importlib
import sys

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlmodel import Session

from app.db.models import ClarifySession, ContentItem
from app.db.session import database_url, init_db, make_engine
from app.main import app

MODULES = [
    "app.main",
    "app.api.chat",
    "app.api.collect",
    "app.api.search",
    "app.api.library",
    "app.api.settings",
    "app.api.stats",
    "app.api.clarify",
    "app.api.verify",
    "app.api.connectors",
    "app.llm.gateway",
    "app.llm.registry",
    "app.llm.keys",
    "app.ingest.base",
    "app.index.chunker",
    "app.index.embedder",
    "app.retrieval.hybrid",
    "app.retrieval.multi_source",
    "app.db.models",
    "app.db.session",
    "app.db.vec",
    "app.orchestrator.scheduler",
    "app.orchestrator.chat_pipeline",
    "app.clarifier.design_tree",
    "app.clarifier.questioner",
    "app.clarifier.session",
    "app.decision.client",
    "app.decision.policies",
    "app.decision.shadow",
    "app.decision.calibrate",
    "app.connectors.base",
    "app.connectors.notion",
    "app.connectors.local_folder",
    "app.connectors.registry",
    "app.freshness.hasher",
    "app.freshness.verifier",
    "app.freshness.policies",
    "app.web_search.provider",
    "app.web_search.normalize",
]

HEAVY = ("torch", "FlagEmbedding", "transformers", "sqlite_vec")


@pytest.fixture()
def token(monkeypatch: pytest.MonkeyPatch) -> str:
    monkeypatch.setenv("SIDECAR_TOKEN", "test-token")
    return "test-token"


@pytest.fixture()
def client(token: str, tmp_path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    with TestClient(app) as test_client:
        yield test_client


def test_packages_import_without_heavy_models() -> None:
    for name in MODULES:
        importlib.import_module(name)
    loaded = set(sys.modules)
    assert not any(name in loaded for name in HEAVY)


def test_stub_routes_are_registered() -> None:
    paths = {getattr(route, "path", None) for route in app.routes}
    for path in (
        "/health",
        "/chat",
        "/collect",
        "/collect/{job_id}",
        "/search",
        "/library/items",
        "/library/items/{item_id}",
        "/settings/key",
        "/providers",
        "/stats",
        "/index/rebuild",
        "/clarify/start",
        "/clarify/active",
        "/clarify/{session_id}",
        "/clarify/{session_id}/answer",
        "/clarify/{session_id}/confirm",
        "/verify",
        "/verify/{job_id}",
        "/connectors",
        "/connectors/{connector_id}/sync",
        "/connectors/{connector_id}/status",
        "/connectors/{connector_id}",
        "/decision/logs",
        "/decision/shadow",
        "/settings/decision",
    ):
        assert path in paths


def test_health_requires_token_and_chat_sse(client: TestClient, token: str) -> None:
    missing = client.get("/health")
    assert missing.status_code == 401

    health = client.get("/health", headers={"x-sidecar-token": token})
    assert health.status_code == 200
    body = health.json()
    assert body["ok"] is True
    assert body["models"] == {"embedder": "not_loaded", "reranker": "not_loaded"}

    chat = client.post("/chat", headers={"x-sidecar-token": token})
    assert chat.status_code == 200
    assert "text/event-stream" in chat.headers["content-type"]
    assert "event: delta" in chat.text
    assert "event: done" in chat.text

    for path in ("/verify", "/connectors"):
        response = client.post(path, headers={"x-sidecar-token": token})
        assert response.status_code == 501
    missing_keyword = client.post("/clarify/start", headers={"x-sidecar-token": token})
    assert missing_keyword.status_code == 422


def test_unimplemented_v11_routes_still_return_501(client: TestClient, token: str) -> None:
    headers = {"x-sidecar-token": token}
    assert client.get("/verify/3", headers=headers).status_code == 501
    assert client.get("/connectors", headers=headers).status_code == 501
    assert client.post("/connectors", headers=headers).status_code == 501


def test_empty_database_upgrade_roundtrip(tmp_path) -> None:
    url = init_db(str(tmp_path))
    engine = make_engine(url)
    with Session(engine) as session:
        item = ContentItem(
            platform="bilibili",
            source_id="av1",
            content_hash="abc",
            url="https://www.bilibili.com/video/BV1",
            body_text="hello",
        )
        session.add(item)
        session.add(ClarifySession(keyword="考研数学"))
        session.commit()
        session.refresh(item)
        assert item.id is not None
        assert item.freshness_state == "unknown"
        assert item.fetched_at is None or item.fetched_at == item.collected_at

    with engine.connect() as conn:
        tables = set(conn.execute(text("SELECT name FROM sqlite_master WHERE type='table'")).scalars())
    assert {
        "content_item",
        "chunk",
        "conversation",
        "message",
        "collect_job",
        "clarify_session",
        "connector_account",
        "decision_log",
        "verify_job",
    } <= tables
    engine.dispose()


def test_upgrade_existing_v1_database(tmp_path) -> None:
    db_file = tmp_path / "data" / "app.db"
    db_file.parent.mkdir(parents=True)
    legacy = create_engine(f"sqlite:///{db_file}")
    with legacy.begin() as conn:
        conn.execute(
            text(
                """
                CREATE TABLE content_item (
                    id INTEGER PRIMARY KEY,
                    platform VARCHAR NOT NULL,
                    source_id VARCHAR NOT NULL,
                    content_hash VARCHAR NOT NULL,
                    url VARCHAR NOT NULL,
                    title VARCHAR,
                    author VARCHAR,
                    author_id VARCHAR,
                    published_at DATETIME,
                    body_text VARCHAR NOT NULL DEFAULT '',
                    media JSON,
                    metrics JSON,
                    collected_at DATETIME NOT NULL,
                    index_state VARCHAR NOT NULL DEFAULT 'pending',
                    last_error VARCHAR
                )
                """
            )
        )
        conn.execute(
            text(
                """
                INSERT INTO content_item
                    (platform, source_id, content_hash, url, body_text, collected_at, index_state)
                VALUES
                    ('zhihu', 'q1', 'hash1', 'https://example.com/q1', 'body', '2026-01-02 03:04:05', 'indexed')
                """
            )
        )
        conn.execute(
            text(
                """
                CREATE TABLE conversation (
                    id INTEGER PRIMARY KEY,
                    title VARCHAR NOT NULL,
                    mode VARCHAR NOT NULL DEFAULT 'personal',
                    created_at DATETIME NOT NULL
                )
                """
            )
        )
        conn.execute(
            text(
                """
                CREATE TABLE message (
                    id INTEGER PRIMARY KEY,
                    conversation_id INTEGER NOT NULL,
                    role VARCHAR NOT NULL,
                    content VARCHAR NOT NULL,
                    citations JSON,
                    provider VARCHAR,
                    model VARCHAR,
                    prompt_tokens INTEGER NOT NULL DEFAULT 0,
                    completion_tokens INTEGER NOT NULL DEFAULT 0,
                    latency_ms INTEGER NOT NULL DEFAULT 0,
                    created_at DATETIME NOT NULL
                )
                """
            )
        )
    legacy.dispose()

    assert database_url(str(tmp_path)).endswith("data/app.db")
    init_db(str(tmp_path))

    check = create_engine(f"sqlite:///{db_file}")
    with check.connect() as conn:
        row = conn.execute(
            text(
                """
                SELECT platform, body_text, index_state, fetched_at, freshness_state,
                       source_mtime, source_kind, connector_id, external_id
                FROM content_item
                """
            )
        ).one()
        conversation_cols = {col[1] for col in conn.execute(text("PRAGMA table_info(conversation)"))}
        message_cols = {col[1] for col in conn.execute(text("PRAGMA table_info(message)"))}
        version = conn.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
        tables = set(conn.execute(text("SELECT name FROM sqlite_master WHERE type='table'")).scalars())
    check.dispose()

    assert row.platform == "zhihu"
    assert row.body_text == "body"
    assert row.index_state == "indexed"
    assert "2026-01-02" in str(row.fetched_at)
    assert row.freshness_state == "unknown"
    assert row.source_mtime is None
    assert row.source_kind is None
    assert row.connector_id is None
    assert row.external_id is None
    assert {"clarify_session_id", "source_policy"} <= conversation_cols
    assert "decision_trace_id" in message_cols
    assert version == "0001_m0"
    assert {"clarify_session", "connector_account", "decision_log", "verify_job"} <= tables
