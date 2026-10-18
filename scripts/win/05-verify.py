"""End-to-end verification for the local deployment.

Runs against a live sidecar:  python scripts/win/05-verify.py [port]
Prints one line per check and exits non-zero when a check fails.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import httpx

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parents[2]
PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 8765
BASE = f"http://127.0.0.1:{PORT}"
TOKEN = os.getenv("SIDECAR_TOKEN") or "devtoken"
HEADERS = {"x-sidecar-token": TOKEN}

failures: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f" :: {detail}" if detail else ""))
    if not ok:
        failures.append(name)


def read_events(response: httpx.Response) -> list[tuple[str, dict]]:
    events: list[tuple[str, dict]] = []
    event = "message"
    data: list[str] = []
    for line in response.iter_lines():
        if line.startswith("event:"):
            event = line[6:].strip()
        elif line.startswith("data:"):
            data.append(line[5:].strip())
        elif line == "":
            if data:
                raw = "\n".join(data)
                try:
                    payload = json.loads(raw)
                except json.JSONDecodeError:
                    payload = {"raw": raw}
                events.append((event, payload))
            event, data = "message", []
    if data:
        try:
            payload = json.loads("\n".join(data))
        except json.JSONDecodeError:
            payload = {"raw": "\n".join(data)}
        events.append((event, payload))
    return events


with httpx.Client(base_url=BASE, timeout=120.0, trust_env=False) as client:
    health = client.get("/health", headers=HEADERS)
    body = health.json() if health.status_code == 200 else {}
    check("GET /health 200", health.status_code == 200, json.dumps(body, ensure_ascii=False))
    data_dir = str(body.get("data_dir") or "")
    check("DATA_DIR inside workspace", data_dir.lower().startswith(str(ROOT).lower()), data_dir)

    unauth = client.get("/health")
    check("missing token -> 401", unauth.status_code == 401, f"status={unauth.status_code}")

    sample = ROOT / "samples" / "m3" / "qingjiao-notes.md"
    text = sample.read_text(encoding="utf-8") if sample.is_file() else "每斤青椒放盐三克。腌制时间是两小时。"
    imported = client.post(
        "/library/import",
        headers=HEADERS,
        json={"filename": "verify-sample.md", "text": text},
    )
    payload = imported.json() if imported.status_code == 200 else {}
    item_id = payload.get("item", {}).get("id") if isinstance(payload.get("item"), dict) else payload.get("id")
    check("POST /library/import", imported.status_code == 200, json.dumps(payload, ensure_ascii=False)[:300])

    items = client.get("/library/items", headers=HEADERS).json()
    check("GET /library/items", int(items.get("total") or 0) >= 1, f"total={items.get('total')}")

    search = client.post("/search", headers=HEADERS, json={"query": "青椒腌制时间", "mode": "personal"})
    hits = search.json().get("hits") if search.status_code == 200 else None
    check("POST /search", bool(hits), f"hits={len(hits or [])}")

    with client.stream(
        "POST",
        "/chat",
        headers=HEADERS,
        json={"mode": "personal", "message": "青椒腌制要多久", "sources": ["local"], "clarify": "skip"},
    ) as response:
        events = read_events(response)
    kinds = [name for name, _ in events]
    check("/chat streams citation+delta+done", "delta" in kinds and "done" in kinds, f"events={kinds}")

    with client.stream(
        "POST",
        "/ask",
        headers=HEADERS,
        json={"message": "青椒腌制要多久", "mode": "personal", "clarify": "skip", "verify": "skip"},
    ) as response:
        ask_status = response.status_code
        ask_events = read_events(response)
    ask_kinds = [name for name, _ in ask_events]
    check("POST /ask streaming (FEATURE_ASK=1)", ask_status == 200 and "done" in ask_kinds, f"status={ask_status} events={ask_kinds}")

    settings = client.get("/settings/ask", headers=HEADERS)
    check("GET /settings/ask", settings.status_code == 200, json.dumps(settings.json(), ensure_ascii=False)[:200] if settings.status_code == 200 else "")

    if isinstance(item_id, int):
        removed = client.delete(f"/library/items/{item_id}", headers=HEADERS)
        check("DELETE /library/items/{id}", removed.status_code == 200, f"status={removed.status_code}")

print()
if failures:
    print("FAILED:", ", ".join(failures))
    raise SystemExit(1)
print("ALL CHECKS PASSED")
