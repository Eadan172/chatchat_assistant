"""M6 ask pipeline. External calls stay on the local mock."""

from __future__ import annotations

import base64
import json
import logging
import os
import stat
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

import pytest
from fastapi.testclient import TestClient

from app.ask.synthesize import (
    CONFLICT_PHRASE,
    claims_conflict,
    fuse_hits,
    guard_citations,
    normalize_url,
    synthesize,
)
from app.connectors.ima_client import reset_client_state
from app.decision.client import DecisionUnavailable, RuleBasedDecisionClient, build_decision_client
from app.main import app
from app.web_search.live import NO_WEB_KEY, parse_hits, search_web, status_ok
from app.web_search.provider import provider_order

DEEPSEEK_KEY = "sk-m6-deepseek-test-secret"
SERPER_KEY = "serper-m6-test-secret-value"
SERPAPI_KEY = "serpapi-m6-test-secret-value"
TAVILY_KEY = "tavily-m6-test-secret-value"
IMA_KEY = "ima-m6-test-secret-value"
IMA_CLIENT = "ima-m6-client"
LOCAL_TEXT = "示例概念由教材提出于1980年，强调基本事实。"
WEB_TEXT = "示例概念提出于1990年，不是从基本事实出发。"
NOTE_TEXT = "示例概念是一种思考方式。"


class MockState:
    def __init__(self) -> None:
        self.calls: list[dict] = []
        self.serper_status = 200
        self.serpapi_status = 200
        self.tavily_status = 200
        self.deepseek_status = 200


STATE = MockState()


class Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        if parsed.path.endswith("/article"):
            self._record("GET", parsed.path, None)
            paragraph = "示例概念的页面正文提到教材与基本事实，并展开了后续论证。" * 8
            body = (
                "<html><head><script>var leaked = 'do-not-keep';</script>"
                "<style>p{color:red}</style></head><body><article><p>"
                + paragraph
                + "</p></article></body></html>"
            )
            self._html(body)
            return
        if parsed.path.endswith("/search.json"):
            self._record("GET", parsed.path, None)
            if STATE.serpapi_status >= 400:
                self._json({"search_metadata": {"status": "Error"}}, status=STATE.serpapi_status)
                return
            self._json(
                {
                    "search_metadata": {"status": "Success", "id": "mock"},
                    "organic_results": [
                        {
                            "title": "必应结果",
                            "link": "https://example.com/bing",
                            "snippet": "必应摘要",
                            "position": 1,
                        }
                    ],
                }
            )
            return
        self._json({"code": 404}, status=404)

    def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers.get("content-length") or 0)
        raw = self.rfile.read(length) if length else b"{}"
        try:
            body = json.loads(raw.decode("utf-8") or "{}")
        except json.JSONDecodeError:
            body = {}
        parsed = urlparse(self.path)
        self._record("POST", parsed.path, body if isinstance(body, dict) else {})
        if parsed.path.endswith("/chat/completions"):
            self._deepseek(body if isinstance(body, dict) else {})
            return
        if parsed.path.endswith("/serper/search"):
            self._serper()
            return
        if parsed.path.endswith("/tavily/search"):
            self._tavily()
            return
        if "openapi/" in parsed.path:
            self._ima(parsed.path, body if isinstance(body, dict) else {})
            return
        self._json({"code": 404}, status=404)

    def log_message(self, fmt: str, *args) -> None:
        return

    def _record(self, method: str, path: str, body: dict | None) -> None:
        query = parse_qs(urlparse(self.path).query)
        STATE.calls.append(
            {
                "method": method,
                "path": path,
                "has_auth": bool(self.headers.get("authorization")),
                "has_serper": bool(self.headers.get("x-api-key")),
                "has_ima": bool(self.headers.get("ima-openapi-apikey")),
                "has_serpapi_param": "api_key" in query,
                "body": body or {},
            }
        )

    def _deepseek(self, body: dict) -> None:
        if STATE.deepseek_status >= 400:
            self._json({"error": {"code": "upstream"}}, status=STATE.deepseek_status)
            return
        messages = body.get("messages") if isinstance(body.get("messages"), list) else []
        system = ""
        if messages and isinstance(messages[0], dict):
            system = str(messages[0].get("content") or "")
        if "摘录" in system:
            content = "因此以网页为准。[2][99]"
        else:
            content = json.dumps(
                {
                    "need_clarify": "yes",
                    "sources": ["web", "ima", "local"],
                    "queries": ["示例概念 定义"],
                    "confidence": 0.81,
                },
                ensure_ascii=False,
            )
        self._json(
            {
                "id": "chatcmpl-mock",
                "choices": [{"message": {"role": "assistant", "content": content}}],
                "usage": {"prompt_tokens": 12, "completion_tokens": 8},
            }
        )

    def _serper(self) -> None:
        if STATE.serper_status >= 400:
            self._json({"message": "nope"}, status=STATE.serper_status)
            return
        self._json(
            {
                "organic": [
                    {
                        "title": "网页甲",
                        "link": "https://example.com/fp?utm_source=newsletter#section",
                        "snippet": WEB_TEXT,
                        "position": 1,
                    },
                    {
                        "title": "网页甲重复",
                        "link": "https://example.com/fp/",
                        "snippet": WEB_TEXT,
                        "position": 2,
                    },
                    {
                        "title": "网页乙",
                        "link": "https://example.com/other",
                        "snippet": "另一篇介绍。",
                        "position": 3,
                    },
                ]
            }
        )

    def _tavily(self) -> None:
        if STATE.tavily_status >= 400:
            self._json({"error": "nope"}, status=STATE.tavily_status)
            return
        self._json(
            {
                "results": [
                    {
                        "title": "Tavily",
                        "url": "https://example.com/tavily",
                        "content": "Tavily 摘要",
                        "score": 0.5,
                    }
                ]
            }
        )

    def _ima(self, path: str, body: dict) -> None:
        key = self.headers.get("ima-openapi-apikey") or ""
        if key != IMA_KEY:
            self._json({"code": 20004, "msg": "apiKey 鉴权失败", "data": {}}, status=401)
            return
        if path.endswith("/search_knowledge_base"):
            self._json(
                {
                    "code": 0,
                    "msg": "成功",
                    "data": {
                        "info_list": [
                            {
                                "kb_id": "kb-demo",
                                "kb_name": "示例概念",
                                "description": "原理库",
                            }
                        ]
                    },
                }
            )
            return
        if path.endswith("/search_knowledge"):
            if body.get("knowledge_base_id") != "kb-demo":
                self._json({"code": 220004, "msg": "invalid knowledge_base_id", "data": {}}, status=200)
                return
            self._json(
                {
                    "code": 0,
                    "msg": "成功",
                    "data": {
                        "info_list": [
                            {
                                "media_id": "media-1",
                                "title": "原理条目",
                                "parent_folder_id": "folder-1",
                                "highlight_content": LOCAL_TEXT,
                                "media_type": 13,
                            }
                        ]
                    },
                }
            )
            return
        if path.endswith("/search_note"):
            self._json(
                {
                    "code": 0,
                    "msg": "成功",
                    "data": {
                        "search_note_infos": [
                            {
                                "note_book_info": {
                                    "note_id": "note-1",
                                    "title": "示例笔记",
                                    "summary": "摘要",
                                },
                                "highlightInfo": {
                                    "doc_title": "示例笔记",
                                    "highlight_content": NOTE_TEXT,
                                },
                            }
                        ]
                    },
                }
            )
            return
        self._json({"code": 110012, "msg": "接口无效", "data": {}}, status=200)

    def _html(self, body: str, status: int = 200) -> None:
        raw = body.encode("utf-8")
        self.send_response(status)
        self.send_header("content-type", "text/html; charset=utf-8")
        self.send_header("content-length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def _json(self, payload: dict, status: int = 200) -> None:
        raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)


@pytest.fixture()
def token(monkeypatch: pytest.MonkeyPatch) -> str:
    monkeypatch.setenv("SIDECAR_TOKEN", "test-token")
    for name in (
        "LLM_API_KEY",
        "OPENAI_API_KEY",
        "LLM_BASE_URL",
        "OPENAI_BASE_URL",
        "CLARIFY_LLM_API_KEY",
        "EMBED_API_KEY",
        "EMBED_BASE_URL",
        "DEEPSEEK_API_KEY",
        "SERPER_API_KEY",
        "SERPAPI_API_KEY",
        "TAVILY_API_KEY",
        "IMA_CLIENT_ID",
        "IMA_API_KEY",
        "FEATURE_ASK",
        "FEATURE_IMA",
        "FEATURE_VERIFY",
    ):
        monkeypatch.delenv(name, raising=False)
    return "test-token"


