"""Read ordinary web pages for the top search hits.

The ask pipeline passes the readable text to DeepSeek. A failed fetch keeps
the search snippet. This module does not search WeChat articles by title:
a hit with no URL, including ima ``media_type`` 6, is left unchanged.
"""

from __future__ import annotations

import logging
import os
import re
from html import unescape
from typing import Any, Callable
from urllib.parse import urlsplit

logger = logging.getLogger(__name__)

Fetch = Callable[[str], str]

_COMMENT = re.compile(r"(?is)<!--.*?-->")
_BLOCK = re.compile(r"(?is)<(script|style|noscript|template|svg|head)\b[^>]*>.*?</\1>")
_BREAK = re.compile(r"(?i)<br\s*/?>|</p>|</div>|</h[1-6]>|</li>|</tr>")
_TAG = re.compile(r"(?is)<[^>]+>")
_SPACE = re.compile(r"[ \t\r\f\v]+")
_BLANK = re.compile(r"\n{3,}")
_CHARSET = re.compile(r"charset=([\w.-]+)", re.I)
_BINARY = ("image/", "audio/", "video/", "pdf", "octet-stream", "zip", "gzip")


def fetch_limit() -> int:
    return _bounded_int("ASK_WEB_FETCH_N", default=4, low=0, high=8)


def enrich_web_hits(
    hits: list[dict[str, Any]],
    *,
    fetch: Fetch | None = None,
    limit: int | None = None,
) -> list[dict[str, Any]]:
    """Replace the snippet on the first ``limit`` http(s) hits when the page has text."""
    bounded = fetch_limit() if limit is None else max(0, min(int(limit), 8))
    fetcher = fetch or fetch_page_text
    updated: list[dict[str, Any]] = []
    used = 0
    for hit in hits:
        copied = dict(hit)
        url = str(copied.get("url") or "").strip()
        if used < bounded and url.startswith(("http://", "https://")):
            used += 1
            body = _read(url, fetcher)
            if body:
                copied["text"] = body
                copied["page_text"] = True
        updated.append(copied)
    return updated


def fetch_page_text(url: str) -> str:
    """Return readable text, or an empty string when the page cannot be read."""
    from app.ask.redact import install_redaction

    install_redaction()
    target = (url or "").strip()
    parts = urlsplit(target)
    if parts.scheme not in {"http", "https"} or not parts.hostname:
        return ""
    import httpx

    try:
        with httpx.Client(timeout=_timeout(), follow_redirects=True, max_redirects=5) as client:
            response = client.get(
                target,
                headers={"User-Agent": "ZhihuConnect/0.1", "Accept": "text/html,text/plain;q=0.9,*/*;q=0.1"},
            )
    except httpx.HTTPError:
        logger.info("web fetch failed host=%s", parts.hostname)
        return ""
    if response.status_code >= 400:
        logger.info("web fetch host=%s http=%s chars=0", parts.hostname, response.status_code)
        return ""
    content_type = (response.headers.get("content-type") or "").lower()
    if any(token in content_type for token in _BINARY):
        logger.info("web fetch host=%s http=%s chars=0", parts.hostname, response.status_code)
        return ""
    raw = response.content[:1_000_000]
    text = _to_text(_decode(raw, content_type), content_type)
    logger.info("web fetch host=%s http=%s chars=%s", parts.hostname, response.status_code, len(text))
    return text


def _read(url: str, fetch: Fetch) -> str:
    host = urlsplit(url).hostname or ""
    try:
        body = fetch(url) or ""
    except Exception:
        logger.info("web fetch failed host=%s", host)
        return ""
    compact = re.sub(r"\s+", " ", body).strip()
    if len(compact) < 40:
        return ""
    return compact[:_max_chars()]


def _to_text(raw: str, content_type: str) -> str:
    sample = raw[:2000].lower()
    if "html" in content_type or "<html" in sample or "<body" in sample or "<p" in sample:
        return _html_to_text(raw)
    compact = re.sub(r"\s+", " ", raw).strip()
    return compact[:_max_chars()]


def _html_to_text(raw: str) -> str:
    cleaned = _COMMENT.sub(" ", raw)
    cleaned = _BLOCK.sub(" ", cleaned)
    cleaned = _BREAK.sub("\n", cleaned)
    cleaned = _TAG.sub(" ", cleaned)
    cleaned = unescape(cleaned)
    cleaned = _SPACE.sub(" ", cleaned)
    lines = [line.strip() for line in cleaned.splitlines()]
    text = "\n".join(line for line in lines if line)
    text = _BLANK.sub("\n\n", text).strip()
    return text[:_max_chars()]


def _decode(raw: bytes, content_type: str) -> str:
    match = _CHARSET.search(content_type or "")
    charset = match.group(1) if match else "utf-8"
    try:
        return raw.decode(charset, errors="replace")
    except LookupError:
        return raw.decode("utf-8", errors="replace")


def _timeout() -> float:
    raw = (os.getenv("ASK_WEB_FETCH_TIMEOUT_S") or "8").strip()
    try:
        value = float(raw)
    except ValueError:
        return 8.0
    return min(max(value, 1.0), 20.0)


def _max_chars() -> int:
    return _bounded_int("ASK_WEB_FETCH_MAX_CHARS", default=4000, low=200, high=8000)


def _bounded_int(name: str, *, default: int, low: int, high: int) -> int:
    raw = (os.getenv(name) or str(default)).strip()
    try:
        value = int(raw)
    except ValueError:
        return default
    return min(max(value, low), high)
