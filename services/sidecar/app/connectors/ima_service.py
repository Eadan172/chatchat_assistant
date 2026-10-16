"""List, search, and import ima notes/files through the official client.

Imported rows go through the M3 ``import_payload`` path with platform ``ima``.
"""

from __future__ import annotations

import re
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Iterator

from sqlmodel import Session, select

from app.connectors.ima_client import (
    PATH_GET_DOC_CONTENT,
    PATH_GET_KNOWLEDGE_BASE,
    PATH_GET_KNOWLEDGE_LIST,
    PATH_GET_MEDIA_INFO,
    PATH_LIST_NOTE,
    PATH_LIST_NOTEBOOK,
    PATH_SEARCH_KNOWLEDGE,
    PATH_SEARCH_KNOWLEDGE_BASE,
    PATH_SEARCH_NOTE,
    ImaClient,
    ImaError,
)
from app.connectors.ima_secrets import load_credentials
from app.db.models import ConnectorAccount, utcnow
from app.ingest.local_file import ImportError, import_payload, safe_filename

_TAG = re.compile(r"(?is)<(script|style).*?>.*?</\1>")
_TAGS = re.compile(r"(?s)<[^>]+>")
_SPACE = re.compile(r"[ \t]+\n|\n{3,}")
_EM = re.compile(r"(?i)</?em>")


@contextmanager
def open_ima_client(*, env_only: bool = False) -> Iterator[ImaClient]:
    creds = load_credentials(env_only=env_only)
    if creds is None:
        from app.connectors.ima_client import MISSING_KEY_MESSAGE

        raise ImaError("missing_key", MISSING_KEY_MESSAGE)
    client = ImaClient(creds.client_id, creds.api_key)
    try:
        yield client
    finally:
        client.close()


def ensure_account(db: Session, display_name: str = "腾讯 ima") -> ConnectorAccount:
    row = db.exec(select(ConnectorAccount).where(ConnectorAccount.connector_type == "ima")).first()
    if row is None:
        row = ConnectorAccount(
            connector_type="ima",
            display_name=display_name or "腾讯 ima",
            auth_ref="local",
            config={},
            enabled=True,
        )
        db.add(row)
        db.commit()
        db.refresh(row)
        return row
    row.enabled = True
    row.auth_ref = "local"
    if display_name:
        row.display_name = display_name
    row.config = _public_config(row.config)
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def public_account(row: ConnectorAccount) -> dict[str, Any]:
    return {
        "id": row.id,
        "type": row.connector_type,
        "display_name": row.display_name,
        "enabled": bool(row.enabled),
        "auth_ref": row.auth_ref,
        "config": _public_config(row.config),
        "sync_cursor": row.sync_cursor,
        "last_sync_at": _iso(row.last_sync_at),
        "last_error": row.last_error,
    }


def knowledge_base_id_of(item: dict[str, Any]) -> str:
    """Live list items use ``kb_id``. ``id`` / ``knowledge_base_id`` are fallbacks."""
    return _text(item, "kb_id", "id", "knowledge_base_id")


def knowledge_base_name_of(item: dict[str, Any]) -> str:
    """Live list items use ``kb_name``. ``name`` / ``title`` are fallbacks."""
    return _text(item, "kb_name", "name", "title")


def list_overview(client: ImaClient) -> dict[str, Any]:
    listed = client.post(PATH_SEARCH_KNOWLEDGE_BASE, {"query": "", "cursor": "", "limit": 20})
    raw_bases = _named_list(listed.data, "info_list")
    ids = [knowledge_base_id_of(item) for item in raw_bases]
    ids = [kb_id for kb_id in ids if kb_id]
    details = _knowledge_base_details(client, ids)
    bases = []
    for item in raw_bases:
        kb_id = knowledge_base_id_of(item)
        if not kb_id:
            continue
        extra = details.get(kb_id) if isinstance(details.get(kb_id), dict) else {}
        bases.append(
            {
                "id": kb_id,
                "name": knowledge_base_name_of(item) or knowledge_base_name_of(extra) or "未命名知识库",
                "description": _text(item, "description", "desc") or _text(extra, "description", "desc"),
            }
        )
    notebooks: list[dict[str, Any]] = []
    notebook_error = None
    try:
        folders = client.post(PATH_LIST_NOTEBOOK, {"cursor": "0", "limit": 20})
        for item in _named_list(folders.data, "note_folder_infos", "folder_infos", "notebooks"):
            folder_id = _text(item, "folder_id", "notebook_id", "id")
            if not folder_id:
                continue
            note_count = item.get("note_number")
            if note_count is None:
                note_count = item.get("note_count")
            notebooks.append(
                {
                    "id": folder_id,
                    "name": _text(item, "name", "folder_name", "title") or "未命名笔记本",
                    "note_count": note_count or 0,
                    "folder_type": item.get("folder_type", item.get("type")),
                }
            )
    except ImaError as exc:
        notebook_error = exc.message
    return {
        "knowledge_bases": bases,
        "notebooks": notebooks,
        "notebook_error": notebook_error,
        "kb_count": len(bases),
        "notebook_count": len(notebooks),
    }


