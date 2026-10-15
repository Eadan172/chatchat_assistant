"""M1 clarifier: one frontier question, park, recap, brief on /chat, restart."""

from __future__ import annotations

import json

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text

from app.clarifier.design_tree import build_tree
from app.clarifier.questioner import render_question
from app.db.session import database_url
from app.main import app


@pytest.fixture()
def token(monkeypatch: pytest.MonkeyPatch) -> str:
    monkeypatch.setenv("SIDECAR_TOKEN", "test-token")
    monkeypatch.delenv("CLARIFY_LLM_API_KEY", raising=False)
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    return "test-token"


@pytest.fixture()
def client(token: str, tmp_path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    with TestClient(app) as test_client:
        yield test_client


def headers(token: str) -> dict[str, str]:
    return {"x-sidecar-token": token}


def parse_sse(payload: str) -> dict[str, dict]:
    events: dict[str, dict] = {}
    for block in payload.split("\n\n"):
        event = "message"
        data = None
        for line in block.splitlines():
            if line.startswith("event:"):
                event = line.split(":", 1)[1].strip()
            elif line.startswith("data:"):
                data = json.loads(line.split(":", 1)[1].strip())
        if isinstance(data, dict):
            events[event] = data
    return events


def test_park_leaves_independent_branches_open() -> None:
    tree = build_tree("考研数学复盘", None, 7)
    assert [node.id for node in tree.nodes] == [
        "goal",
        "scope",
        "constraints",
        "deps",
        "tradeoffs",
        "risks",
        "next",
    ]
    tree.apply("goal", "accept", None, None)
    tree.apply("scope", "park", None, "以后再说")
    frontier = [node.id for node in tree.frontier()]
    assert frontier == ["constraints", "risks"]
    assert tree.node("deps") is not None and tree.node("deps").status == "parked"
    assert tree.node("next") is not None and tree.node("next").status == "parked"
    assert tree.node("constraints") is not None and tree.node("constraints").status == "open"
    assert tree.node("tradeoffs") is not None and tree.node("tradeoffs").status == "open"


def test_template_fallback_without_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CLARIFY_LLM_API_KEY", raising=False)
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    text, recommendation, reason = render_question(
        node_id="goal",
        keyword="考研数学",
        domain_hint=None,
        text="关于「考研数学」，这次怎样才算成功？",
        recommendation="先定义一个可验收的结果",
        reason="成功标准决定检索范围。",
    )
    assert text.startswith("关于")
    assert recommendation
    assert reason


def test_polish_falls_back_when_llm_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CLARIFY_LLM_API_KEY", "test-key")

    class Boom:
        def __init__(self, *args, **kwargs) -> None:
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args) -> bool:
            return False

        def post(self, *args, **kwargs):
            raise httpx.ConnectError("offline")

    monkeypatch.setattr(httpx, "Client", Boom)
    text, recommendation, reason = render_question(
        node_id="goal",
        keyword="考研数学",
        domain_hint="learning",
        text="模板问题",
        recommendation="模板推荐",
        reason="模板理由",
    )
    assert (text, recommendation, reason) == ("模板问题", "模板推荐", "模板理由")


def test_polish_uses_llm_text_when_available(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CLARIFY_LLM_API_KEY", "test-key")

    class FakeResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            return {
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                {
                                    "text": "润色后的问题？",
                                    "recommendation": "润色后的建议",
                                    "reason": "润色后的理由",
                                },
                                ensure_ascii=False,
                            )
                        }
                    }
                ]
            }

    class FakeClient:
        def __init__(self, *args, **kwargs) -> None:
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args) -> bool:
            return False

        def post(self, url, headers, json):  # noqa: A002
            assert headers["Authorization"] == "Bearer test-key"
            assert url.endswith("/chat/completions")
            return FakeResponse()

    monkeypatch.setattr(httpx, "Client", FakeClient)
    text, recommendation, reason = render_question(
        node_id="goal",
        keyword="考研数学",
        domain_hint=None,
        text="模板问题",
        recommendation="模板推荐",
        reason="模板理由",
    )
    assert text == "润色后的问题？"
    assert recommendation == "润色后的建议"
    assert reason == "润色后的理由"


