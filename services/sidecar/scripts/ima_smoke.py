#!/usr/bin/env python3
"""Call ima list-knowledge-bases and one keyword search.

Uses IMA_CLIENT_ID and IMA_API_KEY from the environment only. Prints HTTP
status, business code, and counts. Does not print titles, snippets, ids, or
secrets.

    cd services/sidecar
    IMA_CLIENT_ID=... IMA_API_KEY=... python scripts/ima_smoke.py

Optional: IMA_SMOKE_QUERY (default 笔记), IMA_BASE_URL.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.connectors.ima_client import (  # noqa: E402
    PATH_SEARCH_KNOWLEDGE,
    PATH_SEARCH_KNOWLEDGE_BASE,
    PATH_SEARCH_NOTE,
    ImaError,
)
from app.connectors.ima_service import open_ima_client  # noqa: E402


def _count(data: dict, key: str) -> int:
    value = data.get(key)
    return len(value) if isinstance(value, list) else 0


def _line(name: str, http_status: int, code: int | None, count: int) -> str:
    code_text = "" if code is None else str(code)
    return f"{name} http={http_status} code={code_text} count={count}"


def main() -> int:
    query = (os.getenv("IMA_SMOKE_QUERY") or "笔记").strip() or "笔记"
    try:
        with open_ima_client(env_only=True) as client:
            try:
                listed = client.post(PATH_SEARCH_KNOWLEDGE_BASE, {"query": "", "cursor": "", "limit": 20})
            except ImaError as exc:
                print(_line("list_kb", exc.http_status, exc.code, 0))
                print(_line("search", exc.http_status, exc.code, 0))
                return 1
            bases = listed.data.get("info_list") if isinstance(listed.data.get("info_list"), list) else []
            print(_line("list_kb", listed.http_status, listed.code, len(bases)))
            kb_id = ""
            for item in bases:
                if isinstance(item, dict) and item.get("id"):
                    kb_id = str(item["id"])
                    break
            try:
                if kb_id:
                    found = client.post(
                        PATH_SEARCH_KNOWLEDGE,
                        {"query": query, "cursor": "", "knowledge_base_id": kb_id},
                    )
                    count = _count(found.data, "info_list")
                else:
                    found = client.post(
                        PATH_SEARCH_NOTE,
                        {"search_type": 1, "query_info": {"content": query}, "start": 0, "end": 20},
                    )
                    count = _count(found.data, "search_note_infos")
            except ImaError as exc:
                print(_line("search", exc.http_status, exc.code, 0))
                return 1
            print(_line("search", found.http_status, found.code, count))
            return 0
    except ImaError as exc:
        if exc.kind == "missing_key":
            print("ima_smoke missing IMA_CLIENT_ID or IMA_API_KEY", file=sys.stderr)
            return 2
        print(_line("list_kb", exc.http_status, exc.code, 0))
        print(_line("search", 0, None, 0))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
