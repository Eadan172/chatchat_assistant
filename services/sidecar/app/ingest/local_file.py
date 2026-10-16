"""Import a local md, txt, pdf, or docx file into content_item."""

from __future__ import annotations

import base64
import io
import re
import zipfile
from pathlib import Path
from xml.etree import ElementTree

from sqlmodel import Session, select

from app.db.models import ContentItem, utcnow
from app.freshness.hasher import content_hash, file_mtime
from app.index.pipeline import index_item

_ALLOWED = {".md", ".markdown", ".txt", ".pdf", ".docx"}
MAX_BYTES = 8_000_000
_MAX_BYTES = MAX_BYTES
_NAME = re.compile(r"[^\w.\- \u4e00-\u9fff]+", re.UNICODE)


class ImportError(Exception):
    def __init__(self, status_code: int, detail: str) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


def safe_filename(name: str) -> str:
    base = Path(name or "").name.strip() or "document.txt"
    cleaned = _NAME.sub("_", base).strip("._ ")
    return (cleaned or "document.txt")[:120]


def import_payload(
    db: Session,
    *,
    filename: str,
    data: bytes,
    source_path: str | None = None,
    registry=None,
) -> dict:
    name = safe_filename(filename)
    suffix = Path(name).suffix.lower()
    if suffix not in _ALLOWED:
        raise ImportError(415, "仅支持 md、txt、pdf、docx")
    if not data:
        raise ImportError(400, "文件是空的")
    if len(data) > _MAX_BYTES:
        raise ImportError(413, "文件超过 8MB")
    try:
        body = extract_text(name, data)
    except ImportError:
        raise
    except Exception as exc:
        raise ImportError(400, f"无法读取文件：{exc}") from exc
    body = body.strip()
    if not body:
        raise ImportError(400, "没有从文件中抽出正文")
    title = Path(name).stem or name
    digest = content_hash(title, body)
    existing = db.exec(
        select(ContentItem).where(ContentItem.platform == "local_file", ContentItem.source_id == name)
    ).first()
    if existing is not None and existing.content_hash == digest and existing.index_state == "indexed":
        return _public(existing, unchanged=True)
    now = utcnow()
    if existing is None:
        item = ContentItem(
            platform="local_file",
            source_id=name,
            content_hash=digest,
            url="",
            title=title,
            body_text=body,
            index_state="pending",
            source_kind="local_file",
            fetched_at=now,
            freshness_state="fresh",
            freshness_reason="imported",
            media=[],
            metrics={"filename": name, "bytes": len(data)},
        )
        db.add(item)
        db.commit()
        db.refresh(item)
    else:
        item = existing
        item.content_hash = digest
        item.title = title
        item.body_text = body
        item.index_state = "pending"
        item.last_error = None
        item.source_kind = "local_file"
        item.fetched_at = now
        item.metrics = {"filename": name, "bytes": len(data)}
        db.add(item)
        db.commit()
        db.refresh(item)
    path = _store_bytes(int(item.id or 0), name, data)
    _stamp_source(item, path, source_path)
    db.add(item)
    db.commit()
    indexed = index_item(int(item.id or 0), registry=registry)
    db.refresh(item)
    payload = _public(item, unchanged=False)
    payload["chunks"] = indexed.get("chunks", 0)
    return payload


def extract_text(filename: str, data: bytes) -> str:
    suffix = Path(filename).suffix.lower()
    if suffix in {".md", ".markdown", ".txt"}:
        return _decode(data)
    if suffix == ".pdf":
        return _read_pdf(data)
    if suffix == ".docx":
        return _read_docx(data)
    raise ImportError(415, "仅支持 md、txt、pdf、docx")


def decode_base64(payload: str) -> bytes:
    raw = payload.strip()
    if "," in raw and raw.lower().startswith("data:"):
        raw = raw.split(",", 1)[1]
    try:
        return base64.b64decode(raw, validate=True)
    except Exception as exc:
        raise ImportError(400, "content_base64 无法解码") from exc


