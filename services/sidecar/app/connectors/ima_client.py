"""Official Tencent ima OpenAPI client.

All endpoint paths and auth headers live in this module so a real-key test can
correct them without hunting through the importer.

Confirmed from official sources
-------------------------------
* https://ima.qq.com/agent-interface (fetched 2026-10-16) links the skill
  package https://app-dl.ima.qq.com/skills/ima-skills-1.1.10.zip and tells the
  user to copy Client ID + API Key from that page. No cookie login.
* QClaw product doc https://qclaw.qq.com/docs/206424375046045696.html (updated
  2026-07-01): the same page is where Client ID and API Key are issued.
* Skill package ``ima-skill/knowledge-base/references/api.md`` and
  ``ima-skill/notes/references/api.md`` (version 1.1.10 inside that zip):

  - Base URL ``https://ima.qq.com``
  - HTTP POST, JSON body, envelope ``{"code": 0, "msg": "...", "data": {...}}``
  - Required headers ``ima-openapi-clientid``, ``ima-openapi-apikey``,
    ``Content-Type: application/json``
  - Wiki paths under ``/openapi/wiki/v1/``: ``search_knowledge_base``,
    ``get_knowledge_base``, ``get_knowledge_list``, ``search_knowledge``,
    ``get_media_info``
  - Note paths under ``/openapi/note/v1/``: ``list_notebook``, ``list_note``,
    ``search_note``, ``get_doc_content``
  - ``search_knowledge_base`` with ``query: ""`` lists the user's knowledge bases
  - ``get_knowledge_list`` takes ``knowledge_base_id``, ``cursor``, ``limit``
    (1-50) and an optional ``folder_id``
  - ``search_knowledge`` takes ``query``, ``cursor``, ``knowledge_base_id``
  - ``get_doc_content`` takes ``note_id`` and ``target_content_format``
    (``0`` = plaintext)
  - ``list_notebook`` first cursor is ``"0"``; ``list_note`` first cursor is ``""``
  - Auth / rate business codes named in those docs: notes ``20004`` (apiKey
    鉴权失败), ``20002`` (限频); wiki ``110030`` (无权限), ``110021`` (频控)
  - The official helper ``ima_api.cjs`` also reads ``IMA_CLIENT_ID``,
    ``IMA_API_KEY``, ``IMA_OPENAPI_CLIENTID``, ``IMA_OPENAPI_APIKEY``, and
    ``IMA_BASE_URL`` (default ``https://ima.qq.com``).

Assumed (not stated as a number or schema in those docs)
--------------------------------------------------------
* No published quota. ``IMA_MIN_INTERVAL_S`` (default 0.2) and
  ``IMA_MAX_PER_MINUTE`` (default 30) are local guards. Wiki ``110021`` and
  notes ``20002`` are treated as rate limits.
* Successful list/search responses are cached for ``IMA_CACHE_TTL_S`` seconds
  (default 60). Content reads (``get_doc_content``, ``get_media_info``, and the
  file URL those return) are not cached, so verify can hash the live body.
* ``get_knowledge_list`` / ``search_knowledge`` mix files and folders but do
  not document a discriminator. An entry is treated as a folder when it has
  ``folder_id`` without ``media_id``, or ``file_number`` / ``folder_number``,
  or ``is_folder`` true, or a title ending in ``/``. Otherwise ``media_id`` is
  an importable file. The skill text says a folder's id in search results is
  the returned ``media_id``; that case is only recognized by the hints above.
* HTTP status codes are not specified. Non-JSON or transport failures are
  "unreachable". HTTP 401/403 and business codes ``20004`` / ``110030`` (or a
  message that says 鉴权失败) are credential errors.
* The official helper sends ``ima-openapi-ctx: skill_version=...``. The API
  reference does not list that header as required, so this client does not
  send it and does not call ``openapi/check_skill_update``.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import threading
import time
from dataclasses import dataclass, field
from typing import Any

import httpx

logger = logging.getLogger(__name__)

DEFAULT_BASE_URL = "https://ima.qq.com"

# Confirmed paths. Keep them here.
PATH_SEARCH_KNOWLEDGE_BASE = "openapi/wiki/v1/search_knowledge_base"
PATH_GET_KNOWLEDGE_BASE = "openapi/wiki/v1/get_knowledge_base"
PATH_GET_KNOWLEDGE_LIST = "openapi/wiki/v1/get_knowledge_list"
PATH_SEARCH_KNOWLEDGE = "openapi/wiki/v1/search_knowledge"
PATH_GET_MEDIA_INFO = "openapi/wiki/v1/get_media_info"
PATH_LIST_NOTEBOOK = "openapi/note/v1/list_notebook"
PATH_LIST_NOTE = "openapi/note/v1/list_note"
PATH_SEARCH_NOTE = "openapi/note/v1/search_note"
PATH_GET_DOC_CONTENT = "openapi/note/v1/get_doc_content"

_CACHEABLE = {
    PATH_SEARCH_KNOWLEDGE_BASE,
    PATH_GET_KNOWLEDGE_BASE,
    PATH_GET_KNOWLEDGE_LIST,
    PATH_SEARCH_KNOWLEDGE,
    PATH_LIST_NOTEBOOK,
    PATH_LIST_NOTE,
    PATH_SEARCH_NOTE,
}

_AUTH_CODES = {20004, 110030}
_RATE_CODES = {20002, 110021}

MISSING_KEY_MESSAGE = (
    "未配置 ima 密钥。请填写 Client ID 和 API Key，或设置环境变量 IMA_CLIENT_ID 与 IMA_API_KEY。"
)
BAD_KEY_MESSAGE = (
    "ima 密钥无效或没有权限。请到 https://ima.qq.com/agent-interface 重新获取 Client ID 和 API Key。"
)
RATE_LIMIT_MESSAGE = "ima 请求过于频繁，请稍后再试。官方配额未公开，本地已限制调用频率。"
UNREACHABLE_MESSAGE = "无法连接 ima。请检查网络后重试。"

_LOCK = threading.Lock()
_CACHE: dict[str, tuple[float, "ImaResult"]] = {}
_CALLS: list[float] = []
_NEXT_OK = 0.0


class ImaError(Exception):
    def __init__(
        self,
        kind: str,
        message: str,
        *,
        http_status: int = 0,
        code: int | None = None,
    ) -> None:
        super().__init__(message)
        self.kind = kind
        self.message = message
        self.http_status = http_status
        self.code = code


@dataclass(slots=True)
class ImaResult:
    http_status: int
    code: int
    msg: str
    data: dict[str, Any] = field(default_factory=dict)
    path: str = ""


def reset_client_state() -> None:
    """Drop the in-process cache and rate-limit window. Tests use this."""
    global _NEXT_OK
    with _LOCK:
        _CACHE.clear()
        _CALLS.clear()
        _NEXT_OK = 0.0


def base_url() -> str:
    raw = (os.getenv("IMA_BASE_URL") or DEFAULT_BASE_URL).strip().rstrip("/")
    if not raw.startswith("http://") and not raw.startswith("https://"):
        return DEFAULT_BASE_URL
    return raw


def scrub(text: str, secrets: list[str]) -> str:
    cleaned = text or ""
    for secret in secrets:
        if secret and len(secret) >= 4 and secret in cleaned:
            cleaned = cleaned.replace(secret, "[redacted]")
    return cleaned


class ImaClient:
    def __init__(self, client_id: str, api_key: str, *, base: str | None = None, timeout: float = 20.0) -> None:
        self.client_id = client_id
        self.api_key = api_key
        self._base = (base or base_url()).rstrip("/")
        self._timeout = timeout
        self._http = httpx.Client(base_url=self._base, timeout=timeout, follow_redirects=False)

    def close(self) -> None:
        self._http.close()

    def headers(self) -> dict[str, str]:
        # Confirmed header names. Values are never logged.
        return {
            "ima-openapi-clientid": self.client_id,
            "ima-openapi-apikey": self.api_key,
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    def post(self, path: str, body: dict[str, Any], *, cache: bool | None = None) -> ImaResult:
        use_cache = path in _CACHEABLE if cache is None else cache
        cache_key = ""
        if use_cache:
            cache_key = self._cache_key(path, body)
            cached = _cache_get(cache_key)
            if cached is not None:
                return cached
        _acquire_slot()
        try:
            response = self._http.post(path, json=body, headers=self.headers())
        except httpx.HTTPError as exc:
            logger.info("ima %s unreachable (%s)", path, type(exc).__name__)
            raise ImaError("unreachable", UNREACHABLE_MESSAGE) from exc
        result = self._parse(path, response)
        logger.info("ima %s http=%s code=%s", path, result.http_status, result.code)
        if use_cache and result.code == 0:
            _cache_put(cache_key, result)
        return result

    def download(self, url: str, headers: dict[str, str] | None, *, limit: int) -> tuple[int, bytes, str]:
        """GET a file URL returned by get_media_info. Do not send the OpenAPI key."""
        _acquire_slot()
        safe_headers = {str(key): str(value) for key, value in (headers or {}).items() if key and value}
        # Never forward ima credentials or a browser cookie jar.
        for banned in ("ima-openapi-apikey", "ima-openapi-clientid", "cookie", "Cookie"):
            safe_headers.pop(banned, None)
        try:
            response = self._http.get(url, headers=safe_headers, follow_redirects=True, timeout=self._timeout)
        except httpx.HTTPError as exc:
            logger.info("ima file download unreachable (%s)", type(exc).__name__)
            raise ImaError("unreachable", UNREACHABLE_MESSAGE) from exc
        content_type = response.headers.get("content-type", "")
        if response.status_code >= 400:
            raise ImaError("unreachable", "无法从 ima 下载原文。", http_status=response.status_code)
        data = response.content
        if len(data) > limit:
            raise ImaError("api", "原文超过 8MB，未导入。", http_status=response.status_code)
        return response.status_code, data, content_type

    def _cache_key(self, path: str, body: dict[str, Any]) -> str:
        fingerprint = hashlib.sha256(f"{self.client_id}\n{self.api_key}".encode("utf-8")).hexdigest()[:16]
        encoded = json.dumps(body, sort_keys=True, ensure_ascii=False, default=str)
        return f"{self._base}\n{fingerprint}\n{path}\n{encoded}"

    def _parse(self, path: str, response: httpx.Response) -> ImaResult:
        text = response.text or ""
        try:
            payload = json.loads(text) if text else {}
        except json.JSONDecodeError:
            if response.status_code in {401, 403}:
                raise ImaError("bad_key", BAD_KEY_MESSAGE, http_status=response.status_code)
            raise ImaError("unreachable", UNREACHABLE_MESSAGE, http_status=response.status_code)
        if not isinstance(payload, dict):
            raise ImaError("unreachable", UNREACHABLE_MESSAGE, http_status=response.status_code)
        code = _as_int(payload.get("code"))
        msg = scrub(str(payload.get("msg") or ""), [self.client_id, self.api_key])
        data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
        if response.status_code in {401, 403} or _is_auth(code, msg):
            raise ImaError("bad_key", BAD_KEY_MESSAGE, http_status=response.status_code, code=code)
        if code in _RATE_CODES or "频控" in msg or "限频" in msg:
            raise ImaError("rate_limit", RATE_LIMIT_MESSAGE, http_status=response.status_code, code=code)
        if response.status_code >= 400 or code not in {0, None}:
            detail = msg or "ima 请求失败。"
            if not detail.startswith("ima"):
                detail = f"ima：{detail}"
            raise ImaError("api", detail, http_status=response.status_code, code=code if code is not None else None)
        if code is None:
            raise ImaError("unreachable", UNREACHABLE_MESSAGE, http_status=response.status_code)
        return ImaResult(http_status=response.status_code, code=code, msg=msg, data=data, path=path)


def _as_int(value: Any) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.strip().lstrip("-").isdigit():
        return int(value.strip())
    return None


def _is_auth(code: int | None, msg: str) -> bool:
    if code in _AUTH_CODES:
        return True
    return "鉴权失败" in msg or "鉴权不通过" in msg


def _min_interval() -> float:
    raw = (os.getenv("IMA_MIN_INTERVAL_S") or "0.2").strip()
    try:
        value = float(raw)
    except ValueError:
        return 0.2
    return max(0.0, value)


def _max_per_minute() -> int:
    raw = (os.getenv("IMA_MAX_PER_MINUTE") or "30").strip()
    try:
        value = int(raw)
    except ValueError:
        return 30
    return max(1, value)


def _cache_ttl() -> float:
    raw = (os.getenv("IMA_CACHE_TTL_S") or "60").strip()
    try:
        value = float(raw)
    except ValueError:
        return 60.0
    return max(0.0, value)


def _acquire_slot() -> None:
    global _NEXT_OK
    interval = _min_interval()
    budget = _max_per_minute()
    while True:
        with _LOCK:
            now = time.monotonic()
            fresh = [stamp for stamp in _CALLS if now - stamp < 60]
            _CALLS[:] = fresh
            if len(_CALLS) >= budget:
                raise ImaError("rate_limit", RATE_LIMIT_MESSAGE)
            delay = _NEXT_OK - now
            if delay <= 0:
                _CALLS.append(now)
                _NEXT_OK = now + interval
                return
        time.sleep(delay)


def _cache_get(key: str) -> ImaResult | None:
    ttl = _cache_ttl()
    if ttl <= 0:
        return None
    with _LOCK:
        found = _CACHE.get(key)
        if found is None:
            return None
        expires, result = found
        if expires < time.monotonic():
            _CACHE.pop(key, None)
            return None
        return result


def _cache_put(key: str, result: ImaResult) -> None:
    ttl = _cache_ttl()
    if ttl <= 0 or not key:
        return
    with _LOCK:
        _CACHE[key] = (time.monotonic() + ttl, result)
