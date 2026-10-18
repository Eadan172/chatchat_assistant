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
MAX_WEB_RESULTS = 20

_OK_STATUS = {"success", "ok", "succeeded"}


class ProviderFailed(Exception):
    def __init__(self, http_status: int, detail: str = "") -> None:
        super().__init__("provider failed")
        self.http_status = http_status
        self.detail = (detail or "").strip()[:300]


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
    bounded = max(1, min(int(limit or 5), MAX_WEB_RESULTS))
    keyless_on = (os.getenv("WEB_SEARCH_KEYLESS") or "").strip().lower() in ("1", "true", "yes", "on")
    providers = [
        item
        for item in _providers()
        if (item.key_name and secret_value(item.key_name)) or (not item.key_name and keyless_on)
    ]
    if (os.getenv("WEB_SEARCH_KEYLESS_FIRST") or "").strip() in ("1", "true", "yes"):
        providers.sort(key=lambda item: 0 if not item.key_name else 1)
    if not providers:
        return WebSearchResult(notice=NO_WEB_KEY)
    attempts: list[dict[str, Any]] = []
    for provider in providers:
        try:
            hits, status = provider.fetch(text, limit=bounded)
        except ProviderFailed as exc:
            attempts.append(
                {
                    "provider": provider.id,
                    "http_status": exc.http_status,
                    "count": 0,
                    "detail": exc.detail,
                }
            )
            logger.info("web %s http=%s count=0 detail=%s", provider.id, exc.http_status, exc.detail[:120])
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


_NAMES = {
    "serper": "Serper",
    "serpapi": "SerpApi",
    "tavily": "Tavily",
    "searxng": "SearXNG",
    "so360": "360 搜索",
    "bing": "Bing 网页",
}

_STATUS_HINT = {
    400: "请求被拒绝：常见原因是搜索额度用尽或查询参数不合法",
    401: "密钥无效或已失效",
    403: "密钥无权限、被封禁，或额度已用完",
    429: "请求过于频繁，被限流",
    0: "无法连接（网络或代理问题）",
}


def _failure_notice(attempts: list[dict[str, Any]], providers: list[Any]) -> str:
    failed = [item for item in attempts if item.get("http_status") != 200]
    if not failed:
        return NO_WEB_RESULT
    parts = "、".join(
        f"{_NAMES.get(item['provider'], item['provider'])}（HTTP {item['http_status'] or '无响应'}）" for item in failed
    )
    configured = {item.id for item in providers}
    missing = [_NAMES[name] for name in ("serper", "serpapi", "tavily") if name not in configured]
    hints = []
    for item in failed:
        hint = _STATUS_HINT.get(int(item.get("http_status") or 0))
        if hint and hint not in hints:
            hints.append(hint)
    text = f"联网搜索失败：{parts}。"
    if hints:
        text += "；".join(hints) + "。"
    detail = next((str(item.get("detail") or "") for item in failed if item.get("detail")), "")
    if detail:
        text += f"服务返回：{detail}"
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


class SearxngProvider(_HttpProvider):
    """Self-hosted SearXNG (Docker). No API key; JSON API preferred.

    Start it with:
      docker run -d --name chatchat-searxng -p 127.0.0.1:8080:8080 \
        -v <repo>/.data/searxng:/etc/searxng --restart unless-stopped searxng/searxng
    and set SEARXNG_BASE_URL (default http://127.0.0.1:8080).
    """

    id = "searxng"
    key_name = ""
    host = "127.0.0.1:8080"

    def fetch(self, query: str, *, limit: int) -> tuple[list[dict[str, Any]], int]:
        base = _base("SEARXNG_BASE_URL", "http://127.0.0.1:8080")
        self.host = base.split("://", 1)[-1]
        params = {
            "q": query,
            "format": "json",
            "language": "zh-CN",
            "safesearch": "0",
            "categories": "general",
        }
        try:
            status, payload = _get_json(base + "/search", params)
        except ProviderFailed as exc:
            # Public instances often disable the JSON API; fall back to the HTML page.
            if exc.http_status in (400, 403, 404):
                status, body = _get_text(base + "/search", {"q": query}, headers={"User-Agent": _USER_AGENT})
                return _filter_relevant(parse_searxng_html(body), query)[:limit], status
            raise
        hits = parse_hits(
            payload,
            list_keys=("results",),
            link_keys=("url", "link"),
            provider=self.id,
        )
        hits = _filter_relevant(hits, query)
        return hits[:limit], status


