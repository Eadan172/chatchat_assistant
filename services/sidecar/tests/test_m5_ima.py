"""M5 ima connector. Every ima HTTP call is served by a local mock."""

from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.connectors.ima_client import reset_client_state
from app.db.models import ConnectorAccount, ContentItem
from app.db.session import get_engine
from app.main import app

GOOD_KEY = "test-ima-key-not-a-secret-value"
CLIENT_ID = "test-ima-client"
NOTE_BODY = "腾飞供应商去年的拿铁合同写明露台座位优先。"
SECRET_MARK = "super-secret-ima-key"


class ImaState:
    def __init__(self) -> None:
        self.note = NOTE_BODY
        self.calls: list[dict] = []
        self.fail_network = False


STATE = ImaState()


class Handler(BaseHTTPRequestHandler):
    def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers.get("content-length") or 0)
        raw = self.rfile.read(length) if length else b"{}"
        try:
            body = json.loads(raw.decode("utf-8"))
        except json.JSONDecodeError:
            body = {}
        STATE.calls.append(
            {
                "path": self.path,
                "client": self.headers.get("ima-openapi-clientid"),
                "key": self.headers.get("ima-openapi-apikey"),
                "cookie": self.headers.get("cookie"),
                "body": body,
            }
        )
        if STATE.fail_network:
            self.send_response(503)
            self.end_headers()
            return
        key = self.headers.get("ima-openapi-apikey") or ""
        if key != GOOD_KEY:
            self._json({"code": 20004, "msg": "apiKey 鉴权失败", "data": {}})
            return
        self._json(self._route(self.path, body))

    def do_GET(self) -> None:  # noqa: N802
        STATE.calls.append({"path": self.path, "method": "GET", "cookie": self.headers.get("cookie")})
        payload = "ima file body: 露台座位优先。".encode("utf-8")
        self.send_response(200)
        self.send_header("content-type", "text/plain; charset=utf-8")
        self.send_header("content-length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, fmt: str, *args) -> None:
        return

    def _route(self, path: str, body: dict) -> dict:
        if path.endswith("/search_knowledge_base"):
            return {
                "code": 0,
                "msg": "成功",
                "data": {
                    "info_list": [{"id": "kb1", "name": "行业研究", "cover_url": ""}],
                    "is_end": True,
                    "next_cursor": "",
                },
            }
        if path.endswith("/get_knowledge_base"):
            return {
                "code": 0,
                "msg": "成功",
                "data": {"infos": {"kb1": {"id": "kb1", "name": "行业研究", "description": "供应商纪要"}}},
            }
        if path.endswith("/get_knowledge_list"):
            if body.get("folder_id") == "folder_week":
                entries = [{"media_id": "media_child", "title": "周会todo.txt", "parent_folder_id": "folder_week"}]
            else:
                entries = [
                    {
                        "folder_id": "folder_week",
                        "name": "周会",
                        "file_number": 1,
                        "folder_number": 0,
                        "parent_folder_id": "",
                    },
                    {"media_id": "media_note", "title": "腾飞供应商纪要", "parent_folder_id": ""},
                ]
            return {"code": 0, "msg": "成功", "data": {"knowledge_list": entries, "is_end": True, "next_cursor": "", "current_path": []}}
        if path.endswith("/search_knowledge"):
            return {
                "code": 0,
                "msg": "成功",
                "data": {
                    "info_list": [
                        {
                            "media_id": "media_note",
                            "title": "腾飞供应商纪要",
                            "parent_folder_id": "",
                            "highlight_content": "拿铁合同",
                        }
                    ],
                    "is_end": True,
                    "next_cursor": "",
                },
            }
        if path.endswith("/get_media_info"):
            if body.get("media_id") == "filetxt":
                host = self.headers.get("host") or "127.0.0.1"
                return {
                    "code": 0,
                    "msg": "成功",
                    "data": {
                        "media_type": 13,
                        "url_info": {"url": f"http://{host}/files/zhouhui.txt", "headers": {}},
                    },
                }
            return {"code": 0, "msg": "成功", "data": {"media_type": 11, "notebook_ext_info": {"notebook_id": "note123"}}}
        if path.endswith("/list_notebook"):
            return {
                "code": 0,
                "msg": "成功",
                "data": {
                    "note_folder_infos": [{"folder_id": "nb1", "name": "工作", "note_number": 1, "folder_type": 0}],
                    "is_end": True,
                    "next_cursor": "",
                },
            }
        if path.endswith("/list_note"):
            return {
                "code": 0,
                "msg": "成功",
                "data": {
                    "note_book_list": [{"note_id": "note123", "title": "腾飞供应商纪要", "summary": "合同摘要"}],
                    "is_end": True,
                },
            }
        if path.endswith("/search_note"):
            return {
                "code": 0,
                "msg": "成功",
                "data": {
                    "search_note_infos": [
                        {
                            "note_book_info": {"note_id": "note123", "title": "腾飞供应商纪要", "summary": "合同摘要"},
                            "highlightInfo": {"doc_title": "<em>拿铁</em>合同"},
                        }
                    ],
                    "is_end": True,
                    "total_hit_num": 1,
                },
            }
        if path.endswith("/get_doc_content"):
            return {"code": 0, "msg": "成功", "data": {"content": STATE.note}}
        return {"code": 110012, "msg": "接口无效", "data": {}}

    def _json(self, payload: dict) -> None:
        raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(200)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)


