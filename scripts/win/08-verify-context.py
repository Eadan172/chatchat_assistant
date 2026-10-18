"""Verify local-first retrieval, clickable web citations, follow-up context and the 1M cap.

Run against the live sidecar:  python scripts/win/08-verify-context.py 8765
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

FIXTURE = """# Dify 测试样本

Dify 是一个开源的 LLM 应用开发平台，支持可视化工作流、RAG 知识库与 Agent。
它支持接入 OpenAI、Anthropic、DeepSeek、通义千问、豆包、Kimi 以及本地 Ollama 等模型。
安装方式为 Docker 一键部署：git clone 后执行 docker compose up -d，访问 http://localhost:80。
"""

failures: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f" :: {detail}" if detail else ""))
    if not ok:
        failures.append(name)


def run_ask(client: httpx.Client, payload: dict) -> tuple[dict, list[dict], str]:
    events: list[tuple[str, dict]] = []
    with client.stream("POST", "/ask", json=payload) as response:
        event, data = "message", []
        for line in response.iter_lines():
            if line.startswith("event:"):
                event = line[6:].strip()
            elif line.startswith("data:"):
                data.append(line[5:].strip())
            elif line == "" and data:
                try:
                    events.append((event, json.loads("\n".join(data))))
                except json.JSONDecodeError:
                    pass
                event, data = "message", []
    done = next((item for name, item in events if name == "done"), {})
    searching = next((item for name, item in events if name == "searching"), {})
    text = "".join(item.get("text", "") for name, item in events if name == "delta")
    citations = [item for name, item in events if name == "citation"]
    return {**done, "searching": searching}, citations, text


stamp = datetime.now().strftime("%H%M%S")
with httpx.Client(base_url=BASE, headers=HEADERS, timeout=240, trust_env=False) as client:
    check("sidecar up", client.get("/health").status_code == 200)

    imported = client.post(
        "/import/documents",
        json={"filename": f"dify-context-{stamp}.md", "text": FIXTURE, "source": "local", "title": "Dify 测试样本"},
    )
    item_id = (imported.json().get("item") or {}).get("id") if imported.status_code == 200 else None
    check("import local fixture", imported.status_code == 200, f"item={item_id}")

    hits = client.post("/search", json={"query": "Dify", "mode": "personal"}).json().get("hits") or []
    check("search finds the local Dify document", bool(hits), f"hits={len(hits)}")

    done, citations, text = run_ask(
        client,
        {"message": "Dify 是什么？", "clarify": "skip", "verify": "skip", "locale": "zh"},
    )
    sources = (done.get("searching") or {}).get("sources") or []
    check("no kb_name still searches local", "local" in sources, f"sources={sources}")
    check("answer cites the local document",
          any((c.get("source_kind") or "").startswith("local") for c in citations),
          f"citations={len(citations)}")
    check("web citations carry openable urls",
          any(str(c.get("url") or "").startswith("http") for c in citations),
          f"urls={[c.get('url') for c in citations if str(c.get('url') or '').startswith('http')][:2]}")

    cid = done.get("conversation_id")
    check("conversation created", bool(cid), f"id={cid}")
    detail1 = client.get(f"/conversations/{cid}").json()
    limit = detail1.get("context", {}).get("limit")
    check("context limit is 1M", limit == 1_000_000, f"limit={limit}")
    before = len(detail1.get("messages") or [])

    done2, _, text2 = run_ask(
        client,
        {
            "message": "它支持哪些模型？",
            "conversation_id": cid,
            "clarify": "skip",
            "verify": "skip",
            "locale": "zh",
        },
    )
    check("follow-up stays in the same conversation", done2.get("conversation_id") == cid)
    detail2 = client.get(f"/conversations/{cid}").json()
    after = len(detail2.get("messages") or [])
    check("follow-up appends two messages", after == before + 2, f"{before} -> {after}")
    check("follow-up answers with the product context",
          "Dify" in (text2 or "") or "模型" in (text2 or ""),
          (text2 or "")[:120].replace("\n", " "))

    client.delete(f"/conversations/{cid}")
    if item_id:
        client.delete(f"/library/items/{item_id}")

print()
if failures:
    print("FAILED:", ", ".join(failures))
    raise SystemExit(1)
print("ALL CONTEXT CHECKS PASSED")