def list_children(
    client: ImaClient,
    *,
    knowledge_base_id: str | None = None,
    folder_id: str | None = None,
    notebook_id: str | None = None,
    cursor: str = "",
) -> dict[str, Any]:
    if notebook_id:
        result = client.post(
            PATH_LIST_NOTE,
            {"folder_id": notebook_id, "cursor": cursor or "", "limit": 20, "sort_type": 0},
        )
        items = []
        for item in _named_list(result.data, "note_book_list", "note_list", "notes"):
            parsed = _note_fields(item)
            if parsed is None:
                continue
            items.append(
                {
                    "kind": "note",
                    "id": parsed["id"],
                    "title": parsed["title"],
                    "summary": parsed["summary"],
                }
            )
        return {"folders": [], "items": items, "is_end": bool(result.data.get("is_end", True)), "next_cursor": ""}
    if not knowledge_base_id:
        raise ImaError("api", "请选择一个知识库或笔记本。")
    body: dict[str, Any] = {"knowledge_base_id": knowledge_base_id, "cursor": cursor or "", "limit": 20}
    if folder_id:
        body["folder_id"] = folder_id
    result = client.post(PATH_GET_KNOWLEDGE_LIST, body)
    folders, items = _split_knowledge(
        _named_list(result.data, "knowledge_list", "info_list"),
        knowledge_base_id,
    )
    return {
        "folders": folders,
        "items": items,
        "is_end": bool(result.data.get("is_end", True)),
        "next_cursor": str(result.data.get("next_cursor") or ""),
        "current_path": [
            {
                "id": _text(entry, "folder_id", "id"),
                "name": _text(entry, "name", "title", "folder_name"),
            }
            for entry in _named_list(result.data, "current_path")
        ],
    }


def find_knowledge_base(client: ImaClient, name: str) -> dict[str, str] | None:
    """Match ``kb_name`` to ``kb_id``. Listing with an empty query is the confirmed call."""
    wanted = name.strip()
    if not wanted:
        return None
    found: list[dict[str, str]] = []
    seen: set[str] = set()
    for query in (wanted, ""):
        listed = client.post(PATH_SEARCH_KNOWLEDGE_BASE, {"query": query, "cursor": "", "limit": 20})
        for item in _named_list(listed.data, "info_list"):
            kb_id = knowledge_base_id_of(item)
            kb_name = knowledge_base_name_of(item)
            if not kb_id or not kb_name or kb_id in seen:
                continue
            seen.add(kb_id)
            found.append({"id": kb_id, "name": kb_name})
    for base in found:
        if base["name"] == wanted:
            return base
    partial = [base for base in found if wanted in base["name"] or base["name"] in wanted]
    if len(partial) == 1:
        return partial[0]
    return None


def search(client: ImaClient, query: str, *, knowledge_base_id: str | None = None) -> dict[str, Any]:
    text = query.strip()
    if not text:
        raise ImaError("api", "请输入搜索关键词。")
    hits: list[dict[str, Any]] = []
    notes = client.post(
        PATH_SEARCH_NOTE,
        {"search_type": 1, "query_info": {"content": text}, "start": 0, "end": 20},
    )
    for entry in _named_list(notes.data, "search_note_infos"):
        parsed = _note_fields(entry)
        if parsed is None:
            continue
        hits.append(
            {
                "kind": "note",
                "id": parsed["id"],
                "title": parsed["title"],
                "snippet": parsed["snippet"],
            }
        )
    if knowledge_base_id:
        found = client.post(
            PATH_SEARCH_KNOWLEDGE,
            {"query": text, "cursor": "", "knowledge_base_id": knowledge_base_id},
        )
        # Live ``data`` has only ``info_list``. An empty list is a successful search.
        folders, items = _split_knowledge(_named_list(found.data, "info_list"), knowledge_base_id)
        for folder in folders:
            hits.append({**folder, "snippet": ""})
        for item in items:
            hits.append(item)
    return {"query": text, "hits": hits, "count": len(hits)}