@pytest.fixture(autouse=True)
def quiet_ima(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("IMA_MIN_INTERVAL_S", "0")
    monkeypatch.setenv("IMA_MAX_PER_MINUTE", "1000")
    monkeypatch.setenv("IMA_CACHE_TTL_S", "0")
    for name in ("IMA_CLIENT_ID", "IMA_API_KEY", "IMA_OPENAPI_CLIENTID", "IMA_OPENAPI_APIKEY", "FEATURE_IMA"):
        monkeypatch.delenv(name, raising=False)
    STATE.note = NOTE_BODY
    STATE.calls.clear()
    STATE.fail_network = False
    reset_client_state()
    yield
    reset_client_state()


@pytest.fixture()
def ima_server() -> str:
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()


@pytest.fixture()
def token(monkeypatch: pytest.MonkeyPatch) -> str:
    monkeypatch.setenv("SIDECAR_TOKEN", "test-token")
    return "test-token"


@pytest.fixture()
def client(token: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, ima_server: str) -> TestClient:
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("FEATURE_IMA", "1")
    monkeypatch.setenv("IMA_BASE_URL", ima_server)
    with TestClient(app) as test_client:
        yield test_client


def headers(token: str) -> dict[str, str]:
    return {"x-sidecar-token": token}


def parse_sse(text: str) -> list[tuple[str, dict]]:
    events: list[tuple[str, dict]] = []
    for block in text.split("\n\n"):
        event = "message"
        data = ""
        for line in block.splitlines():
            if line.startswith("event:"):
                event = line.split(":", 1)[1].strip()
            elif line.startswith("data:"):
                data += line.split(":", 1)[1].strip()
        if data:
            events.append((event, json.loads(data)))
    return events


def test_flag_off_keeps_connectors_501(client: TestClient, token: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("FEATURE_IMA", raising=False)
    assert client.get("/connectors", headers=headers(token)).status_code == 501
    assert client.post("/connectors", headers=headers(token)).status_code == 501
    assert client.get("/connectors/ima/bases", headers=headers(token)).status_code == 501
    assert client.get("/settings/ima", headers=headers(token)).status_code == 501
    assert STATE.calls == []


def test_missing_key_is_a_clear_error(client: TestClient, token: str) -> None:
    response = client.get("/connectors/ima/bases", headers=headers(token))
    assert response.status_code == 400
    assert "IMA_CLIENT_ID" in response.json()["detail"]
    assert "API Key" in response.json()["detail"]
    assert STATE.calls == []


def test_wrong_key_is_a_clear_error(client: TestClient, token: str) -> None:
    saved = client.post(
        "/settings/ima",
        headers=headers(token),
        json={"client_id": CLIENT_ID, "api_key": "wrong-key"},
    )
    assert saved.status_code == 401
    detail = saved.json()["detail"]
    assert "密钥无效" in detail
    assert "agent-interface" in detail
    assert "wrong-key" not in detail
    assert not (Path(os.environ["DATA_DIR"]) / "secrets" / "ima.json").exists()


def test_list_bases_folders_and_search(client: TestClient, token: str) -> None:
    _save_good_key(client, token)
    bases = client.get("/connectors/ima/bases", headers=headers(token))
    assert bases.status_code == 200
    body = bases.json()
    assert body["knowledge_bases"][0]["name"] == "行业研究"
    assert body["knowledge_bases"][0]["description"] == "供应商纪要"
    assert body["notebooks"][0]["name"] == "工作"
    folders = client.get("/connectors/ima/folders", headers=headers(token), params={"knowledge_base_id": "kb1"})
    assert folders.status_code == 200
    listed = folders.json()
    assert listed["folders"][0]["title"] == "周会"
    assert listed["items"][0]["id"] == "media_note"
    child = client.get(
        "/connectors/ima/folders",
        headers=headers(token),
        params={"knowledge_base_id": "kb1", "folder_id": "folder_week"},
    )
    assert child.json()["items"][0]["title"] == "周会todo.txt"
    notes = client.get("/connectors/ima/folders", headers=headers(token), params={"notebook_id": "nb1"})
    assert notes.json()["items"][0]["kind"] == "note"
    found = client.post(
        "/connectors/ima/search",
        headers=headers(token),
        json={"query": "拿铁", "knowledge_base_id": "kb1"},
    )
    assert found.status_code == 200
    hits = found.json()["hits"]
    assert found.json()["count"] == len(hits) >= 1
    assert any(hit["kind"] == "note" and hit["id"] == "note123" for hit in hits)
    assert any(hit["kind"] == "media" for hit in hits)
    sent = STATE.calls[0]
    assert sent["path"].endswith("/openapi/wiki/v1/search_knowledge_base")
    assert sent["client"] == CLIENT_ID
    assert sent["key"] == GOOD_KEY
    assert sent["cookie"] is None


def test_import_note_is_cited_as_ima(client: TestClient, token: str) -> None:
    _save_good_key(client, token)
    imported = client.post(
        "/connectors/ima/import",
        headers=headers(token),
        json={"items": [{"kind": "note", "id": "note123", "title": "腾飞供应商纪要"}]},
    )
    assert imported.status_code == 200
    assert imported.json()["count"] == 1
    listed = client.get("/library/items", headers=headers(token)).json()["items"]
    row = listed[0]
    assert row["platform"] == "ima"
    assert row["source_kind"] == "connector"
    assert row["index_state"] == "indexed"
    with sqlite3.connect(get_engine().url.database or "") as conn:
        stored = conn.execute(
            "SELECT platform, source_id, external_id, source_kind, connector_id FROM content_item"
        ).fetchone()
    assert stored[0] == "ima"
    assert stored[1] == "note:note123"
    assert stored[2] == "note123"
    assert stored[3] == "connector"
    assert stored[4]
    db_bytes = Path(get_engine().url.database or "").read_bytes()
    assert GOOD_KEY.encode() not in db_bytes
    assert SECRET_MARK.encode() not in db_bytes
    search = client.post(
        "/search",
        headers=headers(token),
        json={"query": "露台座位优先", "mode": "personal", "sources": ["local"]},
    )
    hits = search.json()["hits"]
    assert hits
    assert hits[0]["platform"] == "ima"
    assert hits[0]["source_kind"] == "connector"
    chat = client.post(
        "/chat",
        headers=headers(token),
        json={"message": "露台座位怎么安排", "mode": "personal", "sources": ["local"]},
    )
    events = parse_sse(chat.text)
    citations = [payload for name, payload in events if name == "citation"]
    assert citations
    assert citations[0]["platform"] == "ima"
    assert citations[0]["source_kind"] == "connector"
    assert "露台" in citations[0]["text"] or "拿铁" in "".join(
        payload.get("text", "") for name, payload in events if name == "delta"
    )


def test_import_text_file_uses_media_url_without_cookies(client: TestClient, token: str) -> None:
    _save_good_key(client, token)
    imported = client.post(
        "/connectors/ima/import",
        headers=headers(token),
        json={"items": [{"kind": "media", "id": "filetxt", "title": "周会todo.txt", "knowledge_base_id": "kb1"}]},
    )
    assert imported.status_code == 200, imported.text
    with sqlite3.connect(get_engine().url.database or "") as conn:
        body, external_id, platform = conn.execute(
            "SELECT body_text, external_id, platform FROM content_item"
        ).fetchone()
    assert platform == "ima"
    assert external_id == "filetxt"
    assert "露台座位优先" in body
    downloads = [call for call in STATE.calls if call.get("method") == "GET"]
    assert downloads
    assert all(call.get("cookie") is None for call in downloads)


def test_disable_drops_ima_from_new_answers(client: TestClient, token: str) -> None:
    _save_good_key(client, token)
    client.post(
        "/connectors/ima/import",
        headers=headers(token),
        json={"items": [{"kind": "note", "id": "note123", "title": "腾飞供应商纪要"}]},
    )
    accounts = client.get("/connectors", headers=headers(token)).json()["accounts"]
    removed = client.delete(f"/connectors/{accounts[0]['id']}", headers=headers(token))
    assert removed.status_code == 200
    search = client.post(
        "/search",
        headers=headers(token),
        json={"query": "露台座位优先", "mode": "personal", "sources": ["local", "connector"]},
    )
    assert search.json()["hits"] == []
    library = client.get("/library/items", headers=headers(token)).json()
    assert library["total"] == 1


def test_verify_refetches_ima(client: TestClient, token: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FEATURE_VERIFY", "1")
    monkeypatch.setenv("VERIFY_DEBOUNCE_S", "0")
    _save_good_key(client, token)
    imported = client.post(
        "/connectors/ima/import",
        headers=headers(token),
        json={"items": [{"kind": "note", "id": "note123", "title": "腾飞供应商纪要"}]},
    )
    item_id = imported.json()["imported"][0]["id"]
    fresh = client.post("/verify", headers=headers(token), json={"item_ids": [item_id], "mode": "hash_mtime"})
    assert fresh.status_code == 200
    assert fresh.json()["stats"]["items"][0]["state"] == "fresh"
    STATE.note = "合同已经改成室内座位，不再保留露台。"
    stale = client.post("/verify", headers=headers(token), json={"item_ids": [item_id], "mode": "hash_mtime"})
    assert stale.json()["stats"]["items"][0]["state"] == "stale"
    assert stale.json()["stats"]["items"][0]["prompt_update"] is True
    STATE.fail_network = True
    unknown = client.post("/verify", headers=headers(token), json={"item_ids": [item_id], "mode": "hash_mtime"})
    assert unknown.json()["stats"]["items"][0]["state"] == "unknown"
    with sqlite3.connect(get_engine().url.database or "") as conn:
        body = conn.execute("SELECT body_text FROM content_item WHERE id = ?", (item_id,)).fetchone()[0]
    assert "露台座位优先" in body


def test_secrets_stay_out_of_sqlite_and_logs(client: TestClient, token: str, caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level("INFO")
    saved = client.post(
        "/settings/ima",
        headers=headers(token),
        json={"client_id": CLIENT_ID, "api_key": GOOD_KEY},
    )
    assert saved.status_code == 200
    assert GOOD_KEY not in saved.text
    created = client.post(
        "/connectors",
        headers=headers(token),
        json={"type": "ima", "display_name": "我的 ima", "config": {"api_key": SECRET_MARK}},
    )
    assert created.status_code == 400
    created = client.post("/connectors", headers=headers(token), json={"type": "ima", "display_name": "我的 ima"})
    assert created.status_code == 200
    assert SECRET_MARK not in created.text
    assert GOOD_KEY not in created.text
    blob = Path(get_engine().url.database or "").read_bytes()
    assert GOOD_KEY.encode() not in blob
    assert SECRET_MARK.encode() not in blob
    text = caplog.text
    assert GOOD_KEY not in text
    assert CLIENT_ID not in text


def test_cache_and_rate_limit(client: TestClient, token: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("IMA_CACHE_TTL_S", "120")
    reset_client_state()
    _save_good_key(client, token)
    before = len(STATE.calls)
    assert client.get("/connectors/ima/bases", headers=headers(token)).status_code == 200
    first = len(STATE.calls) - before
    assert client.get("/connectors/ima/bases", headers=headers(token)).status_code == 200
    second = len(STATE.calls) - before - first
    assert first > 0
    assert second == 0
    monkeypatch.setenv("IMA_CACHE_TTL_S", "0")
    monkeypatch.setenv("IMA_MAX_PER_MINUTE", "1")
    reset_client_state()
    STATE.calls.clear()
    assert client.post(
        "/connectors/ima/search",
        headers=headers(token),
        json={"query": "拿铁"},
    ).status_code == 200
    limited = client.post("/connectors/ima/search", headers=headers(token), json={"query": "拿铁"})
    assert limited.status_code == 429
    assert "频繁" in limited.json()["detail"]


def test_smoke_script_prints_counts_only(ima_server: str, tmp_path: Path) -> None:
    env = os.environ.copy()
    env.update(
        {
            "IMA_CLIENT_ID": CLIENT_ID,
            "IMA_API_KEY": GOOD_KEY,
            "IMA_BASE_URL": ima_server,
            "IMA_MIN_INTERVAL_S": "0",
            "IMA_CACHE_TTL_S": "0",
            "IMA_MAX_PER_MINUTE": "1000",
            "IMA_SMOKE_QUERY": "拿铁",
            "DATA_DIR": str(tmp_path),
        }
    )
    script = Path(__file__).resolve().parents[1] / "scripts" / "ima_smoke.py"
    result = subprocess.run([sys.executable, str(script)], env=env, capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stderr
    assert "list_kb http=200 code=0 count=1" in result.stdout
    assert "search http=200 code=0 count=1" in result.stdout
    assert "腾飞" not in result.stdout
    assert "拿铁" not in result.stdout
    assert GOOD_KEY not in result.stdout
    assert CLIENT_ID not in result.stdout
    missing = os.environ.copy()
    for name in ("IMA_CLIENT_ID", "IMA_API_KEY", "IMA_OPENAPI_CLIENTID", "IMA_OPENAPI_APIKEY"):
        missing.pop(name, None)
    missing["DATA_DIR"] = str(tmp_path)
    bare = subprocess.run([sys.executable, str(script)], env=missing, capture_output=True, text=True, check=False)
    assert bare.returncode == 2
    assert GOOD_KEY not in bare.stderr
    assert GOOD_KEY not in bare.stdout


def test_refetch_updates_imported_note(client: TestClient, token: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FEATURE_VERIFY", "1")
    monkeypatch.setenv("VERIFY_DEBOUNCE_S", "0")
    _save_good_key(client, token)
    imported = client.post(
        "/connectors/ima/import",
        headers=headers(token),
        json={"items": [{"kind": "note", "id": "note123", "title": "腾飞供应商纪要"}]},
    )
    item_id = imported.json()["imported"][0]["id"]
    STATE.note = "新的合同改到室内。"
    refreshed = client.post("/verify", headers=headers(token), json={"item_ids": [item_id], "mode": "refetch"})
    assert refreshed.json()["stats"]["items"][0]["state"] == "fresh"
    from sqlmodel import Session

    with Session(get_engine()) as db:
        item = db.get(ContentItem, item_id)
        assert item is not None
        assert "室内" in (item.body_text or "")
        assert item.platform == "ima"


def test_sync_records_error_without_importing_everything(client: TestClient, token: str) -> None:
    _save_good_key(client, token)
    created = client.post("/connectors", headers=headers(token), json={"type": "ima"}).json()
    synced = client.post(f"/connectors/{created['id']}/sync", headers=headers(token))
    assert synced.status_code == 200
    assert synced.json()["kb_count"] == 1
    assert synced.json()["imported"] == 0
    status = client.get(f"/connectors/{created['id']}/status", headers=headers(token))
    assert status.json()["last_error"] is None
    assert status.json()["configured"] is True


def _save_good_key(client: TestClient, token: str) -> None:
    saved = client.post(
        "/settings/ima",
        headers=headers(token),
        json={"client_id": CLIENT_ID, "api_key": GOOD_KEY},
    )
    assert saved.status_code == 200, saved.text
