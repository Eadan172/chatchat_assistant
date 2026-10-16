#!/usr/bin/env python3
"""One live request each for Serper, SerpApi, Tavily, and DeepSeek.

Prints HTTP status, business code, result count, and top-level JSON field
names. Does not print response bodies, snippets, URLs, or keys.

    python scripts/m6_smoke.py

Keys come from the environment (DEEPSEEK_API_KEY, SERPER_API_KEY,
SERPAPI_API_KEY, TAVILY_API_KEY) or from $DATA_DIR/secrets/ask.json.
A missing key prints ``<name> skipped no_key`` and is not called.
Exit 2 when none of the four keys are set, 1 when a configured call fails,
0 otherwise.

Optional: ASK_SMOKE_QUERY (default 示例概念).
"""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SIDECAR = ROOT / "services" / "sidecar"
if str(SIDECAR) not in sys.path:
    sys.path.insert(0, str(SIDECAR))

from app.ask.redact import install_redaction  # noqa: E402
from app.ask.secrets import secret_value  # noqa: E402


def _line(name: str, http_status: int, code: str, count: int, keys: list[str]) -> str:
    return f"{name} http={http_status} code={code} count={count} keys={','.join(keys)}"


def _top_keys(payload: dict) -> list[str]:
    return sorted(str(key) for key in payload.keys())


def _list_len(payload: dict, *names: str) -> int:
    for name in names:
        value = payload.get(name)
        if isinstance(value, list):
            return len(value)
    return 0


def _business_code(name: str, payload: dict) -> str:
    if name == "serpapi":
        meta = payload.get("search_metadata")
        if isinstance(meta, dict):
            status = meta.get("status")
            if isinstance(status, str):
                return status
        return ""
    if name == "deepseek":
        error = payload.get("error")
        if isinstance(error, dict):
            code = error.get("code")
            if isinstance(code, str):
                return code
        return ""
    return ""


def _count(name: str, payload: dict) -> int:
    if name == "deepseek":
        return _list_len(payload, "choices")
    if name == "serper":
        return _list_len(payload, "organic", "organic_results")
    if name == "serpapi":
        return _list_len(payload, "organic_results", "organic")
    if name == "tavily":
        return _list_len(payload, "results")
    return 0


def _request(method: str, url: str, *, json_body: dict | None = None, params: dict | None = None, headers: dict | None = None) -> tuple[int, dict]:
    import httpx

    request_headers = {"Accept": "application/json"}
    if headers:
        request_headers.update(headers)
    try:
        with httpx.Client(timeout=30.0, follow_redirects=False) as client:
            response = client.request(method, url, json=json_body, params=params, headers=request_headers)
    except httpx.HTTPError:
        return 0, {}
    try:
        payload = response.json()
    except Exception:
        return response.status_code, {}
    if not isinstance(payload, dict):
        return response.status_code, {}
    return response.status_code, payload


def _call(name: str, query: str) -> tuple[bool, str]:
    if name == "serper":
        key = secret_value("serper")
        if not key:
            return True, "serper skipped no_key"
        base = (os.getenv("SERPER_BASE_URL") or "https://google.serper.dev").strip().rstrip("/")
        status, payload = _request(
            "POST",
            f"{base}/search",
            json_body={"q": query, "num": 3},
            headers={"X-API-KEY": key, "Content-Type": "application/json"},
        )
    elif name == "serpapi":
        key = secret_value("serpapi")
        if not key:
            return True, "serpapi skipped no_key"
        base = (os.getenv("SERPAPI_BASE_URL") or "https://serpapi.com").strip().rstrip("/")
        status, payload = _request(
            "GET",
            f"{base}/search.json",
            params={"engine": "bing", "q": query, "num": "3", "api_key": key},
        )
    elif name == "tavily":
        key = secret_value("tavily")
        if not key:
            return True, "tavily skipped no_key"
        base = (os.getenv("TAVILY_BASE_URL") or "https://api.tavily.com").strip().rstrip("/")
        status, payload = _request(
            "POST",
            f"{base}/search",
            json_body={"api_key": key, "query": query, "max_results": 3},
            headers={"Content-Type": "application/json"},
        )
    else:
        key = secret_value("deepseek")
        if not key:
            return True, "deepseek skipped no_key"
        base = (os.getenv("DEEPSEEK_BASE_URL") or "https://api.deepseek.com").strip().rstrip("/")
        if base.endswith("/v1"):
            url = f"{base}/chat/completions"
        elif base.endswith("/chat/completions"):
            url = base
        else:
            url = f"{base}/chat/completions"
        model = (os.getenv("DEEPSEEK_MODEL") or "deepseek-chat").strip() or "deepseek-chat"
        status, payload = _request(
            "POST",
            url,
            json_body={
                "model": model,
                "temperature": 0,
                "messages": [{"role": "user", "content": "只回复一个字：好"}],
            },
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        )
    ok = 200 <= status < 300 and bool(payload)
    if name == "serpapi" and ok:
        code = _business_code(name, payload).strip().lower()
        if code and code not in {"success", "ok", "succeeded"}:
            ok = False
    line = _line(name, status, _business_code(name, payload), _count(name, payload) if payload else 0, _top_keys(payload))
    return ok, line


def main() -> int:
    install_redaction()
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    query = (os.getenv("ASK_SMOKE_QUERY") or "示例概念").strip() or "示例概念"
    names = ("serper", "serpapi", "tavily", "deepseek")
    configured = [name for name in names if secret_value(name)]
    failed = False
    for name in names:
        ok, line = _call(name, query)
        print(line)
        if name in configured and not ok:
            failed = True
    if not configured:
        return 2
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