def import_items(db: Session, client: ImaClient, items: list[dict[str, Any]], *, registry=None) -> dict[str, Any]:
    account = ensure_account(db)
    imported: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []
    for raw in items:
        kind = str(raw.get("kind") or "")
        source_id = str(raw.get("id") or "").strip()
        if kind not in {"note", "media"} or not source_id:
            errors.append({"id": source_id, "detail": "无法识别要导入的条目。"})
            continue
        try:
            document = fetch_document(
                client,
                kind=kind,
                source_id=source_id,
                title=str(raw.get("title") or ""),
                knowledge_base_id=str(raw.get("knowledge_base_id") or "") or None,
            )
            payload = import_payload(
                db,
                filename=document["filename"],
                data=document["data"],
                origin={
                    "platform": "ima",
                    "source_id": document["source_id"],
                    "source_kind": "connector",
                    "connector_id": str(account.id or "ima"),
                    "external_id": document["external_id"],
                    "title": document["title"],
                    "source_mtime": document.get("source_mtime"),
                    "metrics": document["metrics"],
                },
                registry=registry,
            )
            imported.append(payload)
        except ImportError as exc:
            errors.append({"id": source_id, "detail": exc.detail})
        except ImaError as exc:
            errors.append({"id": source_id, "detail": exc.message})
    if not imported and errors:
        raise ImaError("api", errors[0]["detail"])
    account.last_error = None
    account.last_sync_at = utcnow()
    db.add(account)
    db.commit()
    return {"imported": imported, "errors": errors, "count": len(imported)}


def fetch_document(
    client: ImaClient,
    *,
    kind: str,
    source_id: str,
    title: str = "",
    knowledge_base_id: str | None = None,
) -> dict[str, Any]:
    if kind == "note":
        return _note_document(client, source_id, title=title)
    return _media_document(client, source_id, title=title, knowledge_base_id=knowledge_base_id)


def fetch_imported_body(client: ImaClient, metrics: dict[str, Any], *, title: str) -> str:
    """Re-read one imported ima row. Raises ImaError when the source is unreachable."""
    kind = str(metrics.get("ima_kind") or "")
    external_id = str(metrics.get("ima_id") or "")
    if not kind or not external_id:
        raise ImaError("unreachable", "这条记录没有 ima 来源 id，无法对账。")
    document = fetch_document(
        client,
        kind=kind,
        source_id=external_id,
        title=title,
        knowledge_base_id=str(metrics.get("knowledge_base_id") or "") or None,
    )
    return document["text"]


def _note_document(client: ImaClient, note_id: str, *, title: str) -> dict[str, Any]:
    result = client.post(
        PATH_GET_DOC_CONTENT,
        {"note_id": note_id, "target_content_format": 0},
        cache=False,
    )
    text = str(result.data.get("content") or "").strip()
    if not text:
        raise ImaError("api", "这篇笔记没有可导入的正文。")
    name = title.strip() or "ima笔记"
    filename = safe_filename(f"{name}.txt")
    return {
        "filename": filename,
        "data": text.encode("utf-8"),
        "text": text,
        "title": name,
        "source_id": f"note:{note_id}",
        "external_id": note_id,
        "source_mtime": None,
        "metrics": {
            "filename": filename,
            "source": "ima",
            "ima_kind": "note",
            "ima_id": note_id,
            "bytes": len(text.encode("utf-8")),
        },
    }


def _media_document(
    client: ImaClient,
    media_id: str,
    *,
    title: str,
    knowledge_base_id: str | None,
) -> dict[str, Any]:
    info = client.post(PATH_GET_MEDIA_INFO, {"media_id": media_id}, cache=False)
    data = info.data
    media_type = data.get("media_type")
    if media_type == 11:
        note_id = str((data.get("notebook_ext_info") or {}).get("notebook_id") or "")
        if not note_id:
            raise ImaError("api", "ima 没有返回这篇笔记的 note id。")
        document = _note_document(client, note_id, title=title)
        document["source_id"] = f"media:{media_id}"
        document["external_id"] = media_id
        document["metrics"] = {
            **document["metrics"],
            "ima_kind": "media",
            "ima_id": media_id,
            "note_id": note_id,
            "media_type": 11,
            "knowledge_base_id": knowledge_base_id,
        }
        return document
    url_info = data.get("url_info") if isinstance(data.get("url_info"), dict) else {}
    url = str(url_info.get("url") or "")
    if not url:
        raise ImaError("api", "ima 没有返回可导入的原文。请使用 ima 客户端查看。")
    headers = url_info.get("headers") if isinstance(url_info.get("headers"), dict) else {}
    from app.ingest.local_file import MAX_BYTES

    _status, payload, content_type = client.download(url, headers, limit=MAX_BYTES)
    filename, text, raw = _normalize_download(title or media_id, payload, content_type, media_type)
    return {
        "filename": filename,
        "data": raw,
        "text": text,
        "title": title.strip() or filename,
        "source_id": f"media:{media_id}",
        "external_id": media_id,
        "source_mtime": None,
        "metrics": {
            "filename": filename,
            "source": "ima",
            "ima_kind": "media",
            "ima_id": media_id,
            "media_type": media_type,
            "knowledge_base_id": knowledge_base_id,
            "bytes": len(raw),
        },
    }


