"""M3 local library: import, index, retrieve, and cite.

Offline baseline (hashing embedder + FTS5 + lexical overlap, no API key):
the five-question fixture below has top-1 citation accuracy of 5/5 (1.0).
That number is only for this fixture. It is not a production BGE recall claim.
"""

from __future__ import annotations

import base64
import io
import json
import sqlite3
import zipfile

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app.db.models import Chunk, ClarifySession, ContentItem
from app.db.session import get_engine
from app.db.vec import try_enable_vec
from app.decision.client import DecisionUnavailable, build_decision_client, register_decision_backend
from app.index.embedder import hash_embed
from app.main import app
from app.retrieval.answer import NOT_FOUND
from app.web_search.normalize import normalize_web_result
from app.web_search.provider import SearchUnavailable, build_search_chain, provider_order

PDF_TEXT = "Qingjiao salt is 3 grams"


def _pdf_bytes() -> bytes:
    return b"""%PDF-1.4
1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj
2 0 obj<</Type/Pages/Count 1/Kids[3 0 R]>>endobj
3 0 obj<</Type/Page/Parent 2 0 R/MediaBox[0 0 300 144]/Contents 4 0 R/Resources<</Font<</F1 5 0 R>>>>>>endobj
4 0 obj<</Length 68>>stream
BT /F1 18 Tf 20 80 Td (Qingjiao salt is 3 grams) Tj ET
endstream
endobj
5 0 obj<</Type/Font/Subtype/Type1/BaseFont/Helvetica>>endobj
xref
0 6
0000000000 65535 f 
0000000009 00000 n 
0000000052 00000 n 
0000000101 00000 n 
0000000226 00000 n 
0000000344 00000 n 
trailer<</Size 6/Root 1 0 R>>
startxref
421
%%EOF
"""


def _docx_bytes(text: str) -> bytes:
    document = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        f"<w:body><w:p><w:r><w:t>{text}</w:t></w:r></w:p></w:body></w:document>"
    )
    types = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/word/document.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
        "</Types>"
    )
    rels = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" '
        'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" '
        'Target="word/document.xml"/>'
        "</Relationships>"
    )
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("[Content_Types].xml", types)
        archive.writestr("_rels/.rels", rels)
        archive.writestr("word/document.xml", document.encode("utf-8"))
    return buffer.getvalue()


@pytest.fixture()
def token(monkeypatch: pytest.MonkeyPatch) -> str:
    monkeypatch.setenv("SIDECAR_TOKEN", "test-token")
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("EMBED_API_KEY", raising=False)
    monkeypatch.delenv("EMBED_BASE_URL", raising=False)
    monkeypatch.delenv("LLM_BASE_URL", raising=False)
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
    return "".join(payload["text"] for name, payload in events if name == "delta")


