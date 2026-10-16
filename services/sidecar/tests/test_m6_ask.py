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
LOCAL_TEXT = "第一性原理由亚里士多德提出于1980年，强调基本事实。"
WEB_TEXT = "第一性原理提出于1990年，不是从基本事实出发。"
NOTE_TEXT = "笔记本里提到第一性原理是一种思考方式。"


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
            paragraph = "第一性原理的页面正文提到亚里士多德与基本事实，并展开了后续论证。" * 8
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
                    "queries": ["第一性原理 定义"],
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
                                "kb_id": "kb-fp",
                                "kb_name": "第一性原理",
                                "description": "原理库",
                            }
                        ]
                    },
                }
            )
            return
        if path.endswith("/search_knowledge"):
            if body.get("knowledge_base_id") != "kb-fp":
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
                                    "title": "原理笔记",
                                    "summary": "摘要",
                                },
                                "highlightInfo": {
                                    "doc_title": "原理笔记",
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
    denied = client.post("/ask", headers=headers(token), json={"message": "第一性原理"})
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
        "第一性原理",
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
    quiet = search_web("第一性原理")
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
    found = search_web("第一性原理")
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
                            "note_book_info": {"note_id": "note-1", "title": "原理笔记", "summary": "摘要"},
                            "highlightInfo": {"highlight_content": NOTE_TEXT, "doc_title": "原理笔记"},
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
    found = search(stub, "第一性原理", knowledge_base_id="kb-fp")  # type: ignore[arg-type]
    note = next(hit for hit in found["hits"] if hit["kind"] == "note")
    media = next(hit for hit in found["hits"] if hit["kind"] == "media")
    assert note["id"] == "note-1"
    assert note["title"] == "原理笔记"
    assert NOTE_TEXT in note["snippet"]
    assert media["id"] == "media-1"
    assert media["parent_folder_id"] == "folder-1"
    assert media["media_type"] == 13
    assert media["snippet"] == LOCAL_TEXT
    folders, files = _split_knowledge(
        [{"media_id": "media-1", "title": "原理条目", "parent_folder_id": "folder-1", "media_type": 13}],
        "kb-fp",
    )
    assert folders == []
    assert files[0]["kind"] == "media"
    assert stub.sent[1] == (PATH_SEARCH_KNOWLEDGE, {"query": "第一性原理", "cursor": "", "knowledge_base_id": "kb-fp"})


def test_missing_deepseek_falls_back_to_rules(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    with pytest.raises(DecisionUnavailable):
        build_decision_client("deepseek")
    assert isinstance(build_decision_client("rule"), RuleBasedDecisionClient)
    from app.ask.pipeline import decide

    plan = decide("第一性原理", brief=None, kb_name="第一性原理", clarify="auto")
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
            "filename": "first-principles.md",
            "content_base64": base64.b64encode(LOCAL_TEXT.encode("utf-8")).decode("ascii"),
        },
    )
    assert imported.status_code == 200, imported.text

    opened = client.post(
        "/ask",
        headers=headers(token),
        json={"message": "第一性原理", "kb_name": "第一性原理"},
    )
    assert opened.status_code == 200, opened.text
    first = parse_sse(opened.text)
    clarify = next(payload for name, payload in first if name == "clarify_question")
    assert clarify["question"]["id"] == "goal"
    assert "第一性原理" in clarify["question"]["text"]
    assert not any(name == "citation" for name, _payload in first)
    session_id = clarify["session_id"]
    question_id = "goal"
    events: list[tuple[str, dict]] = []
    for _ in range(3):
        step = client.post(
            "/ask",
            headers=headers(token),
            json={
                "message": "第一性原理",
                "kb_name": "第一性原理",
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
    assert "第一性原理 定义" in searching["queries"]
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
    assert knowledge["body"]["knowledge_base_id"] == "kb-fp"
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
    opened = client.post("/ask", headers=headers(token), json={"message": "第一性原理", "clarify": "auto"})
    assert opened.status_code == 200, opened.text
    events = parse_sse(opened.text)
    assert any(name == "clarify_question" for name, _payload in events)
    assert not any(name == "error" for name, _payload in events)

    clear = client.post(
        "/ask",
        headers=headers(token),
        json={"message": "请在网上把第一性原理的定义说清楚，范围只要这个概念本身？", "clarify": "auto"},
    )
    assert clear.status_code == 200, clear.text
    body = joined_delta(parse_sse(clear.text))
    assert "未配置联网搜索密钥" in body or "DeepSeek" in body or "没有检索到" in body


GUIDE_TITLE = "ima笔记使用指南"
GUIDE_TEXT = "打开 ima 后点击新建笔记即可完成记录。"
SUBSCRIPTION_TITLE = "订阅库量子周报"
SNIPPET = "短摘要。"
LONG_PAGE = "第一性原理的页面正文提到亚里士多德与基本事实，并展开了后续论证。" * 8


def test_title_only_subscription_is_related_not_evidence() -> None:
    captured: dict[str, str] = {}

    def complete(messages: list[dict[str, str]]) -> tuple[str, int, int]:
        captured["prompt"] = messages[-1]["content"]
        return "第一性原理是一种思考方式。[1]\n有内容以订阅库量子周报为题。\n只有标题，没有正文。", 3, 4

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
                "title": "第一性原理入门",
                "text": "",
                "ima_kind": "note",
                "source": "ima",
                "external_id": "title-note",
            },
            {
                "title": "原理笔记",
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
    result = synthesize("第一性原理", groups, complete=complete)
    head, _, tail = result.text.partition("相关条目")
    assert tail
    assert SUBSCRIPTION_TITLE in tail
    assert "第一性原理入门" in tail
    assert SUBSCRIPTION_TITLE not in head
    assert "有内容以" not in result.text
    assert "只有标题" not in result.text
    assert GUIDE_TITLE not in result.text
    assert "[1]" in head
    assert "订阅库量子周报" not in captured["prompt"]
    assert GUIDE_TITLE not in captured["prompt"]
    assert "第一性原理入门" not in captured["prompt"]
    assert NOTE_TEXT[:8] in captured["prompt"]
    assert [item["title"] for item in result.citations] == ["原理笔记"]
    assert result.citations[0]["n"] == 1

    offline = synthesize("第一性原理", groups, missing_key=True)
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
        return "先看丙。[3] 再看甲。[1] 编造。[99]", 2, 2

    result = synthesize(
        "第一性原理",
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
    assert "第一性原理的页面正文" in page
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
            "第一性原理",
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
        return "页面说了这些。[1][8]", 1, 1

    guarded = synthesize(
        "第一性原理",
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
    found = search_web("第一性原理")
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
    failed = search_web("第一性原理")
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
    assert note_is_relevant("第一性原理", "原理笔记", NOTE_TEXT)
    assert not note_is_relevant("第一性原理", GUIDE_TITLE, GUIDE_TEXT)