def _decode(data: bytes) -> str:
    for encoding in ("utf-8-sig", "utf-8", "gbk"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def _read_pdf(data: bytes) -> str:
    try:
        from pypdf import PdfReader
    except ImportError as exc:
        raise ImportError(400, "未安装 pypdf，无法读取 PDF") from exc
    reader = PdfReader(io.BytesIO(data))
    pages = [(page.extract_text() or "") for page in reader.pages]
    return "\n".join(pages)


def _read_docx(data: bytes) -> str:
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as exc:
        raise ImportError(400, "docx 不是有效的压缩包") from exc
    try:
        xml = archive.read("word/document.xml")
    except KeyError as exc:
        raise ImportError(400, "docx 里没有正文") from exc
    root = ElementTree.fromstring(xml)
    ns = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
    paragraphs: list[str] = []
    for paragraph in root.iter(f"{ns}p"):
        line = "".join(node.text or "" for node in paragraph.iter(f"{ns}t")).strip()
        if line:
            paragraphs.append(line)
    return "\n".join(paragraphs)


def reingest_local(
    db: Session,
    item: ContentItem,
    data: bytes,
    *,
    source: Path,
    registry=None,
) -> None:
    """Replace the stored body with the source file and index it again."""
    name = str((item.metrics or {}).get("filename") or item.source_id or source.name)
    body = extract_text(name, data).strip()
    if not body:
        raise OSError("没有从文件中抽出正文")
    title = item.title or Path(name).stem or name
    now = utcnow()
    item.body_text = body
    item.title = title
    item.content_hash = content_hash(title, body)
    item.index_state = "pending"
    item.last_error = None
    item.fetched_at = now
    item.verified_at = now
    item.source_mtime = file_mtime(source)
    item.source_path = str(source.resolve())
    item.source_kind = item.source_kind or "local_file"
    item.freshness_state = "fresh"
    item.freshness_reason = "updated"
    metrics = dict(item.metrics or {})
    metrics["filename"] = name
    metrics["bytes"] = len(data)
    metrics["source_path"] = item.source_path
    item.metrics = metrics
    _sync_stored_copy(item, source, data)
    db.add(item)
    db.commit()
    indexed = index_item(int(item.id or 0), registry=registry)
    db.refresh(item)
    if indexed.get("index_state") != "indexed":
        item.freshness_state = "stale"
        item.freshness_reason = "index_failed"
        db.add(item)
        db.commit()


def _stamp_source(item: ContentItem, stored: Path, source_path: str | None) -> None:
    origin = Path(source_path).resolve() if source_path else stored.resolve()
    now = utcnow()
    item.url = str(stored)
    item.source_path = str(origin)
    item.source_mtime = file_mtime(origin) or file_mtime(stored)
    item.fetched_at = now
    item.verified_at = now
    item.freshness_state = "fresh"
    item.freshness_reason = "imported"
    item.source_kind = "local_file"
    metrics = dict(item.metrics or {})
    metrics["source_path"] = item.source_path
    item.metrics = metrics


def _sync_stored_copy(item: ContentItem, source: Path, data: bytes) -> None:
    if not item.url:
        return
    stored = Path(item.url)
    try:
        if stored.resolve() == source.resolve():
            return
        if stored.parent.is_dir():
            stored.write_bytes(data)
    except OSError:
        return


def _store_bytes(item_id: int, filename: str, data: bytes) -> Path:
    import os

    root = Path(os.getenv("DATA_DIR") or ".") / "library" / "files" / str(item_id)
    root.mkdir(parents=True, exist_ok=True)
    path = root / filename
    path.write_bytes(data)
    return path


def _public(item: ContentItem, *, unchanged: bool) -> dict:
    return {
        "id": item.id,
        "title": item.title,
        "filename": (item.metrics or {}).get("filename") or item.source_id,
        "source_kind": item.source_kind,
        "index_state": item.index_state,
        "url": item.url,
        "unchanged": unchanged,
    }
