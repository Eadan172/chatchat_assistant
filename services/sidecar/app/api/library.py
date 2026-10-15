"""Local library: import files, list them, and delete a row plus its index."""

from __future__ import annotations

import shutil
from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from sqlalchemy import func
from sqlmodel import select

from app.db.models import Chunk, ContentItem
from app.db.session import get_engine, open_session
from app.index.pipeline import connect_db, delete_item_index
from app.ingest.local_file import ImportError, decode_base64, import_payload

router = APIRouter()


class ImportBody(BaseModel):
    filename: str
    content_base64: str | None = None
    text: str | None = None
    path: str | None = None


@router.get("/items")
def list_items() -> dict:
    with open_session() as db:
        rows = db.exec(select(ContentItem).order_by(ContentItem.id.desc())).all()
        counts = {
            int(item_id): int(count)
            for item_id, count in db.exec(select(Chunk.item_id, func.count(Chunk.id)).group_by(Chunk.item_id)).all()
        }
        items = [_public(row, counts.get(int(row.id or 0), 0)) for row in rows]
    return {"items": items, "total": len(items), "stub": False}


@router.post("/import")
def import_item(body: ImportBody) -> dict:
    filename, data = _bytes(body)
    with open_session() as db:
        try:
            return import_payload(db, filename=filename, data=data)
        except ImportError as exc:
            raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc


@router.delete("/items/{item_id}")
def delete_item(item_id: int) -> dict:
    with open_session() as db:
        item = db.get(ContentItem, item_id)
        if item is None:
            raise HTTPException(status_code=404, detail="未找到文档")
        url = item.url or ""
    conn = connect_db()
    try:
        delete_item_index(conn, item_id)
        conn.execute("DELETE FROM content_item WHERE id = ?", (item_id,))
        conn.commit()
    finally:
        conn.close()
    _remove_stored_file(url)
    return {"id": item_id, "deleted": True, "stub": False}


def _bytes(body: ImportBody) -> tuple[str, bytes]:
    if body.path:
        file_path = Path(body.path)
        if not file_path.is_file():
            raise HTTPException(status_code=404, detail="找不到要导入的文件")
        return body.filename or file_path.name, file_path.read_bytes()
    if body.content_base64:
        try:
            return body.filename, decode_base64(body.content_base64)
        except ImportError as exc:
            raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
    if body.text is not None:
        return body.filename, body.text.encode("utf-8")
    raise HTTPException(status_code=400, detail="请提供文件内容")


def _public(item: ContentItem, chunks: int) -> dict:
    metrics = item.metrics or {}
    collected = item.collected_at.isoformat(timespec="seconds") if item.collected_at else None
    return {
        "id": item.id,
        "title": item.title,
        "filename": metrics.get("filename") or item.source_id,
        "platform": item.platform,
        "source_kind": item.source_kind,
        "index_state": item.index_state,
        "last_error": item.last_error,
        "url": item.url,
        "chunk_count": chunks,
        "collected_at": collected,
    }


def _remove_stored_file(url: str) -> None:
    if not url:
        return
    path = Path(url)
    root = Path(get_engine().url.database or ".").parent.parent / "library"
    try:
        resolved = path.resolve()
        library = root.resolve()
    except OSError:
        return
    if library not in resolved.parents:
        return
    if resolved.is_file():
        resolved.unlink(missing_ok=True)
    parent = resolved.parent
    if parent.is_dir() and parent != library and not any(parent.iterdir()):
        shutil.rmtree(parent, ignore_errors=True)
