"""Verify "delete all conversations" against a disposable sidecar.

Start a throwaway instance first:
  $env:DATA_DIR="$PWD\.cache\tmp\purge-data"; $env:SIDECAR_TOKEN="devtoken"
  .\.venv\Scripts\python.exe -m uvicorn app.main:app --port 8766   # cwd: services/sidecar

Then:  python scripts/win/07-verify-purge.py 8766
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import httpx

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 8766
BASE = f"http://127.0.0.1:{PORT}"
TOKEN = os.getenv("SIDECAR_TOKEN") or "devtoken"
HEADERS = {"x-sidecar-token": TOKEN}

failures: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f" :: {detail}" if detail else ""))
    if not ok:
        failures.append(name)


with httpx.Client(base_url=BASE, timeout=60.0, trust_env=False) as client:
    health = client.get("/health", headers=HEADERS)
    check("temp sidecar is up", health.status_code == 200, health.text[:120])
    data_dir = Path(health.json().get("data_dir") or "")

    folder = client.post("/folders", headers=HEADERS, json={"name": "清理测试"}).json()
    project = client.post("/projects", headers=HEADERS, json={"name": "清理项目"}).json()
    ids = []
    for index in range(2):
        created = client.post(
            "/conversations",
            headers=HEADERS,
            json={"title": f"待清理对话 {index + 1}", "folder_id": folder.get("id"), "project_id": project.get("id")},
        ).json()
        cid = created["conversation"]["id"]
        ids.append(cid)
        for role, text in (("user", "测试问题"), ("assistant", "测试回答")):
            client.post(f"/conversations/{cid}/messages", headers=HEADERS, json={"role": role, "content": text})

    library_before = client.get("/library/items", headers=HEADERS).json().get("total")
    stats_before = client.get("/conversations/stats", headers=HEADERS).json()
    check("seeded conversations", stats_before.get("conversations", 0) >= 2,
          f"conversations={stats_before.get('conversations')} messages={stats_before.get('messages')}")

    bad = client.post("/conversations/clear", headers=HEADERS, json={"confirm": "nope"})
    check("wrong confirm -> 400", bad.status_code == 400)

    result = client.post("/conversations/clear", headers=HEADERS, json={"confirm": "DELETE_ALL"})
    payload = result.json() if result.status_code == 200 else {}
    deleted = payload.get("deleted", {})
    check("POST /conversations/clear", result.status_code == 200 and payload.get("ok") is True,
          json.dumps(deleted, ensure_ascii=False))
    check("counts cover conversations and messages",
          deleted.get("conversations", 0) >= 2 and deleted.get("messages", 0) >= 4,
          f"deleted={deleted}")
    check("folders and projects cleared", deleted.get("folders", 0) >= 1 and deleted.get("projects", 0) >= 1)

    after = client.get("/conversations", headers=HEADERS).json()
    check("conversation list is empty", after.get("total") == 0, f"total={after.get('total')}")
    check("folders empty", client.get("/folders", headers=HEADERS).json().get("total") == 0)
    check("projects empty", client.get("/projects", headers=HEADERS).json().get("total") == 0)

    backup = Path(payload.get("backup_path") or "")
    check("backup written", backup.is_file() and backup.stat().st_size > 0, str(backup))
    if backup.is_file():
        saved = json.loads(backup.read_text(encoding="utf-8"))
        check("backup contains the deleted conversations",
              saved.get("schema") == "chatchat.purge-backup.v1" and len(saved.get("conversations") or []) >= 2,
              f"conversations={len(saved.get('conversations') or [])}")
        check("backup lives under DATA_DIR", str(backup).lower().startswith(str(data_dir).lower()))

    library_after = client.get("/library/items", headers=HEADERS).json().get("total")
    check("knowledge base untouched", library_before == library_after,
          f"before={library_before} after={library_after}")

print()
if failures:
    print("FAILED:", ", ".join(failures))
    raise SystemExit(1)
print("ALL PURGE CHECKS PASSED")