def _normalize_download(title: str, payload: bytes, content_type: str, media_type: Any) -> tuple[str, str, bytes]:
    from app.ingest.local_file import extract_text

    lowered = content_type.lower()
    name = title.strip() or "ima-file"
    if media_type == 1 or "pdf" in lowered or name.lower().endswith(".pdf"):
        filename = safe_filename(name if name.lower().endswith(".pdf") else f"{name}.pdf")
        text = extract_text(filename, payload)
        return filename, text, payload
    if media_type == 3 or "word" in lowered or name.lower().endswith(".docx"):
        filename = safe_filename(name if name.lower().endswith(".docx") else f"{name}.docx")
        text = extract_text(filename, payload)
        return filename, text, payload
    if media_type in {2, 6, 20} or "html" in lowered:
        text = _html_to_text(payload)
        if not text:
            raise ImaError("api", "网页原文里没有抽出正文。")
        filename = safe_filename(f"{_stem(name)}.txt")
        return filename, text, text.encode("utf-8")
    if media_type in {7, 13} or "markdown" in lowered or "text/plain" in lowered or _text_name(name):
        suffix = ".md" if media_type == 7 or name.lower().endswith(".md") else ".txt"
        filename = safe_filename(name if _text_name(name) else f"{_stem(name)}{suffix}")
        text = extract_text(filename, payload).strip()
        if not text:
            raise ImaError("api", "文件里没有可导入的正文。")
        return filename, text, payload
    raise ImaError("api", "该 ima 条目暂不能导入本地库。目前支持笔记、网页文本、md、txt、pdf、docx。")


def _html_to_text(payload: bytes) -> str:
    from app.ingest.local_file import extract_text

    raw = extract_text("page.txt", payload)
    raw = _TAG.sub(" ", raw)
    raw = _TAGS.sub(" ", raw)
    return _SPACE.sub("\n", raw).strip()


def _text_name(name: str) -> bool:
    lowered = name.lower()
    return lowered.endswith(".md") or lowered.endswith(".markdown") or lowered.endswith(".txt")


def _stem(name: str) -> str:
    cleaned = name.rsplit("/", 1)[-1]
    if "." in cleaned:
        return cleaned.rsplit(".", 1)[0] or cleaned
    return cleaned


def is_folder(entry: dict[str, Any]) -> bool:
    """Best-effort folder test. The live discriminator is not confirmed."""
    if entry.get("is_folder") is True or entry.get("isFolder") is True:
        return True
    kind = _text(entry, "type", "item_type", "knowledge_type").lower()
    if kind in {"folder", "dir", "directory"}:
        return True
    has_media = bool(_media_id_of(entry))
    has_folder = bool(_text(entry, "folder_id", "folderId"))
    if has_folder and not has_media:
        return True
    if any(key in entry for key in ("file_number", "folder_number", "file_count", "folder_count")):
        return True
    title = _text(entry, "title", "name", "folder_name", "file_name")
    return title.endswith("/") and not has_media