def import_bytes(client: TestClient, token: str, filename: str, data: bytes) -> dict:
    response = client.post(
        "/library/import",
        headers=headers(token),
        json={"filename": filename, "content_base64": base64.b64encode(data).decode("ascii")},
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_hash_embedder_is_deterministic() -> None:
    first = hash_embed("青椒腌制两小时")
    second = hash_embed("青椒腌制两小时")
    other = hash_embed("概率论样本空间")
    assert first == second
    assert len(first) == 1024
    assert abs(sum(value * value for value in first) - 1.0) < 1e-6
    assert first != other


def test_import_indexes_md_txt_pdf_docx_and_lists_status(client: TestClient, token: str) -> None:
    md = import_bytes(client, token, "qingjiao-notes.md", "每斤青椒放盐三克。腌制时间是两小时。".encode())
    txt = import_bytes(client, token, "boat.txt", "西湖游船下午四点半停运，最后一班是16:30。".encode())
    pdf = import_bytes(client, token, "salt.pdf", _pdf_bytes())
    docx = import_bytes(client, token, "ginkgo.docx", _docx_bytes("银杏叶子秋天是金黄色。"))
    assert md["index_state"] == "indexed"
    assert md["chunks"] >= 1
    assert txt["source_kind"] == "local_file"
    assert pdf["index_state"] == "indexed"
    assert docx["index_state"] == "indexed"

    listed = client.get("/library/items", headers=headers(token))
    assert listed.status_code == 200
    body = listed.json()
    assert body["stub"] is False
    assert body["total"] == 4
    by_name = {item["filename"]: item for item in body["items"]}
    assert by_name["qingjiao-notes.md"]["index_state"] == "indexed"
    assert by_name["qingjiao-notes.md"]["chunk_count"] >= 1
    assert by_name["salt.pdf"]["chunk_count"] >= 1

    again = import_bytes(client, token, "qingjiao-notes.md", "每斤青椒放盐三克。腌制时间是两小时。".encode())
    assert again["unchanged"] is True
    assert again["id"] == md["id"]
    assert client.get("/library/items", headers=headers(token)).json()["total"] == 4

    rejected = client.post(
        "/library/import",
        headers=headers(token),
        json={"filename": "notes.exe", "text": "nope"},
    )
    assert rejected.status_code == 415

    conn = sqlite3.connect(get_engine().url.database or "")
    try:
        assert try_enable_vec(conn) is True
        vec_count = conn.execute("SELECT COUNT(*) FROM vec_chunks").fetchone()[0]
        fts_count = conn.execute("SELECT COUNT(*) FROM fts_chunks").fetchone()[0]
    finally:
        conn.close()
    assert vec_count >= 4
    assert fts_count >= 4


def test_fixture_top1_hit_rate_is_one(client: TestClient, token: str) -> None:
    """Five known questions. Top citation must be the source file. Baseline: 1.0."""
    docs = {
        "qingjiao-notes.md": "每斤青椒放盐三克。腌制时间是两小时，中途翻一次。".encode(),
        "boat.txt": "西湖游船下午停运，最后一班时间是16:30。".encode(),
        "ginkgo.docx": _docx_bytes("银杏叶子秋天是金黄色，掉落后铺满石阶。"),
        "salt.pdf": _pdf_bytes(),
    }
    for name, data in docs.items():
        import_bytes(client, token, name, data)

    questions = [
        ("青椒腌制要多久", "qingjiao-notes.md", "两小时"),
        ("每斤青椒放多少盐", "qingjiao-notes.md", "三克"),
        ("西湖游船什么时候停", "boat.txt", "16:30"),
        ("银杏叶子秋天是什么颜色", "ginkgo.docx", "金黄"),
        ("How much salt does the Qingjiao pdf mention", "salt.pdf", "grams"),
    ]
    hits = 0
    for query, filename, snippet in questions:
        found = client.post(
            "/search",
            headers=headers(token),
            json={"query": query, "mode": "personal", "top_k": 3},
        )
        assert found.status_code == 200, found.text
        rows = found.json()["hits"]
        assert rows, query
        assert rows[0]["filename"] == filename, query
        assert snippet in rows[0]["text"]
        hits += 1
    assert hits == 5


def test_chat_cites_then_answers_and_says_when_missing(client: TestClient, token: str) -> None:
    import_bytes(client, token, "qingjiao-notes.md", "每斤青椒放盐三克。腌制时间是两小时。".encode())
    chat = client.post(
        "/chat",
        headers=headers(token),
        json={"message": "青椒腌制要多久", "mode": "personal", "sources": ["local"]},
    )
    assert chat.status_code == 200
    events = parse_sse(chat.text)
    names = [name for name, _payload in events]
    citation_at = names.index("citation")
    delta_at = names.index("delta")
    done_at = names.index("done")
    assert citation_at < delta_at < done_at
    assert names[done_at - 1] == "delta"
    citations = [payload for name, payload in events if name == "citation"]
    assert citations[0]["filename"] == "qingjiao-notes.md"
    assert citations[0]["source_kind"] == "local_file"
    assert citations[0]["chunk_id"]
    answer = joined_delta(events)
    assert "两小时" in answer
    assert "[1]" in answer
    done = events[-1][1]
    assert done["provider"] == "offline"
    assert done["model"] == "hash-extractive"
    assert done["retrieval"]["sources"] == ["local"]
    assert done["retrieval"]["hits"][0]["chunk_id"] == citations[0]["chunk_id"]
    assert "gap" not in done

    decisions = [payload for name, payload in events if name == "decision"]
    assert decisions
    assert all(item["applied"] is False for item in decisions)
    logs = client.get("/decision/logs", headers=headers(token)).json()
    assert logs["total"] >= 5
    assert all(item["applied"] is False for item in logs["items"])

    missing = client.post(
        "/chat",
        headers=headers(token),
        json={"message": "火星土壤的含水量是多少", "mode": "personal"},
    )
    missing_events = parse_sse(missing.text)
    assert [name for name, _payload in missing_events if name == "citation"] == []
    assert NOT_FOUND in joined_delta(missing_events)
    assert missing_events[-1][1]["gap"] == "not_found"
    assert missing_events[-2][0] == "delta"
    assert missing_events[-1][0] == "done"


def test_personal_mode_skips_web_and_connector(client: TestClient, token: str) -> None:
    with Session(get_engine()) as db:
        db.add(
            ContentItem(
                platform="web",
                source_id="mars",
                content_hash="mars-hash-value",
                url="https://example.invalid/mars",
                title="火星土壤",
                body_text="火星土壤含水量是百分之四，这条只存在于外部网页。",
                source_kind="web",
                index_state="pending",
            )
        )
        db.add(
            ContentItem(
                platform="notion",
                source_id="note-1",
                content_hash="notion-hash-value",
                url="https://example.invalid/notion",
                title="连接器笔记",
                body_text="连接器里写着火星土壤含水量是百分之四。",
                source_kind="connector",
                index_state="pending",
            )
        )
        db.commit()
    rebuilt = client.post("/index/rebuild", headers=headers(token), json={"scope": "all"})
    assert rebuilt.status_code == 200
    assert rebuilt.json()["indexed"] == 2
    assert rebuilt.json()["stub"] is False

    personal = client.post(
        "/search",
        headers=headers(token),
        json={"query": "火星土壤含水量是多少", "mode": "personal", "sources": ["local", "web", "connector"]},
    )
    assert personal.json()["hits"] == []

    web = client.post(
        "/search",
        headers=headers(token),
        json={"query": "火星土壤含水量是多少", "mode": "global", "sources": ["web"]},
    )
    assert web.json()["hits"]
    assert all(hit["source_kind"] == "web" for hit in web.json()["hits"])

    chat = client.post(
        "/chat",
        headers=headers(token),
        json={"message": "去网上搜索火星土壤含水量", "mode": "personal", "sources": ["local"]},
    )
    events = parse_sse(chat.text)
    route = next(payload for name, payload in events if name == "decision" and payload["question_id"] == "route_channels")
    assert route["applied"] is False
    assert "web" in route["result"]
    assert events[-1][1]["retrieval"]["sources"] == ["local"]
    assert NOT_FOUND in joined_delta(events)


def test_clarified_scope_filters_hits(client: TestClient, token: str) -> None:
    import_bytes(client, token, "linear.md", "线性代数里，特征值是矩阵的根。".encode())
    import_bytes(client, token, "probability.md", "概率论里，样本空间是随机试验的全部结果。".encode())
    with Session(get_engine()) as db:
        row = ClarifySession(
            keyword="数学范围",
            state="confirmed",
            tree_json={"nodes": []},
            brief_json={
                "brief": "clarified",
                "status": "clarified",
                "keyword": "数学范围",
                "scope": "只看线性代数",
                "goal": "弄清特征值",
                "constraints": None,
                "dependencies": None,
                "tradeoffs": None,
                "risks": None,
                "next_step": None,
                "parked": [],
                "success_criteria": "弄清特征值",
                "decisions": [],
            },
        )
        db.add(row)
        db.commit()
        db.refresh(row)
        brief_id = row.id

    chat = client.post(
        "/chat",
        headers=headers(token),
        json={
            "message": "特征值还是样本空间",
            "mode": "personal",
            "sources": ["local"],
            "brief_id": brief_id,
        },
    )
    assert chat.status_code == 200
    events = parse_sse(chat.text)
    done = events[-1][1]
    assert done["brief"]["scope"] == "只看线性代数"
    assert done["retrieval"]["brief_status"] == "clarified"
    assert done["retrieval"]["scope_applied"] is True
    assert done["retrieval"]["hits"]
    assert {hit["filename"] for hit in done["retrieval"]["hits"]} == {"linear.md"}
    answer = joined_delta(events)
    assert "只看线性代数" in answer
    assert "特征值" in answer
    assert "样本空间" not in answer


def test_delete_removes_index_rows(client: TestClient, token: str) -> None:
    imported = import_bytes(client, token, "qingjiao-notes.md", "腌制时间是两小时。".encode())
    removed = client.delete(f"/library/items/{imported['id']}", headers=headers(token))
    assert removed.status_code == 200
    assert removed.json()["deleted"] is True
    assert client.get("/library/items", headers=headers(token)).json()["total"] == 0
    conn = sqlite3.connect(get_engine().url.database or "")
    try:
        assert conn.execute("SELECT COUNT(*) FROM chunk").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM fts_chunks").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM chunk_vector").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM content_item").fetchone()[0] == 0
    finally:
        conn.close()
    with Session(get_engine()) as db:
        assert db.exec(select(Chunk)).all() == []


