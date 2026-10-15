"""sqlite-vec helpers. The extension is imported only when indexing or searching."""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

VEC_DDL = """
CREATE VIRTUAL TABLE IF NOT EXISTS vec_chunks USING vec0(
  chunk_id INTEGER PRIMARY KEY,
  embedding FLOAT[1024]
)
"""

FTS_DDL = """
CREATE VIRTUAL TABLE IF NOT EXISTS fts_chunks USING fts5(
  text,
  item_id UNINDEXED,
  chunk_id UNINDEXED,
  tokenize='trigram'
)
"""


def ensure_vector_tables(conn) -> None:
    """Load sqlite-vec and create virtual tables. Raises if the extension is missing."""
    import sqlite_vec

    sqlite_vec.load(conn)
    conn.execute(VEC_DDL)
    conn.execute(FTS_DDL)


def try_enable_vec(conn) -> bool:
    """Return True when sqlite-vec is loaded on this connection. Never raises."""
    try:
        import sqlite_vec
    except ImportError:
        return False
    try:
        conn.enable_load_extension(True)
    except Exception:
        # Some builds allow sqlite_vec.load without this call.
        pass
    try:
        sqlite_vec.load(conn)
        conn.execute(VEC_DDL)
        return True
    except Exception:
        logger.info("sqlite-vec is unavailable; dense search uses the blob store")
        return False
