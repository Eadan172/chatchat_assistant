"""M7 acceptance checks: i18n, import channels, style routing, conversation memory.

Run against a live sidecar:  python scripts/win/06-verify-m7.py [port]
Creates demo data (folders, projects, imported conversations) for the UI.
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime

import httpx

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 8765
BASE = f"http://127.0.0.1:{PORT}"
TOKEN = os.getenv("SIDECAR_TOKEN") or "devtoken"
HEADERS = {"x-sidecar-token": TOKEN}

failures: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f" :: {detail}" if detail else ""))
    if not ok:
        failures.append(name)


def events(response: httpx.Response) -> list[tuple[str, dict]]:
    rows: list[tuple[str, dict]] = []
    event, data = "message", []
    for line in response.iter_lines():
        if line.startswith("event:"):
            event = line[6:].strip()
        elif line.startswith("data:"):
            data.append(line[5:].strip())
        elif line == "" and data:
            raw = "\n".join(data)
            try:
                rows.append((event, json.loads(raw)))
            except json.JSONDecodeError:
                rows.append((event, {"raw": raw}))
            event, data = "message", []
    return rows


CHATGPT = {
    "conversations": [
        {
            "id": "chatgpt-demo-1",
            "title": "青椒腌制要多久",
            "create_time": 1710000000,
            "current_node": "b",
            "mapping": {
                "a": {
                    "id": "a",
                    "parent": None,
                    "message": {
                        "author": {"role": "user"},
                        "content": {"content_type": "text", "parts": ["青椒腌制要多久？"]},
                    },
                },
                "b": {
                    "id": "b",
                    "parent": "a",
                    "message": {
                        "author": {"role": "assistant"},
                        "content": {"content_type": "text", "parts": ["一般两小时左右，盐三克就够了。"]},
                    },
                },
            },
        }
    ]
}

CLAUDE_JSONL = (
    '{"type":"summary","summary":"FTS5 是什么"}\n'
    '{"type":"user","message":{"role":"user","content":[{"type":"text","text":"帮我解释一下 FTS5"}]}}\n'
    '{"type":"assistant","message":{"role":"assistant","content":[{"type":"text","text":"FTS5 是 SQLite 的全文检索模块。"}]}}\n'
)

with httpx.Client(base_url=BASE, timeout=120.0, trust_env=False) as client:
    # 1. i18n catalog
    zh = client.get("/styles", headers=HEADERS, params={"locale": "zh"}).json()
    en = client.get("/styles", headers=HEADERS, params={"locale": "en"}).json()
    check("GET /styles zh+en", len(zh.get("styles") or []) >= 13 and len(en.get("styles") or []) >= 13,
          f"zh={len(zh.get('styles') or [])} en={len(en.get('styles') or [])}")
    labels = {row["id"]: row["label"] for row in zh.get("styles") or []}
    check("style catalog has life/knowledge/technical/design",
          {"life-xiaohongshu", "knowledge-zhihu", "technical-manual", "design-professional"} <= set(labels),
          json.dumps(labels, ensure_ascii=False)[:160])

    # 2. import channels
    sources = client.get("/import/sources", headers=HEADERS).json()
    ids = {row["id"]: row for row in sources.get("sources") or []}
    check("GET /import/sources", {"local", "ima", "obsidian", "feishu", "tencent_docs"} <= set(ids),
          f"ids={sorted(ids)}")
    check("placeholders flagged", ids.get("obsidian", {}).get("implemented") is False)
    browse = client.post("/import/sources/obsidian/browse", headers=HEADERS, json={})
    detail = browse.json().get("detail") if browse.status_code != 200 else {}
    check("placeholder browse -> 501", browse.status_code == 501 and isinstance(detail, dict)
          and detail.get("code") == "import.not_implemented", f"status={browse.status_code}")

    stamp = datetime.now().strftime("%H%M%S")
    doc = client.post(
        "/import/documents",
        headers=HEADERS,
        json={"filename": f"unified-demo-{stamp}.md", "text": "统一导入接口示例：每斤青椒放盐三克。", "source": "local"},
    )
    doc_item = doc.json().get("item", {}) if doc.status_code == 200 else {}
    check("POST /import/documents", doc.status_code == 200 and (
        doc_item.get("chunks", 0) >= 1 or doc_item.get("index_state") == "indexed"
    ), f"status={doc.status_code} item={json.dumps(doc_item, ensure_ascii=False)[:120]}")

    # 3. conversation memory
    folder_rows = client.get("/folders", headers=HEADERS).json().get("items") or []
    folder = next((row for row in folder_rows if row.get("name") == "工作记录"), None)
    if folder is None:
        folder = client.post("/folders", headers=HEADERS, json={"name": "工作记录"}).json()
    project_rows = client.get("/projects", headers=HEADERS).json().get("items") or []
    project = next((row for row in project_rows if row.get("name") == "知识引擎"), None)
    if project is None:
        project = client.post("/projects", headers=HEADERS, json={"name": "知识引擎", "color": "#5f8f78"}).json()
    check("POST /folders + /projects", bool(folder.get("id")) and bool(project.get("id")),
          f"folder={folder.get('id')} project={project.get('id')}")

    created = client.post(
        "/conversations",
        headers=HEADERS,
        json={"title": "青椒腌制问答", "folder_id": folder.get("id"), "project_id": project.get("id"), "locale": "zh"},
    ).json()
    cid = created.get("conversation", {}).get("id")
    check("POST /conversations", bool(cid), f"id={cid}")
    for role, text in (
        ("user", "青椒腌制要多久？"),
        ("assistant", "一般两小时左右，盐三克就够了。"),
        ("user", "需要放冰箱吗？"),
        ("assistant", "腌制阶段放阴凉处即可，成品再冷藏。"),
    ):
        client.post(f"/conversations/{cid}/messages", headers=HEADERS, json={"role": role, "content": text})
    loaded = client.get(f"/conversations/{cid}", headers=HEADERS).json()
    check("conversation detail with 4 messages", len(loaded.get("messages") or []) == 4,
          f"tokens={loaded.get('context', {}).get('tokens')}")

    moved = client.patch(f"/conversations/{cid}", headers=HEADERS,
                         json={"pinned": True, "style": "knowledge-zhihu"}).json()
    check("PATCH conversation (pin/style)", moved.get("conversation", {}).get("pinned") is True
          and moved.get("conversation", {}).get("style") == "knowledge-zhihu")

    msgs = loaded.get("messages") or []
    branch = msgs[1]["id"] if len(msgs) > 1 else None
    fork = client.post(f"/conversations/{cid}/fork", headers=HEADERS,
                       json={"message_id": branch, "title": "青椒腌制 · 分叉"}).json()
    fork_conv = fork.get("conversation", {})
    check("POST fork keeps prefix", len(fork.get("messages") or []) == 2
          and fork_conv.get("forked_from_id") == cid, f"fork={fork_conv.get('id')}")

    md = client.get(f"/conversations/{cid}/export", headers=HEADERS, params={"format": "md"}).json()
    check("export markdown", md.get("format") == "md" and "青椒腌制要多久" in md.get("content", ""))
    bulk = client.get("/conversations/export", headers=HEADERS).json()
    check("export all json", bulk.get("payload", {}).get("schema") == "chatchat.export.v1",
          f"count={len(bulk.get('payload', {}).get('conversations') or [])}")

    imp1 = client.post("/conversations/import", headers=HEADERS,
                       json={"filename": "chatgpt.json", "content": json.dumps(CHATGPT, ensure_ascii=False),
                             "folder_id": folder.get("id")}).json()
    check("import ChatGPT export", imp1.get("total") == 1,
          json.dumps(imp1.get("imported", [{}])[0], ensure_ascii=False)[:120])
    imp2 = client.post("/conversations/import", headers=HEADERS,
                       json={"filename": "claude-code.jsonl", "content": CLAUDE_JSONL}).json()
    check("import Claude Code JSONL", imp2.get("total") == 1,
          json.dumps(imp2.get("imported", [{}])[0], ensure_ascii=False)[:120])

    stats = client.get("/conversations/stats", headers=HEADERS).json()
    check("GET /conversations/stats", stats.get("conversations", 0) >= 3, json.dumps(stats, ensure_ascii=False))

    # 4. preferences
    ui = client.post("/settings/ui", headers=HEADERS, json={"locale": "en", "default_style": "auto"}).json()
    check("POST /settings/ui persists", ui.get("locale") == "en", json.dumps(ui, ensure_ascii=False))
    client.post("/settings/ui", headers=HEADERS, json={"locale": "zh"})
    app_info = client.get("/settings/app", headers=HEADERS).json()
    check("GET /settings/app", app_info.get("version") == "0.1.0" and app_info.get("db_bytes", 0) > 0,
          f"data_dir={app_info.get('data_dir')}")

    # 5. style + locale on the answer path
    with client.stream("POST", "/ask", headers=HEADERS, json={
        "message": "青椒腌制要多久",
        "clarify": "skip",
        "verify": "skip",
        "locale": "en",
        "style": "technical-manual",
        "conversation_id": cid,
    }) as response:
        ask_events = events(response)
    done = next((payload for name, payload in ask_events if name == "done"), {})
    check("/ask returns style + locale", done.get("style", {}).get("id") == "technical-manual"
          and done.get("locale") == "en", json.dumps(done.get("style"), ensure_ascii=False))
    check("/ask appends to conversation", done.get("conversation_id") == cid,
          f"conversation_id={done.get('conversation_id')}")

    en_chat = client.post("/chat", headers=HEADERS, json={
        "message": "unknown-topic-xyz",
        "sources": ["local"],
        "clarify": "skip",
        "locale": "en",
    })
    body = en_chat.text
    check("/chat english system text", "No relevant content was found" in body or "knowledge base" in body,
          body[-160:].replace("\n", " "))

print()
if failures:
    print("FAILED:", ", ".join(failures))
    raise SystemExit(1)
print("ALL M7 CHECKS PASSED")
