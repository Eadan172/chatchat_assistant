"""sqlite-vec helpers. The extension is not loaded during M0 startup."""

from __future__ import annotations

VEC_DDL = """
CREATE VIRTUAL TABLE IF NOT EXISTS vec_chunks USING vec0(
  chunk_id INTEGER PRIMARY KEY,
  embedding FLOAT[1024]
);
"""

FTS_DDL = """
CREATE VIRTUAL TABLE IF NOT EXISTS fts_chunks USING fts5(
  text,
  item_id UNINDEXED,
  chunk_id UNINDEXED,
  tokenize='unicode61'
);
"""


def ensure_vector_tables(conn) -> None:
    """Load sqlite-vec and create virtual tables. Not called from app startup."""
    import sqlite_vec

    sqlite_vec.load(conn)
    conn.execute(VEC_DDL)
    conn.execute(FTS_DDL)