def parse_searxng_html(body: str, *, provider: str = "searxng") -> list[dict[str, Any]]:
    from lxml import html as lxml_html

    if not body or "result" not in body:
        return []
    try:
        doc = lxml_html.fromstring(body)
    except Exception:
        return []
    hits: list[dict[str, Any]] = []
    for position, node in enumerate(doc.cssselect("article.result"), start=1):
        links = node.cssselect("h3 a")
        if not links:
            continue
        link = links[0]
        href = (link.get("href") or "").strip()
        title = " ".join("".join(link.itertext()).split())
        snippet = " ".join(
            " ".join("".join(part.itertext()).split()) for part in node.cssselect("p.content")
        ).strip()
        if not title or not href.startswith("http"):
            continue
        hits.append(
            {
                "title": title,
                "url": href,
                "text": snippet,
                "position": position,
                "provider": provider,
                "source": "web",
                "source_kind": "web",
                "platform": "web",
                "freshness_state": "unknown",
            }
        )
    return hits


class So360Provider(_HttpProvider):
    """Key-free fallback: parse so.com (360 搜索) result page."""

    id = "so360"
    key_name = ""
    host = "www.so.com"

    def fetch(self, query: str, *, limit: int) -> tuple[list[dict[str, Any]], int]:
        url = _base("SO360_SEARCH_URL", "https://www.so.com") + "/s"
        status, body = _get_text(
            url,
            {"q": query},
            headers={
                "User-Agent": _USER_AGENT,
                "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
                "Accept": "text/html,application/xhtml+xml",
            },
        )
        hits = _filter_relevant(parse_360(body), query)
        return hits[:limit], status


def parse_360(body: str, *, provider: str = "so360") -> list[dict[str, Any]]:
    from urllib.parse import urlparse

    from lxml import html as lxml_html

    if not body or "res-list" not in body:
        return []
    try:
        doc = lxml_html.fromstring(body)
    except Exception:
        return []
    hits: list[dict[str, Any]] = []
    for position, node in enumerate(doc.cssselect("li.res-list"), start=1):
        links = node.cssselect("h3 a")
        if not links:
            continue
        link = links[0]
        title = " ".join("".join(link.itertext()).split())
        href = (link.get("href") or "").strip()
        # Drop 360's own widgets (translation, AI box); keep /link redirects.
        parsed = urlparse(href)
        if parsed.netloc.lower().endswith("so.com") and not parsed.path.startswith("/link"):
            continue
        snippet = " ".join(
            " ".join("".join(part.itertext()).split())
            for part in node.cssselect("p.res-desc, div.res-comm-con, p")
        ).strip()
        if not title or not href.startswith("http"):
            continue
        hits.append(
            {
                "title": title,
                "url": href,
                "text": snippet,
                "position": position,
                "provider": provider,
                "source": "web",
                "source_kind": "web",
                "platform": "web",
                "freshness_state": "unknown",
            }
        )
    return hits


def _filter_relevant(hits: list[dict[str, Any]], query: str) -> list[dict[str, Any]]:
    """Reject decoy pages: keep hits sharing at least one query token."""
    from app.index.tokens import tokens

    wanted = {token.lower() for token in tokens(query) if len(token) >= 2}
    if not wanted:
        return hits
    kept = [
        hit
        for hit in hits
        if any(token in f"{hit.get('title', '')} {hit.get('text', '')}".lower() for token in wanted)
    ]
    if not kept:
        return []
    # A page with many results but almost none on-topic is usually a block/decoy page.
    if len(kept) < 2 and len(hits) >= 3:
        return []
    return kept