@pytest.fixture()
def client(token: str, tmp_path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("EMBED_PROVIDER", "hash")
    app.state.decision_client = None
    with TestClient(app) as test_client:
        yield test_client
    app.state.decision_client = None


@pytest.fixture()
def mock_server(monkeypatch: pytest.MonkeyPatch) -> str:
    STATE.calls.clear()
    STATE.serper_status = 200
    STATE.serpapi_status = 200
    STATE.tavily_status = 200
    STATE.deepseek_status = 200
    monkeypatch.setenv("ASK_WEB_FETCH_N", "0")
    monkeypatch.setenv("IMA_MIN_INTERVAL_S", "0")
    monkeypatch.setenv("IMA_MAX_PER_MINUTE", "1000")
    monkeypatch.setenv("IMA_CACHE_TTL_S", "0")
    reset_client_state()
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        yield base
    finally:
        server.shutdown()
        reset_client_state()


def headers(token: str) -> dict[str, str]:
    return {"x-sidecar-token": token}


def parse_sse(payload: str) -> list[tuple[str, dict]]:
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


def joined_delta(events: list[tuple[str, dict]]) -> str:
    return "".join(payload.get("text") or "" for name, payload in events if name == "delta")


def test_flag_off_keeps_ask_closed_and_stub_chain(client: TestClient, token: str) -> None:
    assert provider_order() == ["google", "fallback"]
    denied = client.post("/ask", headers=headers(token), json={"message": "示例概念"})
    assert denied.status_code == 501
    settings = client.get("/settings/ask", headers=headers(token))
    assert settings.status_code == 501
    chat = client.post("/chat", headers=headers(token), json={"message": "你好"})
    assert chat.status_code == 200
    assert "event: delta" in chat.text


def test_url_rrf_conflict_and_citation_guard() -> None:
    dirty = "https://Example.com/fp/?utm_source=a&gclid=1#section"
    clean = "https://example.com/fp"
    assert normalize_url(dirty) == clean
    assert normalize_url("https://example.com/fp/") == clean
    merged = fuse_hits(
        {
            "web": [
                {"title": "甲", "url": dirty, "text": "短", "position": 2},
                {"title": "乙", "url": clean, "text": "另一段短文本", "position": 1},
            ]
        }
    )
    assert len(merged) == 1
    assert len(merged[0].origins) == 2
    assert merged[0].url == clean

    shared = "相同的一段足够长的重复内容用于向量去重"
    other = "天文观测记录和青椒腌制完全不是一回事所以不应该合并"
    fused = fuse_hits(
        {
            "local": [{"title": "本地", "url": "", "text": shared, "filename": "a.md", "freshness_state": "fresh"}],
            "web": [
                {"title": "网", "url": "https://example.com/a", "text": shared, "position": 1, "freshness_state": "unknown"},
                {"title": "别", "url": "https://example.com/b", "text": other, "position": 2, "freshness_state": "unknown"},
            ],
        }
    )
    duplicated = next(item for item in fused if any(origin.source == "local" for origin in item.origins))
    assert {origin.source for origin in duplicated.origins} >= {"local", "web"}
    assert duplicated.score == pytest.approx(2 / 61, rel=1e-6)
    single = next(item for item in fused if item.title == "别")
    assert single.score == pytest.approx(1 / 62, rel=1e-6)
    assert claims_conflict(LOCAL_TEXT, WEB_TEXT)
    assert not claims_conflict(LOCAL_TEXT, LOCAL_TEXT)
    assert guard_citations("结论 [1] 以及 [99]。", {1}) == "结论 [1] 以及 。"
    captured: dict = {}

    def complete(messages: list[dict[str, str]]) -> tuple[str, int, int]:
        captured["messages"] = messages
        return "因此以网页为准。[1][99]", 4, 5

    result = synthesize(
        "示例概念",
        {
            "local": [{"title": "本地", "text": LOCAL_TEXT, "filename": "a.md", "freshness_state": "fresh"}],
            "web": [{"title": "网页", "url": "https://example.com/fp", "text": WEB_TEXT, "freshness_state": "unknown"}],
        },
        complete=complete,
    )
    assert CONFLICT_PHRASE in result.text
    assert "以网页为准" not in result.text
    assert "[99]" not in result.text
    assert "1980" in result.text and "1990" in result.text
    assert "无法核实" in result.text
    blob = json.dumps(captured["messages"], ensure_ascii=False)
    assert "http://" not in blob and "https://" not in blob
    assert "[1]" in blob


def test_provider_parsers_accept_candidate_keys() -> None:
    serper = parse_hits(
        {"organic_results": [{"title": "t", "url": "https://example.com/a", "content": "c", "position": "2"}]},
        list_keys=("organic", "organic_results", "results"),
        link_keys=("link", "url", "href"),
        provider="serper",
    )
    assert serper[0]["url"] == "https://example.com/a"
    assert serper[0]["text"] == "c"
    assert serper[0]["position"] == 2
    tavily = parse_hits(
        {"results": [{"title": "t", "url": "https://example.com/b", "content": "body", "score": 0.4}]},
        list_keys=("results",),
        link_keys=("url", "link", "href"),
        provider="tavily",
    )
    assert tavily[0]["url"] == "https://example.com/b"
    assert tavily[0]["score"] == pytest.approx(0.4)
    assert status_ok({"search_metadata": {"status": "Success"}})
    assert not status_ok({"search_metadata": {"status": "Error"}})


def test_web_fallback_and_missing_key(monkeypatch: pytest.MonkeyPatch, tmp_path, mock_server: str) -> None:
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    for name in ("SERPER_API_KEY", "SERPAPI_API_KEY", "TAVILY_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    quiet = search_web("示例概念")
    assert quiet.hits == []
    assert quiet.notice == NO_WEB_KEY

    monkeypatch.setenv("SERPER_API_KEY", SERPER_KEY)
    monkeypatch.setenv("SERPAPI_API_KEY", SERPAPI_KEY)
    monkeypatch.setenv("TAVILY_API_KEY", TAVILY_KEY)
    monkeypatch.setenv("SERPER_BASE_URL", f"{mock_server}/serper")
    monkeypatch.setenv("SERPAPI_BASE_URL", f"{mock_server}/serpapi")
    monkeypatch.setenv("TAVILY_BASE_URL", f"{mock_server}/tavily")
    STATE.serper_status = 500
    STATE.calls.clear()
    found = search_web("示例概念")
    assert found.provider == "serpapi"
    assert found.hits[0]["title"] == "必应结果"
    paths = [call["path"] for call in STATE.calls]
    assert any(path.endswith("/serper/search") for path in paths)
    assert any(path.endswith("/search.json") for path in paths)
    assert not any(path.endswith("/tavily/search") for path in paths)
    assert SERPAPI_KEY not in json.dumps(found.hits, ensure_ascii=False)


def test_note_and_knowledge_use_live_fields() -> None:
    from app.connectors.ima_client import PATH_SEARCH_KNOWLEDGE, PATH_SEARCH_NOTE, ImaResult
    from app.connectors.ima_service import _split_knowledge, search

    class Stub:
        def __init__(self) -> None:
            self.sent: list[tuple[str, dict]] = []

        def post(self, path: str, body: dict, cache: bool | None = None) -> ImaResult:
            self.sent.append((path, body))
            if path == PATH_SEARCH_NOTE:
                data = {
                    "search_note_infos": [
                        {
                            "note_book_info": {"note_id": "note-1", "title": "示例笔记", "summary": "摘要"},
                            "highlightInfo": {"highlight_content": NOTE_TEXT, "doc_title": "示例笔记"},
                        }
                    ]
                }
            else:
                data = {
                    "info_list": [
                        {
                            "media_id": "media-1",
                            "title": "原理条目",
                            "parent_folder_id": "folder-1",
                            "highlight_content": LOCAL_TEXT,
                            "media_type": 13,
                        }
                    ]
                }
            return ImaResult(http_status=200, code=0, msg="成功", data=data, path=path)

    stub = Stub()
    found = search(stub, "示例概念", knowledge_base_id="kb-demo")  # type: ignore[arg-type]
    note = next(hit for hit in found["hits"] if hit["kind"] == "note")
    media = next(hit for hit in found["hits"] if hit["kind"] == "media")
    assert note["id"] == "note-1"
    assert note["title"] == "示例笔记"
    assert NOTE_TEXT in note["snippet"]
    assert media["id"] == "media-1"
    assert media["parent_folder_id"] == "folder-1"
    assert media["media_type"] == 13
    assert media["snippet"] == LOCAL_TEXT
    folders, files = _split_knowledge(
        [{"media_id": "media-1", "title": "原理条目", "parent_folder_id": "folder-1", "media_type": 13}],
        "kb-demo",
    )
    assert folders == []
    assert files[0]["kind"] == "media"
    assert stub.sent[1] == (PATH_SEARCH_KNOWLEDGE, {"query": "示例概念", "cursor": "", "knowledge_base_id": "kb-demo"})


def test_missing_deepseek_falls_back_to_rules(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    with pytest.raises(DecisionUnavailable):
        build_decision_client("deepseek")
    assert isinstance(build_decision_client("rule"), RuleBasedDecisionClient)
    from app.ask.pipeline import decide

    plan = decide("示例概念", brief=None, kb_name="示例概念", clarify="auto")
    assert plan.backend == "rule"
    assert plan.need_clarify == "yes"
    assert "ima" in plan.sources
    assert "local" in plan.sources


def test_ask_secrets_stay_out_of_the_response(client: TestClient, token: str, tmp_path, caplog: pytest.LogCaptureFixture) -> None:
    os.environ["FEATURE_ASK"] = "1"
    caplog.set_level(logging.INFO)
    try:
        saved = client.post(
            "/settings/ask",
            headers=headers(token),
            json={"deepseek_api_key": DEEPSEEK_KEY, "serper_api_key": SERPER_KEY},
        )
        assert saved.status_code == 200, saved.text
        body = saved.json()
        assert body["configured"]["deepseek"] is True
        assert body["configured"]["serper"] is True
        assert body["credential_source"]["deepseek"] == "settings"
        assert DEEPSEEK_KEY not in saved.text
        assert SERPER_KEY not in saved.text
        listed = client.get("/settings/ask", headers=headers(token))
        assert DEEPSEEK_KEY not in listed.text
        path = tmp_path / "secrets" / "ask.json"
        assert path.is_file()
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
        assert DEEPSEEK_KEY not in caplog.text
        assert SERPER_KEY not in caplog.text
    finally:
        os.environ.pop("FEATURE_ASK", None)


def test_first_principles_from_clarify_to_cited_answer(
    client: TestClient,
    token: str,
    mock_server: str,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    monkeypatch.setenv("FEATURE_ASK", "1")
    monkeypatch.setenv("DEEPSEEK_API_KEY", DEEPSEEK_KEY)
    monkeypatch.setenv("DEEPSEEK_BASE_URL", f"{mock_server}/deepseek")
    monkeypatch.setenv("SERPER_API_KEY", SERPER_KEY)
    monkeypatch.setenv("SERPER_BASE_URL", f"{mock_server}/serper")
    monkeypatch.setenv("SERPAPI_API_KEY", SERPAPI_KEY)
    monkeypatch.setenv("SERPAPI_BASE_URL", f"{mock_server}/serpapi")
    monkeypatch.setenv("TAVILY_API_KEY", TAVILY_KEY)
    monkeypatch.setenv("TAVILY_BASE_URL", f"{mock_server}/tavily")
    monkeypatch.setenv("IMA_CLIENT_ID", IMA_CLIENT)
    monkeypatch.setenv("IMA_API_KEY", IMA_KEY)
    monkeypatch.setenv("IMA_BASE_URL", mock_server)
    caplog.set_level(logging.INFO)
    imported = client.post(
        "/library/import",
        headers=headers(token),
        json={
            "filename": "sample-concept.md",
            "content_base64": base64.b64encode(LOCAL_TEXT.encode("utf-8")).decode("ascii"),
        },
    )
    assert imported.status_code == 200, imported.text

    opened = client.post(
        "/ask",
        headers=headers(token),
        json={"message": "示例概念", "kb_name": "示例概念"},
    )
    assert opened.status_code == 200, opened.text
    first = parse_sse(opened.text)
    clarify = next(payload for name, payload in first if name == "clarify_question")
    assert clarify["question"]["id"] == "goal"
    assert "示例概念" in clarify["question"]["text"]
    assert not any(name == "citation" for name, _payload in first)
    session_id = clarify["session_id"]
    question_id = "goal"
    events: list[tuple[str, dict]] = []
    for _ in range(3):
        step = client.post(
            "/ask",
            headers=headers(token),
            json={
                "message": "示例概念",
                "kb_name": "示例概念",
                "session_id": session_id,
                "answers": [{"question_id": question_id, "action": "accept"}],
            },
        )
        assert step.status_code == 200, step.text
        events = parse_sse(step.text)
        follow = next((payload for name, payload in events if name == "clarify_question"), None)
        if follow is None:
            break
        question_id = follow["question"]["id"]
    else:
        raise AssertionError("clarify did not finish")

    searching = next(payload for name, payload in events if name == "searching")
    assert set(searching["sources"]) == {"local", "ima", "web"}
    assert "示例概念 定义" in searching["queries"]
    answer = joined_delta(events)
    assert CONFLICT_PHRASE in answer
    assert "以网页为准" not in answer
    assert "[99]" not in answer
    assert "无法核实" in answer or "已核对" in answer or "可能过期" in answer
    citations = [payload for name, payload in events if name == "citation"]
    assert citations
    numbers = {item["n"] for item in citations}
    assert numbers
    for item in citations:
        assert item["n"] in numbers
        assert isinstance(item.get("origins"), list) and item["origins"]
    web_merged = [
        item
        for item in citations
        if sum(1 for origin in item["origins"] if str(origin.get("url") or "").startswith("https://example.com/fp")) >= 2
    ]
    assert web_merged
    mixed = [
        item
        for item in citations
        if {origin.get("source") for origin in item["origins"]} >= {"local", "ima"}
    ]
    assert mixed
    assert any(origin.get("external_id") == "media-1" for item in citations for origin in item["origins"])
    assert any(origin.get("external_id") == "note-1" for item in citations for origin in item["origins"])
    knowledge = next(call for call in STATE.calls if call["path"].endswith("/search_knowledge"))
    assert knowledge["body"]["knowledge_base_id"] == "kb-demo"
    synthesis = next(
        call
        for call in STATE.calls
        if call["path"].endswith("/chat/completions") and "摘录" in json.dumps(call["body"], ensure_ascii=False)
    )
    sent = json.dumps(synthesis["body"], ensure_ascii=False)
    assert "http://" not in sent and "https://" not in sent
    assert DEEPSEEK_KEY not in sent
    blob = opened.text + json.dumps(events, ensure_ascii=False) + caplog.text
    for secret in (DEEPSEEK_KEY, SERPER_KEY, SERPAPI_KEY, TAVILY_KEY, IMA_KEY):
        assert secret not in blob
    assert not any(call["path"].endswith("/search.json") for call in STATE.calls)
    done = next(payload for name, payload in events if name == "done")
    assert done["phase"] == "answer"
    assert done["provider"] == "deepseek"
    assert done["model"] == "deepseek-chat"
    assert done["conflict"] is True


def test_grouped_answer_when_deepseek_is_down(
    client: TestClient,
    token: str,
    mock_server: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("FEATURE_ASK", "1")
    monkeypatch.setenv("DEEPSEEK_API_KEY", DEEPSEEK_KEY)
    monkeypatch.setenv("DEEPSEEK_BASE_URL", f"{mock_server}/deepseek")
    STATE.deepseek_status = 500
    opened = client.post("/ask", headers=headers(token), json={"message": "示例概念", "clarify": "auto"})
    assert opened.status_code == 200, opened.text
    events = parse_sse(opened.text)
    assert any(name == "clarify_question" for name, _payload in events)
    assert not any(name == "error" for name, _payload in events)

    clear = client.post(
        "/ask",
        headers=headers(token),
        json={"message": "请在网上把示例概念的定义说清楚，范围只要这个概念本身？", "clarify": "auto"},
    )
    assert clear.status_code == 200, clear.text
    body = joined_delta(parse_sse(clear.text))
    assert "未配置联网搜索密钥" in body or "DeepSeek" in body or "没有检索到" in body


GUIDE_TITLE = "ima笔记使用指南"
GUIDE_TEXT = "打开 ima 后点击新建笔记即可完成记录。"
SUBSCRIPTION_TITLE = "订阅库量子周报"
SNIPPET = "短摘要。"
LONG_PAGE = "示例概念的页面正文提到教材与基本事实，并展开了后续论证。" * 8


def test_title_only_subscription_is_related_not_evidence() -> None:
    captured: dict[str, str] = {}

    def complete(messages: list[dict[str, str]]) -> tuple[str, int, int]:
        captured["prompt"] = messages[-1]["content"]
        return "示例概念是一种思考方式。[1]\n有内容以订阅库量子周报为题。\n只有标题，没有正文。", 3, 4

    groups = {
        "ima": [
            {
                "title": SUBSCRIPTION_TITLE,
                "text": SUBSCRIPTION_TITLE,
                "ima_kind": "media",
                "media_type": 6,
                "source": "ima",
                "external_id": "sub-1",
            },
            {
                "title": "示例概念入门",
                "text": "",
                "ima_kind": "note",
                "source": "ima",
                "external_id": "title-note",
            },
            {
                "title": "示例笔记",
                "text": NOTE_TEXT,
                "ima_kind": "note",
                "source": "ima",
                "external_id": "note-1",
            },
            {
                "title": GUIDE_TITLE,
                "text": GUIDE_TEXT,
                "ima_kind": "note",
                "source": "ima",
                "external_id": "guide",
            },
        ]
    }
    result = synthesize("示例概念", groups, complete=complete)
    head, _, tail = result.text.partition("相关条目")
    assert tail
    assert SUBSCRIPTION_TITLE in tail
    assert "示例概念入门" in tail
    assert SUBSCRIPTION_TITLE not in head
    assert "有内容以" not in result.text
    assert "只有标题" not in result.text
    assert GUIDE_TITLE not in result.text
    assert "[1]" in head
    assert "订阅库量子周报" not in captured["prompt"]
    assert GUIDE_TITLE not in captured["prompt"]
    assert "示例概念入门" not in captured["prompt"]
    assert NOTE_TEXT[:8] in captured["prompt"]
    assert [item["title"] for item in result.citations] == ["示例笔记"]
    assert result.citations[0]["n"] == 1

    offline = synthesize("示例概念", groups, missing_key=True)
    offline_head, _, offline_tail = offline.text.partition("相关条目")
    assert SUBSCRIPTION_TITLE in offline_tail
    assert SUBSCRIPTION_TITLE not in offline_head
    assert "有内容以" not in offline.text
    assert "只有标题" not in offline.text
    assert GUIDE_TITLE not in offline.text
    assert all(item["title"] != SUBSCRIPTION_TITLE for item in offline.citations)
    offline_blob = json.dumps(offline.citations, ensure_ascii=False)
    assert GUIDE_TITLE not in offline_blob
    assert "guide" not in offline_blob


def test_citations_keep_only_used_numbers_and_drop_unrelated_notes() -> None:
    captured: dict[str, str] = {}

    def complete(messages: list[dict[str, str]]) -> tuple[str, int, int]:
        captured["prompt"] = messages[-1]["content"]
        return "丙的正文足够用来区分第三条。[3]\n甲的正文足够用来区分第一条。[1]\n编造。[99]", 2, 2

    result = synthesize(
        "示例概念",
        {
            "web": [
                {"title": "甲", "url": "https://example.com/a", "text": "甲的正文足够用来区分第一条。", "position": 1, "source": "web"},
                {"title": "乙", "url": "https://example.com/b", "text": "乙的正文足够用来区分第二条。", "position": 2, "source": "web"},
                {"title": "丙", "url": "https://example.com/c", "text": "丙的正文足够用来区分第三条。", "position": 3, "source": "web"},
            ],
            "ima": [
                {
                    "title": GUIDE_TITLE,
                    "text": GUIDE_TEXT,
                    "ima_kind": "note",
                    "source": "ima",
                    "external_id": "guide",
                }
            ],
        },
        complete=complete,
    )
    assert GUIDE_TITLE not in captured["prompt"]
    assert GUIDE_TITLE not in result.text
    assert "[99]" not in result.text
    assert "[3]" not in result.text
    assert result.text.index("[1]") < result.text.index("[2]")
    assert "丙" in result.text.split("[1]", 1)[0]
    assert [item["n"] for item in result.citations] == [1, 2]
    assert [item["title"] for item in result.citations] == ["丙", "甲"]
    assert all(item["title"] != "乙" for item in result.citations)
    assert all(GUIDE_TITLE not in json.dumps(item, ensure_ascii=False) for item in result.citations)


def test_fetched_page_body_is_longer_than_snippet(mock_server: str) -> None:
    from app.web_search.page import enrich_web_hits, fetch_page_text

    page = fetch_page_text(f"{mock_server}/article")
    assert "示例概念的页面正文" in page
    assert len(page) > len(SNIPPET) + 200
    assert "do-not-keep" not in page
    assert "color:red" not in page
    assert fetch_page_text(f"{mock_server}/missing") == ""

    def run(text: str) -> tuple[str, str]:
        seen: dict[str, str] = {}

        def complete(messages: list[dict[str, str]]) -> tuple[str, int, int]:
            seen["prompt"] = messages[-1]["content"]
            excerpt = messages[-1]["content"].split("【问题】", 1)[0].strip()
            return excerpt, 1, 1

        result = synthesize(
            "示例概念",
            {"web": [{"title": "甲", "url": "https://example.com/a", "text": text, "source": "web"}]},
            complete=complete,
        )
        return result.text, seen["prompt"]

    short_text, short_prompt = run(SNIPPET)
    calls: list[str] = []

    def fetch(url: str) -> str:
        calls.append(url)
        return LONG_PAGE

    hits = [
        {"title": "甲", "url": "https://example.com/a", "text": SNIPPET, "source": "web"},
        {"title": "乙", "url": "https://example.com/b", "text": SNIPPET, "source": "web"},
        {"title": "丙", "url": "", "text": SNIPPET, "source": "web", "media_type": 6},
    ]
    enriched = enrich_web_hits(hits, fetch=fetch, limit=1)
    assert calls == ["https://example.com/a"]
    assert len(enriched[0]["text"]) > len(SNIPPET) + 200
    assert enriched[1]["text"] == SNIPPET
    assert enriched[2]["text"] == SNIPPET
    long_text, long_prompt = run(enriched[0]["text"])
    assert "https://" not in long_prompt
    assert "页面正文" not in short_text
    assert "页面正文" not in short_prompt
    assert len(long_prompt) > len(short_prompt) + 200
    assert len(long_text) > len(short_text) + 200
    assert "页面正文" in long_text

    def boom(url: str) -> str:
        raise RuntimeError("boom")

    kept = enrich_web_hits(hits[:1], fetch=boom, limit=3)
    assert kept[0]["text"] == SNIPPET

    def complete_bad(messages: list[dict[str, str]]) -> tuple[str, int, int]:
        return "示例概念的页面正文提到教材与基本事实。[1][8]", 1, 1

    guarded = synthesize(
        "示例概念",
        {"web": [{"title": "甲", "url": "https://example.com/a", "text": enriched[0]["text"], "source": "web"}]},
        complete=complete_bad,
    )
    assert "[8]" not in guarded.text
    assert "[1]" in guarded.text
    assert len(guarded.citations) == 1
    assert guarded.citations[0]["n"] == 1
    assert "页面正文" in guarded.citations[0]["text"]
    assert len(guarded.citations[0]["text"]) > len(SNIPPET) + 200


def test_serper_failure_notice_names_provider(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
    mock_server: str,
    caplog: pytest.LogCaptureFixture,
) -> None:
    from app.web_search.live import NO_WEB_RESULT, _failure_notice

    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.delenv("SERPAPI_API_KEY", raising=False)
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)
    monkeypatch.setenv("SERPER_API_KEY", SERPER_KEY)
    monkeypatch.setenv("SERPER_BASE_URL", f"{mock_server}/serper")
    STATE.serper_status = 500
    STATE.calls.clear()
    caplog.set_level(logging.INFO)
    found = search_web("示例概念")
    assert found.hits == []
    assert found.notice
    assert found.notice != NO_WEB_RESULT
    assert "Serper" in found.notice
    assert "500" in found.notice
    assert "SerpApi" in found.notice
    assert "Tavily" in found.notice
    assert "未配置后备来源" in found.notice
    assert SERPER_KEY not in found.notice
    assert SERPER_KEY not in caplog.text

    class _Provider:
        def __init__(self, provider_id: str) -> None:
            self.id = provider_id

    assert _failure_notice([{"provider": "serper", "http_status": 200, "count": 0}], [_Provider("serper")]) == NO_WEB_RESULT

    monkeypatch.setenv("SERPAPI_API_KEY", SERPAPI_KEY)
    monkeypatch.setenv("TAVILY_API_KEY", TAVILY_KEY)
    monkeypatch.setenv("SERPAPI_BASE_URL", f"{mock_server}/serpapi")
    monkeypatch.setenv("TAVILY_BASE_URL", f"{mock_server}/tavily")
    STATE.serper_status = 401
    STATE.serpapi_status = 403
    STATE.tavily_status = 500
    STATE.calls.clear()
    failed = search_web("示例概念")
    notice = failed.notice or ""
    assert "Serper" in notice and "401" in notice
    assert "SerpApi" in notice and "403" in notice
    assert "Tavily" in notice and "500" in notice
    assert "未配置" not in notice
    for secret in (SERPER_KEY, SERPAPI_KEY, TAVILY_KEY):
        assert secret not in notice


def test_vector_note_filter_drops_unrelated_guide(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.ask.select import note_is_relevant

    monkeypatch.setenv("ASK_NOTE_FILTER", "vector")
    monkeypatch.delenv("ASK_NOTE_RELEVANCE", raising=False)
    assert note_is_relevant("示例概念", "示例笔记", NOTE_TEXT)
    assert not note_is_relevant("示例概念", GUIDE_TITLE, GUIDE_TEXT)


def test_ambiguous_entity_asks_named_candidates(
    client: TestClient,
    token: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.ask.pipeline import decide
    from app.clarifier.design_tree import build_tree
    from app.decision.deepseek import _PLAN_SYSTEM, _normalize_plan

    assert "ambiguities" in _PLAN_SYSTEM
    person = _normalize_plan(
        {
            "need_clarify": "no",
            "sources": ["web"],
            "queries": ["某甲 近况"],
            "confidence": 0.4,
            "ambiguities": [{"term": "某甲", "options": ["作家某甲", "演员某甲"]}],
        }
    )
    assert person["need_clarify"] == "yes"
    person_tree = build_tree("某甲近况", None, 3, ambiguities=person["ambiguities"])
    person_text = person_tree.current().text
    assert "作家某甲" in person_text
    assert "演员某甲" in person_text
    assert "怎样才算成功" not in person_text

    def fake_post(base, api_key, model, messages, *, temperature):
        del base, model, temperature
        assert api_key == DEEPSEEK_KEY
        assert "ambiguities" in messages[0]["content"]
        user = json.loads(messages[-1]["content"])
        assert "某地" in user["text"]
        content = json.dumps(
            {
                "need_clarify": "no",
                "sources": ["web", "local"],
                "queries": ["某地 附近 餐厅 推荐"],
                "confidence": 0.82,
                "ambiguities": [{"term": "某地", "options": ["某地商场", "某地公园"]}],
            },
            ensure_ascii=False,
        )
        return {"choices": [{"message": {"content": content}}], "usage": {}, "_http_status": 200}

    monkeypatch.setenv("FEATURE_ASK", "1")
    monkeypatch.setenv("DEEPSEEK_API_KEY", DEEPSEEK_KEY)
    monkeypatch.setattr("app.decision.deepseek._post_chat", fake_post)
    plan = decide("某地附近的餐厅推荐", brief=None, kb_name="", clarify="auto")
    assert plan.backend == "deepseek"
    assert plan.need_clarify == "yes"
    assert plan.ambiguities[0]["term"] == "某地"

    opened = client.post(
        "/ask",
        headers=headers(token),
        json={"message": "某地附近的餐厅推荐", "clarify": "auto"},
    )
    assert opened.status_code == 200, opened.text
    events = parse_sse(opened.text)
    question = next(payload for name, payload in events if name == "clarify_question")
    text = question["question"]["text"]
    assert "某地商场" in text
    assert "某地公园" in text
    assert question["question"]["recommendation"] == ""
    assert question["question"]["options"] == ["某地商场", "某地公园"]
    assert "怎样才算成功" not in text
    assert str(question["question"]["id"]).startswith("ambiguity-")

    step = client.post(
        "/ask",
        headers=headers(token),
        json={
            "message": "某地附近的餐厅推荐",
            "session_id": question["session_id"],
            "answers": [{"question_id": question["question"]["id"], "action": "edit", "value": "某地商场"}],
        },
    )
    assert step.status_code == 200, step.text
    follow = parse_sse(step.text)
    nxt = next(payload for name, payload in follow if name == "clarify_question")
    assert "想吃哪一类" in nxt["question"]["text"]
    assert "火锅" in nxt["question"]["options"]
    assert nxt["question"]["recommendation"] == ""
    assert "怎样才算成功" not in nxt["question"]["text"]


def test_ranking_copies_source_order_and_cites_only_supporting_lines() -> None:
    from app.ask.synthesize import NO_LIVE_SCORE

    listing = "示例美食榜：1. 甲餐厅 2. 乙餐厅 3. 丙餐厅"
    related = "某地附近有很多餐厅，适合周末去。"
    scored = "示例美食榜：1. 甲餐厅 2. 乙餐厅 3. 丙餐厅。评分 4.8、4.6、4.5。"
    captured: dict[str, str] = {}

    def complete(messages: list[dict[str, str]]) -> tuple[str, int, int]:
        captured["system"] = messages[0]["content"]
        return "附近美食排名：1. 甲餐厅 2. 乙餐厅 3. 丙餐厅。[1][2]", 1, 1

    result = synthesize(
        "某地附近的餐厅排名",
        {
            "web": [
                {"title": "示例榜", "url": "https://example.com/list", "text": listing, "position": 1, "source": "web"},
                {"title": "游记", "url": "https://example.com/note", "text": related, "position": 2, "source": "web"},
            ]
        },
        complete=complete,
    )
    assert "第N名" in captured["system"]
    assert "TOP N" in captured["system"]
    assert "NO.N" in captured["system"]
    assert "不要把原文顺序当成名次" in captured["system"]
    assert "不要编造" in captured["system"]
    assert "仅仅话题相关" in captured["system"]
    assert "据《" not in result.text
    assert NO_LIVE_SCORE in result.text
    assert "[2]" not in result.text
    assert result.text.index("甲餐厅") < result.text.index("乙餐厅") < result.text.index("丙餐厅")
    assert [item["title"] for item in result.citations] == ["示例榜"]
    assert "https://" not in captured["system"]

    def reversed_list(messages: list[dict[str, str]]) -> tuple[str, int, int]:
        return "第1名：丙餐厅。[1]", 1, 1

    reversed_result = synthesize(
        "附近美食排名",
        {"web": [{"title": "示例榜", "url": "https://example.com/list", "text": listing, "source": "web"}]},
        complete=reversed_list,
    )
    assert "据《示例榜》榜单第1名" not in reversed_result.text
    assert "第1名" not in reversed_result.text
    assert "没找到可引用的来源" in reversed_result.text
    assert reversed_result.citations == []

    def with_scores(messages: list[dict[str, str]]) -> tuple[str, int, int]:
        captured["scored"] = messages[0]["content"]
        return "1. 甲餐厅 2. 乙餐厅 3. 丙餐厅。[1]", 1, 1

    scored_result = synthesize(
        "美食 top",
        {"web": [{"title": "示例榜", "url": "https://example.com/list", "text": scored, "source": "web"}]},
        complete=with_scores,
    )
    assert "第N名" in captured["scored"]
    assert NO_LIVE_SCORE not in scored_result.text
    assert [item["title"] for item in scored_result.citations] == ["示例榜"]

    def plain(messages: list[dict[str, str]]) -> tuple[str, int, int]:
        captured["plain"] = messages[0]["content"]
        return "示例概念是一种思考方式。[1]", 1, 1

    synthesize(
        "示例概念",
        {"ima": [{"title": "示例笔记", "text": NOTE_TEXT, "ima_kind": "note", "source": "ima"}]},
        complete=plain,
    )
    assert "来源未标名次" not in captured["plain"]
    assert "仅仅话题相关" in captured["plain"]


def test_clarified_local_life_routes_web_only(client: TestClient, token: str, monkeypatch: pytest.MonkeyPatch) -> None:
    from app.ask.pipeline import decide

    seen: list[dict] = []

    def fake_post(base, api_key, model, messages, *, temperature):
        del base, model, temperature
        assert api_key == DEEPSEEK_KEY
        user = json.loads(messages[-1]["content"])
        seen.append(user)
        if user.get("brief_status") == "clarified":
            payload = {
                "need_clarify": "yes",
                "sources": ["web", "ima", "local"],
                "queries": ["某地 餐厅 人均", "某地 餐厅 地铁站 步行", "某地 餐厅 口碑"],
                "confidence": 0.7,
                "ambiguities": [],
            }
        else:
            payload = {
                "need_clarify": "no",
                "sources": ["web", "ima", "local"],
                "queries": ["某地 附近 餐厅"],
                "confidence": 0.8,
                "ambiguities": [{"term": "某地", "options": ["某地商场", "某地公园"]}],
            }
        content = json.dumps(payload, ensure_ascii=False)
        return {"choices": [{"message": {"content": content}}], "usage": {}, "_http_status": 200}

    monkeypatch.setenv("FEATURE_ASK", "1")
    monkeypatch.setenv("DEEPSEEK_API_KEY", DEEPSEEK_KEY)
    monkeypatch.setattr("app.decision.deepseek._post_chat", fake_post)
    opened = client.post(
        "/ask",
        headers=headers(token),
        json={"message": "某地附近的餐厅推荐", "clarify": "auto"},
    )
    assert opened.status_code == 200, opened.text
    choices = {
        "ambiguity-0": "某地公园",
        "probe-cuisine": "火锅",
        "probe-basis": "榜单名次",
        "probe-price": "价格亲民",
        "probe-metro": "离地铁站近",
    }
    events = parse_sse(opened.text)
    for _ in range(6):
        question = next((payload for name, payload in events if name == "clarify_question"), None)
        if question is None:
            break
        qid = question["question"]["id"]
        step = client.post(
            "/ask",
            headers=headers(token),
            json={
                "message": "某地附近的餐厅推荐",
                "session_id": question["session_id"],
                "answers": [{"question_id": qid, "action": "edit", "value": choices[qid]}],
            },
        )
        assert step.status_code == 200, step.text
        events = parse_sse(step.text)
    searching = next(payload for name, payload in events if name == "searching")
    assert searching["sources"] == ["web"]
    assert "ima" not in searching["sources"]
    assert "local" not in searching["sources"]
    assert searching["queries"] == ["某地 餐厅 人均", "某地 餐厅 地铁站 步行", "某地 餐厅 口碑"]
    assert not any("；" in item or "人均100-200" in item for item in searching["queries"])
    assert seen[-1]["brief_status"] == "clarified"
    assert "某地公园" in seen[-1]["text"]
    assert "火锅" in seen[-1]["constraints"]
    assert "榜单名次" in seen[-1]["constraints"]

    plain = decide("示例概念的定义是什么", brief=None, kb_name="", clarify="skip")
    assert plain.sources == ["local", "ima", "web"]


def test_web_choice_still_searches_local_but_never_adds_ima(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    from app.ask.pipeline import decide, life_web_only

    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    food = decide("某地附近美食", brief=None, kb_name="", clarify="skip")
    assert food.sources == ["web"]
    assert life_web_only("某地附近的餐厅")
    assert not life_web_only("示例概念的笔记")

    def fake_post(base, api_key, model, messages, *, temperature):
        del base, api_key, model, messages, temperature
        content = json.dumps(
            {"need_clarify": "no", "sources": ["web"], "queries": ["示例概念 定义"], "confidence": 0.9, "ambiguities": []},
            ensure_ascii=False,
        )
        return {"choices": [{"message": {"content": content}}], "usage": {}, "_http_status": 200}

    monkeypatch.setenv("DEEPSEEK_API_KEY", DEEPSEEK_KEY)
    monkeypatch.setattr("app.decision.deepseek._post_chat", fake_post)
    plan = decide("示例概念的定义是什么", brief=None, kb_name="", clarify="skip")
    # A web-only decider choice no longer hides the local library: unnamed
    # questions always search it first (M8), while ima stays opt-in via kb_name.
    assert plan.sources == ["local", "web"]
    assert plan.backend == "deepseek"


def test_rank_markers_budget_and_uncited_sentences() -> None:
    from app.ask.synthesize import NO_LIVE_SCORE, Evidence, citation_excerpt_limit, public_citation

    calls = {"n": 0}

    def complete(messages: list[dict[str, str]]) -> tuple[str, int, int]:
        calls["n"] += 1
        if calls["n"] == 1:
            return "1. 甲餐厅 2. 乙餐厅 3. 丙餐厅。[1] 丁餐厅最好吃。[1]", 3, 4
        assert "重写成连贯" in messages[0]["content"]
        return "甲餐厅人均80。[1] 丙餐厅。[1]", 5, 6

    listed = "甲餐厅人均80 · 乙餐厅人均150 · 丙餐厅"
    result = synthesize(
        "某地附近餐厅排名 人均100以内",
        {"web": [{"title": "附近美食榜", "url": "https://example.com/top", "text": listed, "source": "web"}]},
        complete=complete,
    )
    assert "据《" not in result.text
    assert "收录了" not in result.text
    assert "第1名" not in result.text
    assert "丁餐厅" not in result.text
    assert "按人均100元以内筛选" in result.text
    assert result.text.count("按人均100元以内筛选") == 1
    assert "已排除" not in result.text
    assert "乙餐厅" not in result.text
    assert NO_LIVE_SCORE in result.text
    assert "甲餐厅人均80" in result.text
    assert calls["n"] == 2
    assert result.prompt_tokens == 8
    assert result.completion_tokens == 10

    def ranked(messages: list[dict[str, str]]) -> tuple[str, int, int]:
        return "第1名是甲餐厅。[1] 第2名是乙餐厅。[1]", 1, 1

    ranked_result = synthesize(
        "附近美食排名",
        {"web": [{"title": "示例排行榜", "url": "https://example.com/r", "text": "TOP1 甲餐厅 TOP2 乙餐厅", "source": "web"}]},
        complete=ranked,
    )
    assert "据《示例排行榜》榜单第1名" not in ranked_result.text
    assert "第1名是甲餐厅" in ranked_result.text
    assert "第2名是乙餐厅" in ranked_result.text
    assert ranked_result.text.index("第1名") < ranked_result.text.index("第2名")
    assert ranked_result.citations

    passage = "可核对的事实。" + ("甲" * 1200)
    cited = public_citation(
        Evidence(
            n=1,
            text=passage,
            title="长摘录",
            url="https://example.com/long",
            filename="",
            source="web",
            source_kind="web_search",
            platform="web",
            freshness_state="unknown",
            score=1,
        )
    )
    assert citation_excerpt_limit() > 800
    assert len(cited["text"]) == len(passage)
    assert cited["text"].endswith("甲")


def test_vague_food_query_clarifies_even_when_router_says_no(
    client: TestClient, token: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.clarifier.design_tree import clean_ambiguities, specified_food_query, vague_food_query

    specific = "某地餐厅里口碑打分最高的价格亲民、距离地铁站最近的火锅"
    assert vague_food_query("某地附近的餐厅推荐")
    assert not vague_food_query(specific)
    assert specified_food_query(specific)
    assert clean_ambiguities(
        [
            {"term": "价格亲民", "options": ["便宜", "贵"]},
            {"term": "距离地铁站最近", "options": ["步行5分钟", "不限"]},
            {"term": "某地", "options": ["某地商场", "某地公园"]},
        ]
    ) == [{"term": "某地", "options": ["某地商场", "某地公园"]}]

    def fake_post(base, api_key, model, messages, *, temperature):
        del base, api_key, model, temperature
        user = json.loads(messages[-1]["content"])
        if "火锅" in str(user.get("text") or ""):
            payload = {
                "need_clarify": "yes",
                "sources": ["web", "ima", "local"],
                "queries": ["某地 餐厅 人均", "某地 餐厅 地铁站 步行", "某地 餐厅 口碑"],
                "confidence": 0.4,
                "ambiguities": [
                    {"term": "价格亲民", "options": ["便宜", "贵"]},
                    {"term": "距离地铁站最近", "options": ["步行5分钟", "不限"]},
                ],
            }
        else:
            payload = {
                "need_clarify": "no",
                "sources": ["web", "ima", "local"],
                "queries": ["某地 附近 餐厅"],
                "confidence": 0.9,
                "ambiguities": [],
            }
        content = json.dumps(payload, ensure_ascii=False)
        return {"choices": [{"message": {"content": content}}], "usage": {}, "_http_status": 200}

    monkeypatch.setenv("FEATURE_ASK", "1")
    monkeypatch.setenv("DEEPSEEK_API_KEY", DEEPSEEK_KEY)
    monkeypatch.setattr("app.decision.deepseek._post_chat", fake_post)
    opened = client.post(
        "/ask",
        headers=headers(token),
        json={"message": "某地附近的餐厅推荐", "clarify": "auto"},
    )
    assert opened.status_code == 200, opened.text
    events = parse_sse(opened.text)
    question = next(payload for name, payload in events if name == "clarify_question")
    assert question["question"]["id"] == "probe-place"
    assert question["question"]["recommendation"] == ""
    assert "请写清楚" in question["question"]["text"]
    assert "options" not in question["question"]
    refused = client.post(
        "/ask",
        headers=headers(token),
        json={
            "message": "某地附近的餐厅推荐",
            "session_id": question["session_id"],
            "answers": [{"question_id": "probe-place", "action": "accept"}],
        },
    )
    assert any(name == "error" for name, _payload in parse_sse(refused.text))

    specific_response = client.post(
        "/ask",
        headers=headers(token),
        json={"message": specific, "clarify": "auto"},
    )
    specific_events = parse_sse(specific_response.text)
    assert not any(name == "clarify_question" for name, _payload in specific_events)
    searching = next(payload for name, payload in specific_events if name == "searching")
    assert searching["sources"] == ["web"]
    assert searching["queries"] == ["某地 餐厅 人均", "某地 餐厅 地铁站 步行", "某地 餐厅 口碑"]


def test_dianping_scores_and_metro_distance_stay_inside_sources() -> None:
    from app.ask.synthesize import DIANPING_NOTICE, DISTANCE_GAP

    def complete(messages: list[dict[str, str]]) -> tuple[str, int, int]:
        system = messages[0]["content"]
        assert "评分以大众点评 App 为准" in system
        assert "不要调用地图" in system
        return "甲餐厅 4.9 分。[1]\n距某地铁站步行8分钟。[1]", 1, 1

    cited = synthesize(
        "某地餐厅打分最高而且离地铁站近",
        {
            "web": [
                {
                    "title": "附近游记",
                    "url": "https://example.com/a",
                    "text": "甲餐厅在某地，距某地铁站步行8分钟。",
                    "source": "web",
                }
            ]
        },
        complete=complete,
    )
    assert "4.9" not in cited.text
    assert "步行3" not in cited.text
    assert "距某地铁站步行8分钟" in cited.text
    assert "[1]" in cited.text
    assert cited.text.count(DIANPING_NOTICE) == 1
    assert DISTANCE_GAP not in cited.text

    def bare(messages: list[dict[str, str]]) -> tuple[str, int, int]:
        return "乙餐厅评分 4.8 分。[1]", 1, 1

    missing = synthesize(
        "打分最高而且离地铁近",
        {"web": [{"title": "笔记", "url": "https://example.com/b", "text": "乙餐厅在商场里。", "source": "web"}]},
        complete=bare,
    )
    assert "4.8" not in missing.text
    assert DIANPING_NOTICE in missing.text
    assert DISTANCE_GAP in missing.text
    assert "没找到可引用的来源" in missing.text


def test_bare_decimal_score_suppresses_the_missing_score_note() -> None:
    from app.ask.synthesize import NO_LIVE_SCORE

    def complete(messages: list[dict[str, str]]) -> tuple[str, int, int]:
        return "甲餐厅 4.9。[1]", 1, 1

    result = synthesize(
        "附近美食排名",
        {"web": [{"title": "榜", "url": "https://example.com/s", "text": "甲餐厅 4.9", "source": "web"}]},
        complete=complete,
    )
    assert "4.9" in result.text
    assert NO_LIVE_SCORE not in result.text
    assert result.citations


def test_quoted_period_stays_attached_to_its_citation() -> None:
    def complete(messages: list[dict[str, str]]) -> tuple[str, int, int]:
        return "推荐「甲餐厅。口味稳定。」[1]\n步行到店即可。[1]", 1, 1

    result = synthesize(
        "火锅",
        {"web": [{"title": "文", "url": "https://example.com/q", "text": "甲餐厅。口味稳定。步行到店即可。", "source": "web"}]},
        complete=complete,
    )
    assert "「甲餐厅。口味稳定。」" in result.text
    assert "[1]" in result.text
    assert result.citations


def test_rank_claim_without_verbatim_marker_is_dropped() -> None:
    def complete(messages: list[dict[str, str]]) -> tuple[str, int, int]:
        return "第1名是甲餐厅。[1]", 1, 1

    excerpt = "甲餐厅人均¥368，2018年开业。"
    result = synthesize(
        "附近美食排名",
        {"web": [{"title": "旧文", "url": "https://example.com/old", "text": excerpt, "source": "web"}]},
        complete=complete,
    )
    assert "据《" not in result.text
    assert "榜单第" not in result.text
    assert "第1名" not in result.text
    assert "没找到可引用的来源" in result.text
    assert "年资料" not in result.text
    assert "已排除" not in result.text
    assert "2018元" not in result.text


def test_affordable_price_states_the_assumption_without_pasting_the_page() -> None:
    def complete(messages: list[dict[str, str]]) -> tuple[str, int, int]:
        return "甲餐厅人均80。\n[1]", 1, 1

    result = synthesize(
        "某地 价格亲民 火锅",
        {
            "web": [
                {
                    "title": "菜单",
                    "url": "https://example.com/m",
                    "text": "甲餐厅人均80。环境、交通、优惠等更多信息>> 4.6 乙餐厅¥368。丙餐厅¥400。",
                    "source": "web",
                }
            ]
        },
        complete=complete,
    )
    assert result.text.count("把“价格亲民”按人均150元以内理解") == 1
    assert result.text.count("按人均150元以内筛选") == 1
    assert "甲餐厅人均80" in result.text
    assert "已排除" not in result.text
    assert "更多信息" not in result.text
    assert "4.6" not in result.text
    assert "¥368" not in result.text
    assert "¥400" not in result.text


def test_recent_sources_sort_ahead_of_undated_and_old() -> None:
    from app.ask.synthesize import prefer_recent

    ordered = prefer_recent(
        [
            {"title": "旧", "text": "2018年的店", "url": "https://example.com/old"},
            {"title": "新", "text": "2024年的店", "url": "https://example.com/new"},
            {"title": "未知", "text": "没有年份", "url": "https://example.com/u"},
        ]
    )
    assert [item["title"] for item in ordered] == ["新", "未知", "旧"]
    assert [item["position"] for item in ordered] == [1, 2, 3]


def test_web_search_merges_each_short_query(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.ask.pipeline import _retrieve
    from app.web_search.live import NO_WEB_RESULT, WebSearchResult

    calls: list[str] = []

    def fake_search(query: str, limit: int = 5) -> WebSearchResult:
        assert limit == 20
        calls.append(query)
        if "site:" in query:
            return WebSearchResult(hits=[])
        pages = {
            "某地 餐厅 人均": "https://example.com/a",
            "某地 餐厅 地铁站 步行": "https://example.com/b",
            "某地 餐厅 口碑": "https://example.com/a?utm_source=ad#top",
        }
        notice = NO_WEB_RESULT if query.endswith("口碑") else None
        return WebSearchResult(
            hits=[{"title": query, "url": pages[query], "text": "甲餐厅", "position": 9}],
            notice=notice,
        )

    def fake_enrich(hits, **kwargs):
        del kwargs
        return list(hits)

    monkeypatch.setattr("app.ask.pipeline.search_web", fake_search)
    monkeypatch.setattr("app.ask.pipeline.enrich_web_hits", fake_enrich)
    groups, notices = _retrieve(
        ["某地 餐厅 人均", "某地 餐厅 地铁站 步行", "某地 餐厅 口碑"],
        ["web"],
        kb_name="",
        brief=None,
        mode="personal",
        verify="auto",
        registry=None,
    )
    expected_calls: list[str] = []
    for query in ["某地 餐厅 人均", "某地 餐厅 地铁站 步行", "某地 餐厅 口碑"]:
        expected_calls.extend([query, f"{query} site:dianping.com", f"{query} site:xiaohongshu.com"])
    assert calls == expected_calls
    assert [item["url"] for item in groups["web"]] == ["https://example.com/a", "https://example.com/b"]
    assert NO_WEB_RESULT not in notices


def test_uncited_model_text_is_replaced() -> None:
    def complete(messages: list[dict[str, str]]) -> tuple[str, int, int]:
        return "这家店很好但是没有编号。", 1, 1

    result = synthesize(
        "火锅推荐",
        {"web": [{"title": "文", "url": "https://example.com/z", "text": "甲餐厅在某地。", "source": "web"}]},
        complete=complete,
    )
    assert result.text == "没找到可引用的来源"
    assert result.citations == []
    assert "这家店" not in result.text


def test_length_finish_raises_max_tokens(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("DEEPSEEK_API_KEY", DEEPSEEK_KEY)
    monkeypatch.delenv("ASK_MAX_TOKENS", raising=False)
    calls: list[int | None] = []

    def fake_post(base, api_key, model, messages, *, temperature, max_tokens=None):
        del base, api_key, model, messages, temperature
        calls.append(max_tokens)
        if len(calls) == 1:
            return {
                "choices": [{"message": {"content": "截断"}, "finish_reason": "length"}],
                "usage": {"prompt_tokens": 1, "completion_tokens": 2},
                "_http_status": 200,
            }
        return {
            "choices": [{"message": {"content": "完整回答"}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 3, "completion_tokens": 4},
            "_http_status": 200,
        }

    monkeypatch.setattr("app.decision.deepseek._post_chat", fake_post)
    from app.decision.deepseek import complete_deepseek

    text, prompt, completion = complete_deepseek([{"role": "user", "content": "hi"}])
    assert text == "完整回答"
    assert calls == [2048, 4096]
    assert prompt == 3
    assert completion == 4


def test_fixed_notes_are_emitted_once() -> None:
    def complete(messages: list[dict[str, str]]) -> tuple[str, int, int]:
        return (
            "按人均150元以内筛选。甲餐厅人均80。[1]\n"
            "按人均150元以内筛选。\n"
            "评分以大众点评 App 为准。\n"
            "评分以大众点评 App 为准。"
        ), 1, 1

    result = synthesize(
        "价格亲民 打分 火锅",
        {"web": [{"title": "菜单", "url": "https://example.com/m", "text": "甲餐厅人均80。", "source": "web"}]},
        complete=complete,
    )
    assert result.text.count("按人均150元以内筛选") == 1
    assert result.text.count("评分以大众点评 App 为准") == 1
    assert result.text.count("把“价格亲民”按人均150元以内理解") == 1
    assert "已排除" not in result.text


def test_staleness_uses_one_date_and_drops_notes_that_are_the_only_citation() -> None:
    from app.ask.synthesize import source_year

    assert source_year("2015年指南", "正文里提到2013年") == 2015
    assert source_year("旧文", "2013年开业，2018年仍在。") == 2018

    def complete(messages: list[dict[str, str]]) -> tuple[str, int, int]:
        return (
            "甲餐厅是火锅。[1]\n"
            "《2018年旧文》为2013年资料，可能已过时。[1]\n"
            "《2018年旧文》为2018年资料，可能已过时。[1]\n"
            "《旁支》为2018年资料，可能已过时。[2]"
        ), 1, 1

    result = synthesize(
        "火锅推荐",
        {
            "web": [
                {
                    "title": "2018年旧文",
                    "url": "https://example.com/a",
                    "text": "甲餐厅是火锅。文中也提到2013年。",
                    "source": "web",
                },
                {
                    "title": "旁支",
                    "url": "https://example.com/b",
                    "text": "2018年的另一家店。",
                    "source": "web",
                },
            ]
        },
        complete=complete,
    )
    assert "2013年资料" not in result.text
    assert "2018年资料" in result.text
    assert "旁支" not in result.text
    assert [item["title"] for item in result.citations] == ["2018年旧文"]


def test_uncited_score_is_removed() -> None:
    def complete(messages: list[dict[str, str]]) -> tuple[str, int, int]:
        return "甲餐厅 4.6。\n乙餐厅人均80。[1]", 1, 1

    result = synthesize(
        "附近餐厅",
        {"web": [{"title": "菜单", "url": "https://example.com/m", "text": "乙餐厅人均80。另有评分 4.6。", "source": "web"}]},
        complete=complete,
    )
    assert "4.6" not in result.text
    assert "乙餐厅人均80" in result.text
    assert result.citations


def test_cuisine_filter_drops_other_food_and_adds_site_queries(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.ask.pipeline import _retrieve
    from app.web_search.live import WebSearchResult

    calls: list[tuple[str, int]] = []

    def fake_search(query: str, limit: int = 5) -> WebSearchResult:
        calls.append((query, limit))
        if "site:" in query or query == "示例概念 定义":
            return WebSearchResult(hits=[])
        return WebSearchResult(
            hits=[
                {"title": "火锅店", "url": "https://example.com/hotpot", "text": "这家是火锅"},
                {"title": "咖喱店", "url": "https://example.com/curry", "text": "咖喱饭很好吃"},
            ]
        )

    def fake_enrich(hits, **kwargs):
        del kwargs
        return list(hits)

    monkeypatch.setattr("app.ask.pipeline.search_web", fake_search)
    monkeypatch.setattr("app.ask.pipeline.enrich_web_hits", fake_enrich)
    groups, _notices = _retrieve(
        ["某地 餐厅 人均", "某地 餐厅 地铁站 步行"],
        ["web"],
        kb_name="",
        brief=None,
        mode="personal",
        verify="auto",
        registry=None,
        context="某地 火锅",
    )
    assert [item["title"] for item in groups["web"]] == ["火锅店"]
    assert all(limit == 20 for _query, limit in calls)
    assert "某地 餐厅 人均 site:dianping.com" in [query for query, _limit in calls]
    assert "某地 餐厅 地铁站 步行 site:xiaohongshu.com" in [query for query, _limit in calls]

    calls.clear()
    _retrieve(
        ["示例概念 定义"],
        ["web"],
        kb_name="",
        brief=None,
        mode="personal",
        verify="auto",
        registry=None,
        context="示例概念 定义",
    )
    assert calls == [("示例概念 定义", 20)]


def test_serper_requests_twenty_results(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("SERPER_API_KEY", SERPER_KEY)
    monkeypatch.delenv("SERPAPI_API_KEY", raising=False)
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)
    captured: dict = {}

    def fake_post(url, body, headers):
        del url, headers
        captured["body"] = body
        return 200, {"organic": []}

    monkeypatch.setattr("app.web_search.live._post_json", fake_post)
    from app.web_search.live import SerperProvider

    SerperProvider().fetch("某地 餐厅", limit=20)
    assert captured["body"] == {"q": "某地 餐厅", "num": 20}

    seen: dict = {}

    def fake_fetch(self, query, *, limit):
        del self, query
        seen["limit"] = limit
        return [], 200

    monkeypatch.setattr("app.web_search.live.SerperProvider.fetch", fake_fetch)
    found = search_web("餐厅", limit=20)
    assert seen["limit"] == 20
    assert found.hits == []


def test_one_year_orders_old_sources(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.ask.synthesize import prefer_recent
    from app.web_search.page import fetch_limit

    monkeypatch.delenv("ASK_WEB_FETCH_N", raising=False)
    ordered = prefer_recent(
        [
            {"title": "早", "text": "2015年的记录"},
            {"title": "混", "text": "文中提到2013年，2018年仍在营业"},
        ]
    )
    assert [item["title"] for item in ordered] == ["混", "早"]
    assert fetch_limit() == 12
