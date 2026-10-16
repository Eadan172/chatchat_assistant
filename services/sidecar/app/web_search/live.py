"""Live web search. Serper, then SerpApi (Bing), then Tavily.

The M3 stub chain in ``provider.py`` is unchanged and still does not call out.
This module is used only by the ask pipeline.

Response fields below were NOT confirmed against a live call. Parsers accept
several keys so a renamed field still maps:

* Serper: ``organic[].title`` / ``link`` / ``snippet`` / ``position``
  (also ``organic_results``, ``url``, ``content``)
* SerpApi: ``organic_results[].title`` / ``link`` / ``snippet`` / ``position``,
  status at ``search_metadata.status`` (also ``organic``, ``url``, ``content``)
* Tavily: ``results[].title`` / ``url`` / ``content`` / ``score``
  (``url``, not ``link``; ``link`` is still accepted)

No key is logged. A missing key is a notice, not an exception.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

NO_WEB_KEY = "未配置联网搜索密钥。请填写 SERPER_API_KEY、SERPAPI_API_KEY 或 TAVILY_API_KEY 中的至少一个。"
NO_WEB_RESULT = "未检索到网络结果。"

_OK_STATUS = {"success", "ok", "succeeded"}


class ProviderFailed(Exception):
    def __init__(self, http_status: int) -> None:
        super().__init__("provider failed")
        self.http_status = http_status


@dataclass
class WebSearchResult:
    hits: list[dict[str, Any]] = field(default_factory=list)
    notice: str | None = None
    provider: str | None = None
    attempts: list[dict[str, Any]] = field(default_factory=list)


def search_web(query: str, *, limit: int = 5) -> WebSearchResult:
    from app.ask.redact import install_redaction
    from app.ask.secrets import secret_value

    install_redaction()
    text = (query or "").strip()
    if not text:
        return WebSearchResult(notice=NO_WEB_RESULT)
    bounded = max(1, min(int(limit or 5), 10))
    providers = [item for item in _providers() if secret_value(item.key_name)]
    if not providers:
        return WebSearchResult(notice=NO_WEB_KEY)
    attempts: list[dict[str, Any]] = []
    for provider in providers:
        try:
            hits, status = provider.fetch(text, limit=bounded)
        except ProviderFailed as exc:
            attempts.append({"provider": provider.id, "http_status": exc.http_status, "count": 0})
            logger.info("web %s http=%s count=0", provider.id, exc.http_status)
            continue
        except Exception:
            attempts.append({"provider": provider.id, "http_status": 0, "count": 0})
            logger.info("web %s unreachable", provider.id)
            continue
        attempts.append({"provider": provider.id, "http_status": status, "count": len(hits)})
        logger.info("web %s http=%s count=%s host=%s", provider.id, status, len(hits), provider.host)
        if hits:
            return WebSearchResult(hits=hits, provider=provider.id, attempts=attempts)
    return WebSearchResult(notice=_failure_notice(attempts, providers), attempts=attempts)


_NAMES = {"serper": "Serper", "serpapi": "SerpApi", "tavily": "Tavily"}


def _failure_notice(attempts: list[dict[str, Any]], providers: list[Any]) -> str:
    failed = [item for item in attempts if item.get("http_status") != 200]
    if not failed:
        return NO_WEB_RESULT
    parts = "、".join(
        f"{_NAMES.get(item['provider'], item['provider'])}（HTTP {item['http_status'] or '无响应'}）" for item in failed
    )
    configured = {item.id for item in providers}
    missing = [_NAMES[name] for name in ("serper", "serpapi", "tavily") if name not in configured]
    text = f"联网搜索失败：{parts}，请检查密钥。"
    if missing:
        text += f"未配置后备来源 {'、'.join(missing)}，本次没有网页结果。"
    return text


def parse_hits(payload: dict[str, Any], *, list_keys: tuple[str, ...], link_keys: tuple[str, ...], provider: str) -> list[dict[str, Any]]:
    rows = _first_list(payload, list_keys)
    hits: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        title = _first_str(row, "title", "name")
        url = _first_str(row, *link_keys)
        snippet = _first_str(row, "snippet", "content", "highlight_content", "description", "body")
        if not title and not url and not snippet:
            continue
        hits.append(
            {
                "title": title or url or "未命名网页",
                "url": url,
                "text": snippet,
                "position": _first_int(row, "position", "rank", "index"),
                "score": _first_float(row, "score", "relevance_score"),
                "provider": provider,
                "source": "web",
                "source_kind": "web",
                "platform": "web",
                "freshness_state": "unknown",
            }
        )
    return hits


def status_ok(payload: dict[str, Any]) -> bool:
    status = provider_status(payload)
    if status is None:
        return True
    return status.strip().lower() in _OK_STATUS


def provider_status(payload: dict[str, Any]) -> str | None:
    meta = payload.get("search_metadata")
    if isinstance(meta, dict):
        for key in ("status", "search_status"):
            value = meta.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
    value = payload.get("status")
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


class _HttpProvider:
    id = ""
    key_name = ""
    host = ""

    def fetch(self, query: str, *, limit: int) -> tuple[list[dict[str, Any]], int]:
        raise NotImplementedError


class SerperProvider(_HttpProvider):
    id = "serper"
    key_name = "serper"
    host = "google.serper.dev"

    def fetch(self, query: str, *, limit: int) -> tuple[list[dict[str, Any]], int]:
        from app.ask.secrets import secret_value

        key = secret_value("serper") or ""
        url = _base("SERPER_BASE_URL", "https://google.serper.dev") + "/search"
        status, payload = _post_json(url, {"q": query, "num": limit}, headers={"X-API-KEY": key})
        hits = parse_hits(payload, list_keys=("organic", "organic_results", "results"), link_keys=("link", "url", "href"), provider=self.id)
        return hits[:limit], status


class SerpApiProvider(_HttpProvider):
    id = "serpapi"
    key_name = "serpapi"
    host = "serpapi.com"

    def fetch(self, query: str, *, limit: int) -> tuple[list[dict[str, Any]], int]:
        from app.ask.secrets import secret_value

        key = secret_value("serpapi") or ""
        url = _base("SERPAPI_BASE_URL", "https://serpapi.com") + "/search.json"
        status, payload = _get_json(
            url,
            {"engine": "bing", "q": query, "num": str(limit), "api_key": key},
        )
        if not status_ok(payload):
            raise ProviderFailed(status)
        hits = parse_hits(
            payload,
            list_keys=("organic_results", "organic", "results"),
            link_keys=("link", "url", "href"),
            provider=self.id,
        )
        return hits[:limit], status


class TavilyProvider(_HttpProvider):
    id = "tavily"
    key_name = "tavily"
    host = "api.tavily.com"

    def fetch(self, query: str, *, limit: int) -> tuple[list[dict[str, Any]], int]:
        from app.ask.secrets import secret_value

        key = secret_value("tavily") or ""
        url = _base("TAVILY_BASE_URL", "https://api.tavily.com") + "/search"
        status, payload = _post_json(url, {"api_key": key, "query": query, "max_results": limit})
        hits = parse_hits(payload, list_keys=("results", "organic", "organic_results"), link_keys=("url", "link", "href"), provider=self.id)
        return hits[:limit], status


def _providers() -> list[_HttpProvider]:
    return [SerperProvider(), SerpApiProvider(), TavilyProvider()]


def _base(env_name: str, default: str) -> str:
    raw = (os.getenv(env_name) or default).strip().rstrip("/")
    if not raw.startswith("http://") and not raw.startswith("https://"):
        return default.rstrip("/")
    return raw


def _post_json(url: str, body: dict[str, Any], *, headers: dict[str, str] | None = None) -> tuple[int, dict[str, Any]]:
    import httpx

    request_headers = {"Content-Type": "application/json", "Accept": "application/json"}
    if headers:
        request_headers.update(headers)
    try:
        with httpx.Client(timeout=_timeout(), follow_redirects=False) as client:
            response = client.post(url, json=body, headers=request_headers)
    except httpx.HTTPError as exc:
        raise ProviderFailed(0) from exc
    return _payload(response)


def _get_json(url: str, params: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    import httpx

    try:
        with httpx.Client(timeout=_timeout(), follow_redirects=False) as client:
            response = client.get(url, params=params, headers={"Accept": "application/json"})
    except httpx.HTTPError as exc:
        raise ProviderFailed(0) from exc
    return _payload(response)


def _payload(response) -> tuple[int, dict[str, Any]]:
    if response.status_code >= 400:
        raise ProviderFailed(response.status_code)
    try:
        payload = response.json()
    except Exception as exc:
        raise ProviderFailed(response.status_code) from exc
    if not isinstance(payload, dict):
        raise ProviderFailed(response.status_code)
    return response.status_code, payload


def _timeout() -> float:
    raw = (os.getenv("WEB_SEARCH_TIMEOUT_S") or "20").strip()
    try:
        value = float(raw)
    except ValueError:
        return 20.0
    return min(max(value, 1.0), 60.0)


def _first_list(payload: dict[str, Any], keys: tuple[str, ...]) -> list[Any]:
    for key in keys:
        if key in payload and isinstance(payload.get(key), list):
            return list(payload[key])
    return []


def _first_str(row: dict[str, Any], *keys: str) -> str:
    for key in keys:
        value = row.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _first_int(row: dict[str, Any], *keys: str) -> int | None:
    for key in keys:
        value = row.get(key)
        if isinstance(value, bool) or value is None:
            continue
        if isinstance(value, int):
            return value
        if isinstance(value, str) and value.strip().lstrip("-").isdigit():
            return int(value.strip())
    return None


def _first_float(row: dict[str, Any], *keys: str) -> float | None:
    for key in keys:
        value = row.get(key)
        if isinstance(value, bool) or value is None:
            continue
        if isinstance(value, (int, float)):
            return float(value)
        if isinstance(value, str):
            try:
                return float(value.strip())
            except ValueError:
                continue
    return None