class BingHtmlProvider(_HttpProvider):
    """Key-free fallback: parse the public Bing results page.

    Uses cn.bing.com, which serves direct result links. No API key, no quota,
    but it is page parsing: Bing may rate-limit or change markup. Keep the
    request volume low (see WEB_SEARCH_MAX_QUERIES).
    """

    id = "bing"
    key_name = ""
    host = "cn.bing.com"

    def fetch(self, query: str, *, limit: int) -> tuple[list[dict[str, Any]], int]:
        url = _base("BING_SEARCH_URL", "https://cn.bing.com") + "/search"
        status, body = _get_text(
            url,
            {"q": query, "count": str(max(1, min(limit, 20))), "setlang": "zh-Hans", "ensearch": "0"},
            headers={
                "User-Agent": _USER_AGENT,
                "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
                "Accept": "text/html,application/xhtml+xml",
            },
        )
        hits = _filter_relevant(parse_bing(body, provider=self.id), query)
        return hits[:limit], status


def parse_bing(body: str, *, provider: str = "bing") -> list[dict[str, Any]]:
    from lxml import html as lxml_html

    if not body or "<li" not in body:
        return []
    try:
        doc = lxml_html.fromstring(body)
    except Exception:
        return []
    hits: list[dict[str, Any]] = []
    for position, node in enumerate(doc.cssselect("li.b_algo"), start=1):
        links = node.cssselect("h2 a")
        if not links:
            continue
        link = links[0]
        href = _unwrap_bing(link.get("href") or "")
        title = " ".join("".join(link.itertext()).split())
        snippet = " ".join(
            " ".join("".join(part.itertext()).split()) for part in node.cssselect(".b_caption p, .b_snippet, p")
        ).strip()
        if not href.startswith("http") or not title:
            continue
        hits.append(
            {
                "title": title,
                "url": href,
                "text": snippet,
                "position": position,
                "provider": provider,
                "source": "web",
                "source_kind": "web",
                "platform": "web",
                "freshness_state": "unknown",
            }
        )
    return hits


def _unwrap_bing(url: str) -> str:
    """cn.bing.com links are direct; www.bing.com wraps them in /ck/a?u=a1<base64>."""
    if "bing.com/ck/a" not in url:
        return url
    from urllib.parse import parse_qs, unquote, urlparse
    import base64

    value = (parse_qs(urlparse(url).query).get("u") or [""])[0]
    if not value.startswith("a1"):
        return url
    raw = value[2:]
    raw += "=" * (-len(raw) % 4)
    try:
        return unquote(base64.urlsafe_b64decode(raw).decode("utf-8", errors="replace"))
    except Exception:
        return url


_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/130.0 Safari/537.36"
)


def _get_text(url: str, params: dict[str, Any], *, headers: dict[str, str] | None = None) -> tuple[int, str]:
    import httpx

    try:
        with httpx.Client(timeout=_timeout(), follow_redirects=True) as client:
            response = client.get(url, params=params, headers=headers or {})
    except httpx.HTTPError as exc:
        raise ProviderFailed(0, str(exc)) from exc
    if response.status_code >= 400:
        raise ProviderFailed(response.status_code, (response.text or "")[:200])
    return response.status_code, response.text or ""


def _providers() -> list[_HttpProvider]:
    return [
        SerperProvider(),
        SerpApiProvider(),
        TavilyProvider(),
        SearxngProvider(),
        So360Provider(),
        BingHtmlProvider(),
    ]


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
        raise ProviderFailed(response.status_code, _error_detail(response))
    try:
        payload = response.json()
    except Exception as exc:
        raise ProviderFailed(response.status_code, _error_detail(response)) from exc
    if not isinstance(payload, dict):
        raise ProviderFailed(response.status_code, _error_detail(response))
    return response.status_code, payload


def _error_detail(response) -> str:
    """Provider error message (Serper: {"message": "Not enough credits"})."""
    text = ""
    try:
        text = (response.text or "").strip()
    except Exception:
        return ""
    try:
        data = response.json()
    except Exception:
        return text[:300].replace("\n", " ")
    if isinstance(data, dict):
        for key in ("message", "error", "detail"):
            value = data.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()[:300]
            if isinstance(value, dict) and isinstance(value.get("message"), str):
                return value["message"].strip()[:300]
    return text[:300].replace("\n", " ")


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