def test_blob_fallback_still_retrieves(client: TestClient, token: str, monkeypatch: pytest.MonkeyPatch) -> None:
    import_bytes(client, token, "qingjiao-notes.md", "腌制时间是两小时。".encode())
    monkeypatch.setattr("app.retrieval.hybrid.try_enable_vec", lambda _conn: False)
    found = client.post(
        "/search",
        headers=headers(token),
        json={"query": "青椒腌制要多久", "mode": "personal"},
    )
    assert found.json()["hits"][0]["filename"] == "qingjiao-notes.md"


def test_llm_endpoint_is_used_when_configured(client: TestClient, token: str, monkeypatch: pytest.MonkeyPatch) -> None:
    import_bytes(client, token, "qingjiao-notes.md", "腌制时间是两小时。".encode())
    monkeypatch.setenv("LLM_BASE_URL", "https://example.invalid/v1")
    monkeypatch.setenv("LLM_API_KEY", "sk-test-key")
    monkeypatch.setenv("LLM_MODEL", "demo-model")
    calls: list[str] = []

    def fake_post(endpoint, messages):
        calls.append(endpoint.join("chat/completions"))
        assert endpoint.api_key == "sk-test-key"
        assert endpoint.model == "demo-model"
        assert messages
        return "根据证据，腌制两小时。[1]", 11, 7

    monkeypatch.setattr("app.llm.gateway._post_chat", fake_post)
    chat = client.post(
        "/chat",
        headers=headers(token),
        json={"message": "青椒腌制要多久", "mode": "personal"},
    )
    events = parse_sse(chat.text)
    assert calls == ["https://example.invalid/v1/chat/completions"]
    assert "腌制两小时" in joined_delta(events)
    assert events[-1][1]["provider"] == "env"
    assert events[-1][1]["model"] == "demo-model"
    assert events[-1][1]["prompt_tokens"] == 11
    blob = json.dumps(events, ensure_ascii=False)
    assert "sk-test-key" not in blob


def test_remote_decision_and_web_chain_stay_unplugged() -> None:
    class Fake:
        def choice(self, *_args, **_kwargs):
            return None

        def score(self, *_args, **_kwargs):
            return None

        def noul(self, *_args, **_kwargs):
            return None

    from app.decision import client as decision_client

    with pytest.raises(DecisionUnavailable):
        build_decision_client("remote")
    register_decision_backend("remote", Fake)
    try:
        assert isinstance(build_decision_client("remote"), Fake)
        assert isinstance(build_decision_client("rule"), type(build_decision_client()))
    finally:
        decision_client._BACKENDS.pop("remote", None)
    assert provider_order() == ["google", "fallback"]
    with pytest.raises(SearchUnavailable):
        build_search_chain().search("青椒")
    normalized = normalize_web_result({"title": "t", "url": "https://example.invalid", "snippet": "s"})
    assert normalized["source_kind"] == "web"
    assert normalized["text"] == "s"
