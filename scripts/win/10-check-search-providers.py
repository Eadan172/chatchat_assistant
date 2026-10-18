"""Check the web-search providers (SearXNG / Tavily / chain).

Usage:
  python scripts/win/10-check-search-providers.py           # local checks, no paid calls
  python scripts/win/10-check-search-providers.py --live     # also call Tavily once
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = Path(os.getenv("DATA_DIR") or (ROOT / ".data"))


def load_env() -> None:
    path = ROOT / ".env"
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        text = line.strip()
        if not text or text.startswith("#") or "=" not in text:
            continue
        name, value = text.split("=", 1)
        os.environ.setdefault(name.strip(), value.strip())


load_env()
# The app resolves secrets relative to DATA_DIR; keep the script consistent with it.
os.environ.setdefault("DATA_DIR", str(DATA_DIR))
sys.path.insert(0, str(ROOT / "services" / "sidecar"))

from app.web_search import live  # noqa: E402


def tavily_key() -> tuple[str, str]:
    env_key = (os.getenv("TAVILY_API_KEY") or "").strip()
    if env_key:
        return env_key, "env"
    path = DATA_DIR / "secrets" / "ask.json"
    if path.is_file():
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            payload = {}
        value = str(payload.get("tavily_api_key") or "").strip()
        if value:
            return value, str(path)
    return "", "missing"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--live", action="store_true", help="also send one real Tavily request")
    args = parser.parse_args()

    base = os.getenv("SEARXNG_BASE_URL") or "http://127.0.0.1:8080"
    print(f"searxng url : {base}")
    try:
        with httpx.Client(timeout=30, trust_env=False) as client:
            response = client.get(f"{base}/search", params={"q": "Dify 是什么", "format": "json"})
        ok = response.status_code == 200 and (response.text or "").lstrip().startswith("{")
        count = len(response.json().get("results") or []) if ok else 0
        print(f"searxng json: HTTP {response.status_code} results={count}")
    except Exception as exc:
        print(f"searxng json: error={type(exc).__name__}: {exc}")

    key, source = tavily_key()
    print(f"tavily key  : {source}" + (f" ({key[:5]}… len={len(key)})" if key else ""))

    if args.live and key:
        try:
            hits, status = live.TavilyProvider().fetch("Dify 是什么", limit=3)
            print(f"tavily call : HTTP {status} hits={len(hits)}")
            for hit in hits[:2]:
                print("  -", hit["title"][:40], "|", hit["url"][:55])
        except live.ProviderFailed as exc:
            print(f"tavily call : HTTP {exc.http_status} detail={exc.detail[:160]}")
        except Exception as exc:
            print(f"tavily call : error={type(exc).__name__}: {exc}")

    result = live.search_web("Dify 是什么", limit=5)
    print(f"chain       : provider={result.provider} hits={len(result.hits)} attempts={result.attempts}")
    if result.notice:
        print(f"notice      : {result.notice}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
