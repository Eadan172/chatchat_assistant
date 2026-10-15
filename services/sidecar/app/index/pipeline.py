"""Chunk a content item and write FTS5, the blob vector store, and sqlite-vec."""

from __future__ import annotations

import logging
import sqlite3
from typing import Any

from sqlalchemy import func
from sqlmodel import Session, select

from app.db.models import Chunk, ContentItem
from app.db.session import get_engine, open_session
from app.db.vec import try_enable_vec
from app.index.chunker import chunk_item
from app.index.embedder import embed_texts, pack_f32
from app.index.tokens import token_count

logger = logging.getLogger(__name__)


def indexed_chunk_count() -> int:
    with open_session() as db:
        raw = db.exec(select(func.count(Chunk.id))).one()
    if isinstance(raw, tuple):
        raw = raw[0]
    return int(raw or 0)


def index_item(item_id: int, *, registry=None) -> dict[str, Any]:
    """Replace chunks for one item. Failure marks the row ``failed`` and does not raise."""
    with open_session() as db:
        item = db.get(ContentItem, item_id)
        if item is None:
            return {"id": item_id, "index_state": "missing", "chunks": 0}
        try:
            count = _write_chunks(db, item, registry=registry)
        except Exception as exc:
            logger.exception("index failed for item %s", item_id)
            db.rollback()
            failed = db.get(ContentItem, item_id)
            if failed is not None:
                failed.index_state = "failed"
                failed.last_error = str(exc)[:500]
                db.add(failed)
                db.commit()
            return {"id": item_id, "index_state": "failed", "chunks": 0, "error": str(exc)[:500]}
        item.index_state = "indexed"
        item.last_error = None
        db.add(item)
        db.commit()
        return {"id": item_id, "index_state": "indexed", "chunks": count}


def rebuild_index(*, scope: str = "pending", registry=None) -> dict[str, Any]:
    wanted = "all" if scope == "all" else "pending"
    with open_session() as db:
        statement = select(ContentItem.id)
        if wanted == "pending":
            statement = statement.where(ContentItem.index_state != "indexed")
        ids = [int(row) for row in db.exec(statement).all() if row is not None]
    indexed = 0
    failed = 0
    for item_id in ids:
        result = index_item(item_id, registry=registry)
        if result.get("index_state") == "indexed":
            indexed += 1
        else:
            failed += 1
    return {"accepted": True, "stub": False, "scope": wanted, "indexed": indexed, "failed": failed}


def delete_item_index(conn: sqlite3.Connection, item_id: int) -> None:
    chunk_ids = [row[0] for row in conn.execute("SELECT id FROM chunk WHERE item_id = ?", (item_id,))]
    for chunk_id in chunk_ids:
        _delete_chunk_rows(conn, int(chunk_id))
    conn.execute("DELETE FROM chunk WHERE item_id = ?", (item_id,))


def _write_chunks(db: Session, item: ContentItem, *, registry=None) -> int:
    del db  # writes go through a short sqlite connection so vec loading stays local
    texts = chunk_item(item)
    if not texts:
        raise ValueError("文档没有可索引的正文")
    vectors = embed_texts(texts, registry)
    if len(vectors) != len(texts):
        raise RuntimeError("embedder returned a different number of vectors")
    path = get_engine().url.database
    if not path:
        raise RuntimeError("database path is missing")
    conn = sqlite3.connect(path, timeout=30)
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        use_vec = try_enable_vec(conn)
        conn.execute("BEGIN")
        delete_item_index(conn, int(item.id or 0))
        for ordinal, (text, vector) in enumerate(zip(texts, vectors)):
            cursor = conn.execute(
                "INSERT INTO chunk(item_id, ordinal, text, token_count, vec_rowid, meta) VALUES (?, ?, ?, ?, NULL, '{}')",
                (item.id, ordinal, text, token_count(text)),
            )
            chunk_id = int(cursor.lastrowid or 0)
            blob = pack_f32(vector)
            conn.execute(
                "INSERT INTO chunk_vector(chunk_id, embedding, dim) VALUES (?, ?, ?)",
                (chunk_id, blob, len(vector)),
            )
            conn.execute(
                "INSERT INTO fts_chunks(text, item_id, chunk_id) VALUES (?, ?, ?)",
                (text, item.id, chunk_id),
            )
            if use_vec:
                conn.execute(
                    "INSERT INTO vec_chunks(chunk_id, embedding) VALUES (?, ?)",
                    (chunk_id, blob),
                )
            conn.execute("UPDATE chunk SET vec_rowid = ? WHERE id = ?", (chunk_id, chunk_id))
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    return len(texts)


def _delete_chunk_rows(conn: sqlite3.Connection, chunk_id: int) -> None:
    conn.execute("DELETE FROM fts_chunks WHERE chunk_id = ?", (chunk_id,))
    conn.execute("DELETE FROM chunk_vector WHERE chunk_id = ?", (chunk_id,))
    if _vec_ready(conn):
        try:
            conn.execute("DELETE FROM vec_chunks WHERE chunk_id = ?", (chunk_id,))
        except sqlite3.OperationalError:
            pass


def _vec_ready(conn: sqlite3.Connection) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'vec_chunks'"
    ).fetchone()
    return row is not None


def connect_db() -> sqlite3.Connection:
    path = get_engine().url.database
    if not path:
        raise RuntimeError("database path is missing")
    conn = sqlite3.connect(path, timeout=30)
    conn.execute("PRAGMA foreign_keys=ON")
    return conn