def _split_knowledge(entries: list[dict[str, Any]], knowledge_base_id: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    folders: list[dict[str, Any]] = []
    items: list[dict[str, Any]] = []
    for entry in entries:
        if is_folder(entry):
            folder_id = _text(entry, "folder_id", "folderId", "media_id", "id")
            if not folder_id:
                continue
            file_number = entry.get("file_number")
            if file_number is None:
                file_number = entry.get("file_count")
            folder_number = entry.get("folder_number")
            if folder_number is None:
                folder_number = entry.get("folder_count")
            folders.append(
                {
                    "kind": "folder",
                    "id": folder_id,
                    "title": (
                        _text(entry, "name", "folder_name", "title", "file_name") or "未命名文件夹"
                    ).rstrip("/"),
                    "file_number": file_number,
                    "folder_number": folder_number,
                    "knowledge_base_id": knowledge_base_id,
                }
            )
            continue
        media_id = _media_id_of(entry) or _text(entry, "id")
        if not media_id:
            continue
        items.append(
            {
                "kind": "media",
                "id": media_id,
                "title": _text(entry, "title", "name", "file_name") or "未命名文件",
                "snippet": _text(entry, "highlight_content", "highlight", "snippet", "summary"),
                "knowledge_base_id": knowledge_base_id,
                "media_type": entry.get("media_type", entry.get("mediaType")),
                "parent_folder_id": _text(entry, "parent_folder_id", "parentFolderId"),
            }
        )
    return folders, items


def _knowledge_base_details(client: ImaClient, ids: list[str]) -> dict[str, dict[str, Any]]:
    if not ids:
        return {}
    try:
        info = client.post(PATH_GET_KNOWLEDGE_BASE, {"ids": ids[:20]})
    except ImaError:
        return {}
    found = info.data.get("infos")
    mapped: dict[str, dict[str, Any]] = {}
    if isinstance(found, dict):
        for key, value in found.items():
            if not isinstance(value, dict):
                continue
            mapped[knowledge_base_id_of(value) or str(key)] = value
        return mapped
    for value in _list(found):
        kb_id = knowledge_base_id_of(value)
        if kb_id:
            mapped[kb_id] = value
    return mapped


def _note_fields(entry: dict[str, Any]) -> dict[str, str] | None:
    """Read a search_note hit.

    Live items put the note under ``note_book_info`` and the highlight under
    ``highlightInfo``. Older flat shapes stay accepted.
    """
    info = _nested(entry, "note_book_info", "noteBookInfo", "note_info", "note") or entry
    note_id = _text(info, "note_id", "doc_id", "docid", "id") or _text(entry, "note_id", "doc_id", "docid", "id")
    if not note_id:
        return None
    highlight = _nested(entry, "highlightInfo", "highlight_info") or {}
    title = _text(info, "title", "name") or _strip_em(_text(highlight, "doc_title")) or "未命名笔记"
    summary = _text(info, "summary", "abstract")
    snippet = _highlight_text(entry) or summary
    return {"id": note_id, "title": title, "summary": summary, "snippet": snippet}


def _highlight_text(entry: dict[str, Any]) -> str:
    raw: Any = None
    for key in ("highlightInfo", "highlight_info"):
        if key in entry:
            raw = entry.get(key)
            break
    parts: list[str] = []
    if isinstance(raw, dict):
        parts.append(_text(raw, "highlight_content", "content", "snippet", "text", "abstract", "doc_title"))
    elif isinstance(raw, list):
        for item in raw:
            if isinstance(item, dict):
                parts.append(_text(item, "highlight_content", "content", "snippet", "text", "doc_title"))
            elif isinstance(item, str) and item.strip():
                parts.append(item.strip())
    elif isinstance(raw, str) and raw.strip():
        parts.append(raw.strip())
    if not any(parts):
        parts.append(_text(entry, "highlight_content", "highlight", "snippet"))
    return _strip_em(" ".join(part for part in parts if part))


def _media_id_of(entry: dict[str, Any]) -> str:
    return _text(entry, "media_id", "knowledge_id")


def _nested(entry: dict[str, Any], *keys: str) -> dict[str, Any] | None:
    for key in keys:
        value = entry.get(key)
        if isinstance(value, dict) and value:
            return value
    return None


def _named_list(data: dict[str, Any], *keys: str) -> list[dict[str, Any]]:
    """First present key wins, including an empty list. Later keys are fallbacks."""
    for key in keys:
        if key in data:
            return _list(data.get(key))
    return []


def _text(entry: dict[str, Any], *keys: str) -> str:
    for key in keys:
        value = entry.get(key)
        if isinstance(value, str):
            cleaned = value.strip()
            if cleaned:
                return cleaned
        elif isinstance(value, int) and not isinstance(value, bool):
            return str(value)
    return ""


def _list(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def _strip_em(value: str) -> str:
    return _EM.sub("", value or "")


def _public_config(config: dict[str, Any] | None) -> dict[str, Any]:
    blocked = {"api_key", "apikey", "client_id", "clientid", "secret", "token", "password"}
    clean: dict[str, Any] = {}
    for key, value in (config or {}).items():
        if str(key).lower() in blocked:
            continue
        if isinstance(value, str) and len(value) > 80:
            continue
        clean[str(key)] = value
    return clean


def _iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is not None:
        value = value.astimezone(timezone.utc).replace(tzinfo=None)
    return value.isoformat(timespec="seconds")
