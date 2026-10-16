"""M4 local freshness: hash and mtime, stale and unknown, auto-verify from cues.

Web re-fetch is not covered. Remote kinds stay unknown.
"""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session

from app.db.models import ContentItem
from app.db.session import get_engine
from app.decision.client import DecisionUnavailable
from app.freshness import verifier
from app.main import app


@pytest.fixture()
def token(monkeypatch: pytest.MonkeyPatch) -> str:
    monkeypatch.setenv("SIDECAR_TOKEN", "test-token")
    return "test-token"


@pytest.fixture()
def client(token: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.delenv("FEATURE_VERIFY", raising=False)
    monkeypatch.delenv("VITE_FEATURE_VERIFY", raising=False)
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


def write_note(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8")


def import_path(client: TestClient, token: str, path: Path) -> dict:
    response = client.post(
        "/library/import",
        headers=headers(token),
        json={"path": str(path), "filename": path.name},
    )
    assert response.status_code == 200, response.text
    return response.json()


def item_row(item_id: int) -> ContentItem:
    with Session(get_engine()) as db:
        row = db.get(ContentItem, item_id)
        assert row is not None
        db.expunge(row)
        return row


def test_flag_off_keeps_verify_501_and_skips_auto_check(client: TestClient, token: str, tmp_path: Path) -> None:
    note = tmp_path / "qingjiao-notes.md"
    write_note(note, "每斤青椒放盐三克。腌制时间是两小时。")
    imported = import_path(client, token, note)
    denied = client.post("/verify", headers=headers(token), json={"item_ids": [imported["id"]]})
    assert denied.status_code == 501
    assert client.get(f"/verify/{imported['id']}", headers=headers(token)).status_code == 501

    chat = client.post(
        "/chat",
        headers=headers(token),
        json={"message": "最新的青椒腌制要多久", "mode": "personal", "sources": ["local"]},
    )
    events = parse_sse(chat.text)
    assert [name for name, _payload in events if name == "freshness"] == []
    needs = next(payload for name, payload in events if name == "decision" and payload["question_id"] == "needs_verify")
    assert needs["result"] is True
    assert needs["applied"] is False
    assert "已查证" not in "".join(payload.get("text", "") for name, payload in events if name == "delta")


def test_hash_mtime_marks_fresh_stale_and_unknown(client: TestClient, token: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FEATURE_VERIFY", "1")
    monkeypatch.setenv("VERIFY_DEBOUNCE_S", "0")
    note = tmp_path / "qingjiao-notes.md"
    write_note(note, "每斤青椒放盐三克。腌制时间是两小时。")
    imported = import_path(client, token, note)
    item_id = int(imported["id"])
    listed = client.get("/library/items", headers=headers(token)).json()["items"][0]
    assert listed["freshness_state"] == "fresh"
    assert listed["content_hash"]
    assert listed["source_path"] == str(note.resolve())
    assert listed["source_mtime"]
    assert listed["fetched_at"]

    seen: dict[str, str] = {}
    real_read = verifier.read_source_bytes

    def spy(path: Path) -> bytes:
        seen["state"] = item_row(item_id).freshness_state
        return real_read(path)

    monkeypatch.setattr(verifier, "read_source_bytes", spy)
    checked = client.post("/verify", headers=headers(token), json={"item_ids": [item_id], "mode": "hash_mtime"})
    assert checked.status_code == 200, checked.text
    body = checked.json()
    assert body["state"] == "done"
    assert body["stats"]["items"][0]["state"] == "fresh"
    assert body["stats"]["items"][0]["prompt_update"] is False
    assert seen["state"] == "verifying"
    job = client.get(f"/verify/{body['job_id']}", headers=headers(token))
    assert job.status_code == 200
    assert job.json()["state"] == "done"
    assert item_row(item_id).freshness_state == "fresh"

    write_note(note, "每斤青椒放盐三克。腌制时间改成了四小时。")
    future = time.time() + 30
    note.touch()
    os_utime = __import__("os").utime
    os_utime(note, (future, future))
    changed = client.post("/verify", headers=headers(token), json={"item_ids": [item_id]})
    assert changed.status_code == 200, changed.text
    stale = changed.json()["stats"]["items"][0]
    assert stale["state"] == "stale"
    assert stale["reason"] == "hash_changed"
    assert stale["action"] == "prompt_update"
    assert stale["prompt_update"] is True
    assert "四小时" not in (item_row(item_id).body_text or "")
    assert "两小时" in (item_row(item_id).body_text or "")

    note.unlink()
    missing = client.post("/verify", headers=headers(token), json={"item_ids": [item_id]})
    assert missing.status_code == 200, missing.text
    unknown = missing.json()["stats"]["items"][0]
    assert unknown["state"] == "unknown"
    assert unknown["reason"] == "missing"
    assert unknown["state"] != "fresh"
    assert item_row(item_id).freshness_state == "unknown"


def test_mtime_change_with_same_bytes_is_stale(client: TestClient, token: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FEATURE_VERIFY", "1")
    monkeypatch.setenv("VERIFY_DEBOUNCE_S", "0")
    note = tmp_path / "salt.md"
    write_note(note, "每斤青椒放盐三克。")
    item_id = int(import_path(client, token, note)["id"])
    future = time.time() + 90
    __import__("os").utime(note, (future, future))
    checked = client.post("/verify", headers=headers(token), json={"item_ids": [item_id]})
    row = checked.json()["stats"]["items"][0]
    assert row["state"] == "stale"
    assert row["reason"] == "mtime_changed"
    assert row["prompt_update"] is True


def test_unreadable_source_is_unknown(client: TestClient, token: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FEATURE_VERIFY", "1")
    monkeypatch.setenv("VERIFY_DEBOUNCE_S", "0")
    note = tmp_path / "salt.md"
    write_note(note, "每斤青椒放盐三克。")
    item_id = int(import_path(client, token, note)["id"])

    def explode(path: Path) -> bytes:
        raise PermissionError(path)

    monkeypatch.setattr(verifier, "read_source_bytes", explode)
    checked = client.post("/verify", headers=headers(token), json={"item_ids": [item_id]})
    assert checked.status_code == 200
    assert checked.json()["stats"]["items"][0]["state"] == "unknown"
    assert checked.json()["stats"]["items"][0]["reason"] == "unreadable"


def test_debounce_skips_reread_until_mtime_moves(client: TestClient, token: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FEATURE_VERIFY", "1")
    monkeypatch.setenv("VERIFY_DEBOUNCE_S", "0")
    note = tmp_path / "salt.md"
    write_note(note, "每斤青椒放盐三克。")
    item_id = int(import_path(client, token, note)["id"])
    calls = {"n": 0}
    real_read = verifier.read_source_bytes

    def spy(path: Path) -> bytes:
        calls["n"] += 1
        return real_read(path)

    monkeypatch.setattr(verifier, "read_source_bytes", spy)
    first = client.post("/verify", headers=headers(token), json={"item_ids": [item_id]})
    monkeypatch.setenv("VERIFY_DEBOUNCE_S", "60")
    second = client.post("/verify", headers=headers(token), json={"item_ids": [item_id]})
    assert first.json()["stats"]["items"][0]["state"] == "fresh"
    assert second.json()["stats"]["items"][0]["action"] == "debounced"
    assert calls["n"] == 1
    future = time.time() + 90
    __import__("os").utime(note, (future, future))
    third = client.post("/verify", headers=headers(token), json={"item_ids": [item_id]})
    assert third.json()["stats"]["items"][0]["reason"] == "mtime_changed"
    assert calls["n"] == 2


def test_refetch_updates_the_library(client: TestClient, token: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FEATURE_VERIFY", "1")
    monkeypatch.setenv("VERIFY_DEBOUNCE_S", "0")
    note = tmp_path / "qingjiao-notes.md"
    write_note(note, "每斤青椒放盐三克。腌制时间是两小时。")
    item_id = int(import_path(client, token, note)["id"])
    write_note(note, "每斤青椒放盐五克。腌制时间是四小时。")
    updated = client.post("/verify", headers=headers(token), json={"item_ids": [item_id], "mode": "refetch"})
    assert updated.status_code == 200, updated.text
    assert updated.json()["stats"]["items"][0]["state"] == "fresh"
    assert updated.json()["stats"]["items"][0]["action"] == "updated"
    assert "四小时" in (item_row(item_id).body_text or "")
    found = client.post(
        "/search",
        headers=headers(token),
        json={"query": "腌制时间是四小时", "mode": "personal", "sources": ["local"]},
    )
    assert found.status_code == 200
    assert found.json()["hits"]
    assert "四小时" in found.json()["hits"][0]["text"]


def test_web_source_stays_unknown_without_a_fetch(client: TestClient, token: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FEATURE_VERIFY", "1")

    def forbid(*_args, **_kwargs):
        raise AssertionError("web verify must not open a client")

    monkeypatch.setattr("httpx.Client", forbid)
    with Session(get_engine()) as db:
        row = ContentItem(
            platform="web",
            source_id="page",
            content_hash="webhash",
            url="https://example.invalid/page",
            title="网页",
            body_text="最新公告",
            source_kind="web",
            freshness_state="fresh",
            index_state="indexed",
        )
        db.add(row)
        db.commit()
        db.refresh(row)
        item_id = int(row.id or 0)
    checked = client.post("/verify", headers=headers(token), json={"item_ids": [item_id]})
    assert checked.status_code == 200
    row = checked.json()["stats"]["items"][0]
    assert row["state"] == "unknown"
    assert row["reason"] == "deferred_web"
    assert item_row(item_id).freshness_state == "unknown"


def test_freshness_cue_auto_verifies_without_changing_shadow(client: TestClient, token: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FEATURE_VERIFY", "1")
    monkeypatch.setenv("VERIFY_DEBOUNCE_S", "0")
    note = tmp_path / "qingjiao-notes.md"
    write_note(note, "每斤青椒放盐三克。腌制时间是两小时。")
    import_path(client, token, note)

    quiet = client.post(
        "/chat",
        headers=headers(token),
        json={"message": "青椒腌制要多久", "mode": "personal", "sources": ["local"]},
    )
    quiet_events = parse_sse(quiet.text)
    assert [name for name, _payload in quiet_events if name == "freshness"] == []
    citation = next(payload for name, payload in quiet_events if name == "citation")
    assert citation["freshness_state"] == "fresh"
    assert "两小时" in "".join(payload["text"] for name, payload in quiet_events if name == "delta")

    write_note(note, "每斤青椒放盐三克。腌制时间改成了四小时。")
    __import__("os").utime(note, (time.time() + 30, time.time() + 30))
    chat = client.post(
        "/chat",
        headers=headers(token),
        json={"message": "最新的青椒腌制要多久", "mode": "personal", "sources": ["local"]},
    )
    events = parse_sse(chat.text)
    needs = next(payload for name, payload in events if name == "decision" and payload["question_id"] == "needs_verify")
    assert needs["result"] is True
    assert needs["applied"] is False
    freshness = [payload for name, payload in events if name == "freshness"]
    assert freshness
    assert freshness[0]["state"] == "stale"
    assert freshness[0]["prompt_update"] is True
    cited = next(payload for name, payload in events if name == "citation")
    assert cited["freshness_state"] == "stale"
    answer = "".join(payload["text"] for name, payload in events if name == "delta")
    assert "可能已过期" in answer
    assert "两小时" in answer
    names = [name for name, _payload in events]
    assert names.index("freshness") < names.index("citation") < names.index("delta") < names.index("done")
    assert events[-1][1]["verify_ms"] >= 0
    logs = client.get("/decision/logs", headers=headers(token)).json()["items"]
    stored = next(item for item in logs if item["question_id"] == "needs_verify" and item["result"]["value"] is True)
    assert stored["applied"] is False

    current = client.post(
        "/chat",
        headers=headers(token),
        json={"message": "当前青椒放多少盐", "mode": "personal", "sources": ["local"]},
    )
    current_events = parse_sse(current.text)
    assert any(name == "freshness" for name, _payload in current_events)


def test_verify_skip_does_not_block_the_answer(client: TestClient, token: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FEATURE_VERIFY", "1")
    note = tmp_path / "qingjiao-notes.md"
    write_note(note, "每斤青椒放盐三克。腌制时间是两小时。")
    item_id = int(import_path(client, token, note)["id"])
    chat = client.post(
        "/chat",
        headers=headers(token),
        json={"message": "最新的青椒腌制要多久", "mode": "personal", "verify": "skip", "sources": ["local"]},
    )
    events = parse_sse(chat.text)
    assert [name for name, _payload in events if name == "freshness"] == []
    answer = "".join(payload["text"] for name, payload in events if name == "delta")
    assert "两小时" in answer
    assert "未查证" in answer
    assert item_row(item_id).freshness_state == "fresh"


def test_missing_source_during_auto_verify_is_unknown(client: TestClient, token: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FEATURE_VERIFY", "1")
    monkeypatch.setenv("VERIFY_DEBOUNCE_S", "0")
    note = tmp_path / "qingjiao-notes.md"
    write_note(note, "每斤青椒放盐三克。腌制时间是两小时。")
    import_path(client, token, note)
    note.unlink()
    chat = client.post(
        "/chat",
        headers=headers(token),
        json={"message": "现在青椒腌制要多久", "mode": "personal", "sources": ["local"]},
    )
    events = parse_sse(chat.text)
    freshness = next(payload for name, payload in events if name == "freshness")
    assert freshness["state"] == "unknown"
    assert "无法核实" in "".join(payload["text"] for name, payload in events if name == "delta")
    assert "两小时" in "".join(payload["text"] for name, payload in events if name == "delta")


def test_broken_decider_does_not_block_chat_when_verify_is_on(client: TestClient, token: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FEATURE_VERIFY", "1")
    note = tmp_path / "qingjiao-notes.md"
    write_note(note, "每斤青椒放盐三克。腌制时间是两小时。")
    import_path(client, token, note)

    class Boom:
        def choice(self, *_args, **_kwargs):
            raise DecisionUnavailable("down")

        def score(self, *_args, **_kwargs):
            raise DecisionUnavailable("down")

        def noul(self, *_args, **_kwargs):
            raise DecisionUnavailable("down")

    app.state.decision_client = Boom()
    chat = client.post(
        "/chat",
        headers=headers(token),
        json={"message": "最新的青椒腌制要多久", "mode": "personal", "sources": ["local"]},
    )
    assert chat.status_code == 200
    events = parse_sse(chat.text)
    assert events[-1][0] == "done"
    assert "两小时" in "".join(payload["text"] for name, payload in events if name == "delta")
    assert [name for name, _payload in events if name == "freshness"] == []


def test_concurrent_verify_shares_one_read(client: TestClient, token: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FEATURE_VERIFY", "1")
    monkeypatch.setenv("VERIFY_DEBOUNCE_S", "0")
    note = tmp_path / "salt.md"
    write_note(note, "每斤青椒放盐三克。")
    item_id = int(import_path(client, token, note)["id"])
    entered = threading.Event()
    release = threading.Event()
    calls = {"n": 0}
    real_read = verifier.read_source_bytes

    def blocking(path: Path) -> bytes:
        calls["n"] += 1
        entered.set()
        assert release.wait(5)
        return real_read(path)

    monkeypatch.setattr(verifier, "read_source_bytes", blocking)
    holder: dict[str, int] = {}

    def first() -> None:
        holder["id"] = verifier.run_verify([item_id], mode="hash_mtime")

    worker = threading.Thread(target=first)
    worker.start()
    assert entered.wait(5)
    joined: dict[str, int] = {}

    def second() -> None:
        joined["id"] = verifier.run_verify([item_id], mode="hash_mtime")

    other = threading.Thread(target=second)
    other.start()
    time.sleep(0.2)
    release.set()
    worker.join(5)
    other.join(5)
    assert holder["id"] == joined["id"]
    assert calls["n"] == 1
    assert item_row(item_id).freshness_state == "fresh"