def test_empty_polish_recommendation_keeps_template(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "app.clarifier.questioner.polish_question",
        lambda **kwargs: ("还有字", "", "有理由"),
    )
    text, recommendation, reason = render_question(
        node_id="goal",
        keyword="考研数学",
        domain_hint=None,
        text="模板问题",
        recommendation="模板推荐",
        reason="模板理由",
    )
    assert (text, recommendation, reason) == ("模板问题", "模板推荐", "模板理由")


def test_start_asks_one_question_with_recommendation(client: TestClient, token: str) -> None:
    denied = client.post("/clarify/start", json={"keyword": "考研数学复盘"})
    assert denied.status_code == 401

    response = client.post(
        "/clarify/start",
        headers=headers(token),
        json={"keyword": "考研数学复盘", "domain_hint": "learning"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["state"] == "active"
    assert isinstance(body["question"], dict)
    assert "questions" not in body
    assert body["question"]["id"] == "goal"
    assert body["question"]["recommendation"]
    assert body["question"]["reason"]
    assert "两周内" in body["question"]["recommendation"]
    assert body["tree_progress"] == {"resolved": 0, "open": 7, "parked": 0}


def test_accept_edit_park_then_confirm_brief_is_readable_by_chat(client: TestClient, token: str) -> None:
    started = client.post(
        "/clarify/start",
        headers=headers(token),
        json={"keyword": "考研数学复盘怎么做", "max_questions": 7},
    )
    assert started.status_code == 200
    session_id = started.json()["session_id"]
    goal = started.json()["question"]
    assert isinstance(goal, dict)

    accepted = client.post(
        f"/clarify/{session_id}/answer",
        headers=headers(token),
        json={"question_id": goal["id"], "action": "accept", "value": None, "park_reason": None},
    )
    assert accepted.status_code == 200
    assert accepted.json()["question"]["id"] == "scope"
    assert accepted.json()["tree_progress"]["resolved"] == 1

    edited = client.post(
        f"/clarify/{session_id}/answer",
        headers=headers(token),
        json={"question_id": "scope", "action": "edit", "value": "只看近三年真题"},
    )
    assert edited.status_code == 200
    assert edited.json()["question"]["id"] == "constraints"

    missing_value = client.post(
        f"/clarify/{session_id}/answer",
        headers=headers(token),
        json={"question_id": "constraints", "action": "edit", "value": "  "},
    )
    assert missing_value.status_code == 422

    parked = client.post(
        f"/clarify/{session_id}/answer",
        headers=headers(token),
        json={"question_id": "constraints", "action": "park", "park_reason": "以后再说"},
    )
    assert parked.status_code == 200
    # Parking constraints blocks tradeoffs and next. deps only needs scope, and
    # risks only needs goal, so both stay askable. One question comes back.
    assert isinstance(parked.json()["question"], dict)
    assert parked.json()["question"]["id"] == "deps"
    assert parked.json()["question"]["recommendation"]
    assert parked.json()["tree_progress"]["parked"] >= 1

    wrong = client.post(
        f"/clarify/{session_id}/answer",
        headers=headers(token),
        json={"question_id": "goal", "action": "accept"},
    )
    assert wrong.status_code == 409

    deps_done = client.post(
        f"/clarify/{session_id}/answer",
        headers=headers(token),
        json={"question_id": "deps", "action": "accept"},
    )
    assert deps_done.status_code == 200
    risks_q = deps_done.json()["question"]
    assert risks_q["id"] == "risks"
    assert risks_q["recommendation"]
    assert risks_q["reason"]

    resolved = client.post(
        f"/clarify/{session_id}/answer",
        headers=headers(token),
        json={"question_id": "risks", "action": "accept"},
    )
    assert resolved.status_code == 200
    assert resolved.json()["state"] == "ready_to_confirm"
    assert resolved.json()["question"] is None
    draft = resolved.json()["brief"]
    assert draft["status"] == "clarified"
    assert draft["goal"] == goal["recommendation"]
    assert draft["scope"] == "只看近三年真题"
    assert draft["dependencies"] == parked.json()["question"]["recommendation"]
    assert draft["risks"] == risks_q["recommendation"]
    parked_ids = {item["id"] for item in draft["parked"]}
    assert {"constraints", "tradeoffs", "next"} <= parked_ids
    assert "deps" not in parked_ids
    assert "risks" not in parked_ids
    assert "scope" not in parked_ids

    early_chat = client.post(
        "/chat",
        headers=headers(token),
        json={"brief_id": session_id, "message": "先别查"},
    )
    assert early_chat.status_code == 409

    revised = client.post(
        f"/clarify/{session_id}/answer",
        headers=headers(token),
        json={"question_id": "scope", "action": "edit", "value": "只看线性代数"},
    )
    assert revised.status_code == 200
    assert revised.json()["state"] == "ready_to_confirm"
    assert revised.json()["brief"]["scope"] == "只看线性代数"

    confirmed = client.post(f"/clarify/{session_id}/confirm", headers=headers(token))
    assert confirmed.status_code == 200
    confirmed_body = confirmed.json()
    assert confirmed_body["state"] == "confirmed"
    assert confirmed_body["brief_id"] == session_id
    assert confirmed_body["brief"]["success_criteria"] == goal["recommendation"]
    assert confirmed_body["conversation_id"]

    again = client.post(f"/clarify/{session_id}/confirm", headers=headers(token), json={})
    assert again.status_code == 200
    assert again.json()["brief"]["scope"] == "只看线性代数"

    chat = client.post(
        "/chat",
        headers=headers(token),
        json={
            "message": "按这个范围查",
            "mode": "personal",
            "brief_id": confirmed_body["brief_id"],
            "conversation_id": confirmed_body["conversation_id"],
            "sources": ["local"],
        },
    )
    assert chat.status_code == 200
    events = parse_sse(chat.text)
    assert "delta" in events and "done" in events
    assert events["done"]["brief"]["scope"] == "只看线性代数"
    assert events["done"]["brief"]["goal"] == goal["recommendation"]
    assert events["done"]["retrieval"]["accepted_brief"] is True
    assert events["done"]["retrieval"]["brief_status"] == "clarified"
    assert events["done"]["retrieval"]["hits"] == []

    follow = client.post(
        "/chat",
        headers=headers(token),
        json={"conversation_id": events["done"]["conversation_id"], "message": "继续"},
    )
    assert follow.status_code == 200
    follow_events = parse_sse(follow.text)
    assert follow_events["done"]["brief"]["scope"] == "只看线性代数"
    assert follow_events["done"]["brief_id"] == session_id


def test_skip_writes_skipped_brief_into_retrieval(client: TestClient, token: str) -> None:
    skipped = client.post(
        "/clarify/start",
        headers=headers(token),
        json={"keyword": "先直接查", "skip": True},
    )
    assert skipped.status_code == 200
    body = skipped.json()
    assert body["state"] == "confirmed"
    assert body["question"] is None
    assert body["brief"]["brief"] == "skipped"
    assert body["brief"]["status"] == "skipped"
    assert body["brief_id"] == body["session_id"]

    chat = client.post(
        "/chat",
        headers=headers(token),
        json={"brief_id": body["brief_id"], "message": "直接检索"},
    )
    events = parse_sse(chat.text)
    assert events["done"]["brief"]["brief"] == "skipped"
    assert events["done"]["retrieval"]["brief_status"] == "skipped"

    direct = client.post(
        "/chat",
        headers=headers(token),
        json={"clarify": "skip", "message": "跳过澄清直接问", "mode": "personal"},
    )
    assert direct.status_code == 200
    direct_events = parse_sse(direct.text)
    assert direct_events["done"]["brief"]["brief"] == "skipped"
    assert direct_events["done"]["retrieval"]["brief_status"] == "skipped"
    assert "event: delta" in direct.text
    assert "event: done" in direct.text


def test_max_questions_is_configurable(client: TestClient, token: str, monkeypatch: pytest.MonkeyPatch) -> None:
    short = client.post(
        "/clarify/start",
        headers=headers(token),
        json={"keyword": "短流程", "max_questions": 2},
    )
    assert short.json()["tree_progress"]["open"] == 2
    session_id = short.json()["session_id"]
    first = short.json()["question"]["id"]
    nxt = client.post(
        f"/clarify/{session_id}/answer",
        headers=headers(token),
        json={"question_id": first, "action": "accept"},
    )
    assert nxt.json()["question"]["id"] == "scope"
    done = client.post(
        f"/clarify/{session_id}/answer",
        headers=headers(token),
        json={"question_id": "scope", "action": "park"},
    )
    assert done.json()["state"] == "ready_to_confirm"
    assert done.json()["tree_progress"]["open"] == 0

    monkeypatch.setenv("CLARIFY_MAX_QUESTIONS", "3")
    capped = client.post("/clarify/start", headers=headers(token), json={"keyword": "环境变量"})
    assert capped.json()["tree_progress"] == {"resolved": 0, "open": 3, "parked": 0}


def test_unfinished_session_survives_restart(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("SIDECAR_TOKEN", "test-token")
    monkeypatch.delenv("CLARIFY_LLM_API_KEY", raising=False)
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    auth = headers("test-token")

    with TestClient(app) as first:
        started = first.post("/clarify/start", headers=auth, json={"keyword": "未完成的会话"})
        assert started.status_code == 200
        session_id = started.json()["session_id"]
        recommendation = started.json()["question"]["recommendation"]
        answered = first.post(
            f"/clarify/{session_id}/answer",
            headers=auth,
            json={"question_id": "goal", "action": "accept"},
        )
        assert answered.json()["question"]["id"] == "scope"

    engine = create_engine(database_url(str(tmp_path)))
    with engine.connect() as conn:
        row = conn.execute(
            text("SELECT state, tree_json, brief_json FROM clarify_session WHERE id = :id"),
            {"id": session_id},
        ).one()
    engine.dispose()
    assert row.state == "active"
    stored = json.loads(row.tree_json) if isinstance(row.tree_json, str) else row.tree_json
    goal = next(node for node in stored["nodes"] if node["id"] == "goal")
    assert goal["status"] == "resolved"
    assert goal["decision"] == recommendation
    raw_brief = json.loads(row.brief_json) if isinstance(row.brief_json, str) else row.brief_json
    assert raw_brief is None

    with TestClient(app) as second:
        active = second.get("/clarify/active", headers=auth)
        assert active.status_code == 200
        restored = active.json()["session"]
        assert restored["session_id"] == session_id
        assert restored["state"] == "active"
        assert restored["question"]["id"] == "scope"
        assert restored["question"]["recommendation"]
        assert restored["question"]["reason"]
        continued = second.post(
            f"/clarify/{session_id}/answer",
            headers=auth,
            json={"question_id": "scope", "action": "edit", "value": "只问范围"},
        )
        assert continued.status_code == 200
        assert continued.json()["question"]["id"] == "constraints"


def test_loopback_preflight_does_not_require_a_token(client: TestClient) -> None:
    response = client.options(
        "/clarify/start",
        headers={
            "Origin": "http://127.0.0.1:5173",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "content-type,x-sidecar-token",
        },
    )
    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "http://127.0.0.1:5173"


def test_unknown_session_and_plain_chat_stub(client: TestClient, token: str) -> None:
    missing = client.post(
        "/clarify/999/answer",
        headers=headers(token),
        json={"question_id": "goal", "action": "accept"},
    )
    assert missing.status_code == 404
    assert client.post("/clarify/999/confirm", headers=headers(token)).status_code == 404
    assert client.get("/clarify/active", headers=headers(token)).json() == {"session": None}

    chat = client.post("/chat", headers=headers(token))
    assert chat.status_code == 200
    assert "event: delta" in chat.text
    assert "event: done" in chat.text
    assert "（M0 stub）" in chat.text
    events = parse_sse(chat.text)
    assert "brief_id" not in events["done"]

    health = client.get("/health", headers=headers(token))
    assert health.status_code == 200
    assert health.json()["models"] == {"embedder": "not_loaded", "reranker": "not_loaded"}
