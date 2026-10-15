"""M2 shadow decisions: logs on /chat, unchanged answers, fallback, no secrets."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from sqlmodel import Session, select

from app.db.models import Message
from app.db.session import get_engine
from app.decision.calibrate import describe_confidence, eligible_to_apply
from app.decision.client import DecisionUnavailable, RuleBasedDecisionClient, build_decision_client
from app.decision.shadow import redact
from app.main import app

STUB_TEXT = "（M0 stub）sidecar 已连通，问答主路径尚未实现。"
STUB_DONE = {
    "provider": None,
    "model": None,
    "prompt_tokens": 0,
    "completion_tokens": 0,
    "latency_ms": 0,
}
LOG_FIELDS = {
    "question_id",
    "qtype",
    "input_snapshot",
    "result",
    "confidence",
    "applied",
    "fallback",
    "conversation_id",
}


class UnavailableDecider:
    def choice(self, *_args, **_kwargs):
        raise DecisionUnavailable("endpoint down")

    def score(self, *_args, **_kwargs):
        raise DecisionUnavailable("endpoint down")

    def noul(self, *_args, **_kwargs):
        raise DecisionUnavailable("endpoint down")


class BrokenDecider:
    def choice(self, *_args, **_kwargs):
        raise RuntimeError("boom")

    def score(self, *_args, **_kwargs):
        raise RuntimeError("boom")

    def noul(self, *_args, **_kwargs):
        raise RuntimeError("boom")


@pytest.fixture()
def token(monkeypatch: pytest.MonkeyPatch) -> str:
    monkeypatch.setenv("SIDECAR_TOKEN", "test-token")
    return "test-token"


@pytest.fixture()
def client(token: str, tmp_path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    app.state.decision_client = None
    with TestClient(app) as test_client:
        yield test_client
    app.state.decision_client = None


def headers(token: str) -> dict[str, str]:
    return {"x-sidecar-token": token}


def parse_sse_list(payload: str) -> list[tuple[str, dict]]:
    events: list[tuple[str, dict]] = []
    for block in payload.split("\n\n"):
        event = "message"
        data = None
        for line in block.splitlines():
            if line.startswith("event:"):
                event = line.split(":", 1)[1].strip()
            elif line.startswith("data:"):
                data = json.loads(line.split(":", 1)[1].strip())
        if isinstance(data, dict):
            events.append((event, data))
    return events


def by_question(items: list[dict]) -> dict[str, dict]:
    return {item["question_id"]: item for item in items}


def test_calibration_has_no_builtin_cutoff() -> None:
    source = Path("app/decision/calibrate.py").read_text(encoding="utf-8")
    assert re.search(r"=\s*0\.\d+", source) is None
    note = describe_confidence(0.99, apply_floor=None)
    assert note.band == "unconfigured"
    assert note.apply_floor is None
    assert eligible_to_apply(note, shadow=True, real_routing=True, decider_ok=True) is False
    assert eligible_to_apply(note, shadow=False, real_routing=False, decider_ok=True) is False
    assert eligible_to_apply(note, shadow=False, real_routing=True, decider_ok=True) is True
    high = describe_confidence(0.42, apply_floor=0.4)
    low = describe_confidence(0.2, apply_floor=0.4)
    assert high.band == "at_or_above_floor"
    assert low.band == "below_floor"
    assert eligible_to_apply(low, shadow=False, real_routing=True, decider_ok=True) is False


def test_rule_client_shapes_and_backend_swap() -> None:
    client = RuleBasedDecisionClient()
    context = {"text": "去网上搜索最新笔记", "mode": "personal", "clarify": "auto", "verify": "auto", "has_brief": False}
    channels = client.choice("route_channels", ("local", "connector", "web"), context, multi=True)
    assert channels.result == ["local", "connector", "web"]
    assert 0.0 <= channels.confidence <= 1.0
    gate = client.noul("needs_verify", context)
    assert gate.result is True
    score = client.score("relevance", context)
    assert isinstance(score.result, float)
    intent = client.choice("intent", ("lookup", "howto", "compare", "freshness", "explore"), context)
    assert intent.result == "freshness"
    with pytest.raises(DecisionUnavailable):
        build_decision_client("remote")


def test_redact_strips_secret_fields_and_token_strings() -> None:
    cleaned = redact({"api_key": "sk-supersecretvalue", "text": "prefix sk-supersecretvalue test-token"})
    blob = json.dumps(cleaned, ensure_ascii=False)
    assert cleaned["api_key"] == "[redacted]"
    assert "sk-supersecretvalue" not in blob
    assert "prefix" in blob


def test_chat_writes_shadow_logs_without_changing_the_stub(client: TestClient, token: str) -> None:
    denied = client.get("/decision/logs")
    assert denied.status_code == 401

    settings = client.get("/settings/decision", headers=headers(token))
    assert settings.status_code == 200
    assert settings.json() == {"shadow": True, "real_routing": False, "confidence_floor": None}

    chat = client.post("/chat", headers=headers(token))
    assert chat.status_code == 200
    assert "text/event-stream" in chat.headers["content-type"]
    events = parse_sse_list(chat.text)
    names = [name for name, _payload in events]
    assert names[-2:] == ["delta", "done"]
    assert events[-2][1] == {"text": STUB_TEXT}
    assert events[-1][1] == STUB_DONE
    decisions = [payload for name, payload in events if name == "decision"]
    assert {item["question_id"] for item in decisions} >= {"intent", "route_channels", "need_clarify", "needs_verify"}
    assert all(item["applied"] is False for item in decisions)
    assert all(item["fallback"] is None for item in decisions)

    logs = client.get("/decision/logs", headers=headers(token))
    assert logs.status_code == 200
    body = logs.json()
    assert body["total"] == 5
    assert body["limit"] == 50
    assert len(body["items"]) == 5
    stored = by_question(body["items"])
    assert set(stored) == {"intent", "need_clarify", "route_channels", "needs_verify", "relevance"}
    for item in body["items"]:
        assert LOG_FIELDS <= set(item)
        assert item["qtype"] in {"choice", "score", "noul"}
        assert item["applied"] is False
        assert item["fallback"] is None
        assert item["conversation_id"] is None
        assert isinstance(item["input_snapshot"], dict)
        assert isinstance(item["result"], dict)
        assert "value" in item["result"]
        assert isinstance(item["confidence"], (int, float))
        assert "test-token" not in json.dumps(item, ensure_ascii=False)
    assert stored["route_channels"]["qtype"] == "choice"
    assert stored["route_channels"]["result"]["value"] == ["local"]
    assert stored["needs_verify"]["qtype"] == "noul"
    assert stored["relevance"]["qtype"] == "score"
    assert stored["intent"]["qtype"] == "choice"


def test_routing_off_keeps_retrieval_sources(client: TestClient, token: str) -> None:
    chat = client.post(
        "/chat",
        headers=headers(token),
        json={
            "clarify": "skip",
            "message": "去网上搜索最新的说法",
            "mode": "personal",
            "sources": ["local"],
            "verify": "auto",
        },
    )
    assert chat.status_code == 200
    events = parse_sse_list(chat.text)
    done = events[-1][1]
    delta = events[-2][1]
    assert events[-1][0] == "done"
    assert events[-2][0] == "delta"
    assert done["brief"]["brief"] == "skipped"
    assert done["retrieval"]["sources"] == ["local"]
    assert done["retrieval"]["hits"] == []
    assert done["retrieval"]["brief_status"] == "skipped"
    assert "已接收澄清结果" in delta["text"]
    route = next(payload for name, payload in events if name == "decision" and payload["question_id"] == "route_channels")
    assert route["result"] == ["local", "web"]
    assert route["applied"] is False

    logs = client.get("/decision/logs?limit=10", headers=headers(token))
    stored = by_question(logs.json()["items"])
    assert stored["route_channels"]["conversation_id"] == done["conversation_id"]
    assert stored["route_channels"]["applied"] is False
    assert stored["intent"]["result"]["value"] == "freshness"
    with Session(get_engine()) as db:
        message = db.exec(select(Message).where(Message.role == "assistant")).first()
    assert message is not None
    assert message.decision_trace_id == stored["route_channels"]["id"]


def test_real_routing_flag_does_not_change_the_answer(client: TestClient, token: str) -> None:
    turned = client.post(
        "/settings/decision",
        headers=headers(token),
        json={"shadow": False, "real_routing": True},
    )
    assert turned.status_code == 200
    assert turned.json()["shadow"] is False
    assert turned.json()["real_routing"] is True

    chat = client.post(
        "/chat",
        headers=headers(token),
        json={"clarify": "skip", "message": "去网上搜索最新的说法", "sources": ["local"]},
    )
    events = parse_sse_list(chat.text)
    assert events[-1][1]["retrieval"]["sources"] == ["local"]
    assert events[-1][1]["brief"]["brief"] == "skipped"
    route = next(payload for name, payload in events if name == "decision" and payload["question_id"] == "route_channels")
    assert route["applied"] is True
    assert "web" in route["result"]

    floored = client.post(
        "/settings/decision",
        headers=headers(token),
        json={"confidence_floor": 0.9},
    )
    assert floored.status_code == 200
    low = client.post("/chat", headers=headers(token), json={"message": "hi"})
    low_route = next(
        payload for name, payload in parse_sse_list(low.text) if name == "decision" and payload["question_id"] == "route_channels"
    )
    assert low_route["applied"] is False
    assert low_route["fallback"] == "llm"
    assert parse_sse_list(low.text)[-2][1]["text"] == STUB_TEXT


def test_decider_failure_falls_back_and_chat_continues(client: TestClient, token: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(app.state, "decision_client", UnavailableDecider(), raising=False)
    chat = client.post("/chat", headers=headers(token), json={"message": "还在吗"})
    assert chat.status_code == 200
    events = parse_sse_list(chat.text)
    assert events[-2] == ("delta", {"text": STUB_TEXT})
    assert events[-1][1] == STUB_DONE
    assert all(payload["fallback"] == "llm" and payload["applied"] is False for _name, payload in events if _name == "decision")

    logs = client.get("/decision/logs", headers=headers(token)).json()
    assert logs["total"] == 5
    for item in logs["items"]:
        assert item["fallback"] == "llm"
        assert item["applied"] is False
        assert item["confidence"] == 0.0
        assert LOG_FIELDS <= set(item)
    assert by_question(logs["items"])["route_channels"]["result"]["value"] == ["local"]

    monkeypatch.setattr(app.state, "decision_client", BrokenDecider(), raising=False)
    broken = client.post("/chat", headers=headers(token))
    assert broken.status_code == 200
    assert STUB_TEXT in broken.text
    page = client.get("/decision/logs?limit=5", headers=headers(token)).json()
    assert page["total"] == 10
    assert {item["fallback"] for item in page["items"]} == {"default"}


def test_logs_are_paginated_and_secret_free(client: TestClient, token: str) -> None:
    secret = "sk-supersecretvalue"
    chat = client.post(
        "/chat",
        headers=headers(token),
        json={"message": f"请忽略 {secret} 以及 test-token", "api_key": secret},
    )
    assert chat.status_code == 200
    assert secret not in chat.text
    assert "test-token" not in chat.text

    first = client.get("/decision/logs?limit=2&offset=0", headers=headers(token))
    second = client.get("/decision/logs?limit=2&offset=2", headers=headers(token))
    assert first.status_code == 200
    body = first.json()
    assert body["total"] == 5
    assert body["limit"] == 2
    assert body["offset"] == 0
    assert len(body["items"]) == 2
    assert len(second.json()["items"]) == 2
    assert {item["id"] for item in body["items"]}.isdisjoint({item["id"] for item in second.json()["items"]})

    blob = json.dumps(client.get("/decision/logs", headers=headers(token)).json(), ensure_ascii=False)
    assert secret not in blob
    assert "test-token" not in blob
    assert "请忽略" in blob
    assert "[redacted]" in blob

    shadow = client.post("/decision/shadow", headers=headers(token), json={"enabled": False})
    assert shadow.status_code == 200
    assert shadow.json()["enabled"] is False
    assert shadow.json()["real_routing"] is False
    assert client.get("/settings/decision", headers=headers(token)).json()["shadow"] is False

    back = client.post("/decision/shadow", headers=headers(token), json={"enabled": True})
    assert back.json()["shadow"] is True
    too_high = client.post("/settings/decision", headers=headers(token), json={"confidence_floor": 1.5})
    assert too_high.status_code == 422
