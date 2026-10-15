"""Hybrid retrieval: dense vectors, FTS5 trigram, and lexical overlap via RRF.

The cross-encoder reranker stays lazy. M3 ranks with reciprocal rank fusion
plus token overlap so the offline demo does not load FlagEmbedding.
"""

from __future__ import annotations

import logging
import math
import sqlite3
from typing import Any, Sequence

from app.db.session import get_engine, open_session
from app.db.vec import try_enable_vec
from app.index.embedder import embed_texts, pack_f32, unpack_f32
from app.index.tokens import token_count, tokens, trigram_query

logger = logging.getLogger(__name__)

RRF_K = 60

_SCOPE_STOP = {
    "的",
    "了",
    "和",
    "与",
    "在",
    "是",
    "只",
    "看",
    "仅",
    "把",
    "被",
    "及",
    "或",
    "而",
    "就",
    "都",
    "也",
    "很",
    "还",
    "这",
    "那",
    "有",
    "没",
    "不",
    "只看",
    "仅看",
    "关于",
    "范围",
    "一下",
    "一个",
}


def _load_reranker():
    from FlagEmbedding import FlagReranker

    return FlagReranker("BAAI/bge-reranker-v2-m3", use_fp16=True)


def rrf_fuse(rankings: list[list[int]], k: int = RRF_K) -> list[tuple[int, float]]:
    scores: dict[int, float] = {}
    for ranking in rankings:
        for rank, doc_id in enumerate(ranking, start=1):
            scores[doc_id] = scores.get(doc_id, 0.0) + 1.0 / (k + rank)
    return sorted(scores.items(), key=lambda item: item[1], reverse=True)


def scope_terms(brief: dict | None) -> list[str]:
    """Content words from a clarified scope. Skipped briefs do not filter."""
    if not isinstance(brief, dict):
        return []
    status = brief.get("status") or brief.get("brief")
    if status == "skipped":
        return []
    scope = brief.get("scope")
    if not isinstance(scope, str) or not scope.strip():
        return []
    found: list[str] = []
    for token in tokens(scope):
        if token in _SCOPE_STOP or len(token) < 2:
            continue
        if token not in found:
            found.append(token)
    return found


def scope_text(brief: dict | None) -> str | None:
    if not isinstance(brief, dict):
        return None
    status = brief.get("status") or brief.get("brief")
    if status == "skipped":
        return None
    scope = brief.get("scope")
    if isinstance(scope, str) and scope.strip():
        return scope.strip()
    return None


def search_local(
    query: str,
    *,
    mode: str = "personal",
    sources: list[str] | None = None,
    brief: dict | None = None,
    top_k: int = 8,
    candidate_k: int = 40,
    registry=None,
) -> list[dict[str, Any]]:
    text = (query or "").strip()
    if not text:
        return []
    limit = max(1, min(int(top_k or 8), 20))
    candidates = max(limit, min(int(candidate_k or 40), 80))
    expanded = text
    terms = scope_terms(brief)
    if terms:
        expanded = f"{text}\n{' '.join(terms)}"
    with open_session() as db:
        conn = _connect()
        try:
            allowed = _allowed_ids(conn, mode=mode, sources=sources)
            if not allowed:
                return []
            dense_ids, cosines = _dense_rank(conn, expanded, allowed, candidates, registry)
            fts_ids = _fts_rank(conn, text, allowed, candidates)
            lexical_ids = _lexical_rank(conn, text, allowed, candidates)
            fused = rrf_fuse([dense_ids, fts_ids, lexical_ids])
            hits = _hydrate(conn, fused, allowed, query=text)
            support = set(fts_ids) | set(lexical_ids)
            hits = [
                hit
                for hit in hits
                if hit["chunk_id"] in support or cosines.get(hit["chunk_id"], 0.0) >= 0.45
            ]
        finally:
            conn.close()
    if terms:
        hits = [hit for hit in hits if _matches_scope(hit["text"], terms)]
    return _number(hits[:limit])


async def hybrid_search(
    query: str,
    *,
    mode: str,
    top_k: int = 8,
    candidate_k: int = 40,
    sources: list[str] | None = None,
    brief: dict | None = None,
    registry=None,
) -> list[dict]:
    return search_local(
        query,
        mode=mode,
        sources=sources,
        brief=brief,
        top_k=top_k,
        candidate_k=candidate_k,
        registry=registry,
    )


def _allowed_ids(conn: sqlite3.Connection, *, mode: str, sources: list[str] | None) -> set[int]:
    requested = set(sources or ["local"])
    rows = conn.execute(
        "SELECT id, source_kind, index_state FROM content_item WHERE index_state = 'indexed'"
    ).fetchall()
    allowed: set[int] = set()
    for item_id, kind, _state in rows:
        source_kind = kind or "local_file"
        if mode == "personal" and source_kind in {"web", "connector"}:
            continue
        if source_kind == "web" and "web" not in requested:
            continue
        if source_kind == "connector" and "connector" not in requested:
            continue
        if source_kind in {"local_file", "crawl", "rss", None} and "local" not in requested and mode != "personal":
            continue
        allowed.add(int(item_id))
    return allowed


def _dense_rank(conn, query: str, allowed: set[int], limit: int, registry) -> tuple[list[int], dict[int, float]]:
    vector = embed_texts([query], registry)[0]
    cosines: dict[int, float] = {}
    if try_enable_vec(conn) and _has_table(conn, "vec_chunks"):
        try:
            rows = conn.execute(
                """
                SELECT chunk_id, distance
                FROM vec_chunks
                WHERE embedding MATCH ?
                ORDER BY distance
                LIMIT ?
                """,
                (pack_f32(vector), max(limit * 4, limit)),
            ).fetchall()
            ranked: list[int] = []
            for chunk_id, distance in rows:
                item_id = _item_of(conn, int(chunk_id))
                if item_id not in allowed:
                    continue
                ranked.append(int(chunk_id))
                cosines[int(chunk_id)] = _cosine_from_l2(float(distance))
                if len(ranked) >= limit:
                    break
            if ranked:
                return ranked, cosines
        except sqlite3.OperationalError:
            logger.info("vec_chunks query failed; using the blob store")
    scored: list[tuple[float, int]] = []
    rows = conn.execute(
        """
        SELECT v.chunk_id, v.embedding, c.item_id
        FROM chunk_vector AS v
        JOIN chunk AS c ON c.id = v.chunk_id
        """
    ).fetchall()
    for chunk_id, blob, item_id in rows:
        if int(item_id) not in allowed:
            continue
        similarity = _cosine(vector, unpack_f32(blob))
        scored.append((similarity, int(chunk_id)))
        cosines[int(chunk_id)] = similarity
    scored.sort(key=lambda item: item[0], reverse=True)
    return [chunk_id for _score, chunk_id in scored[:limit]], cosines


def _cosine_from_l2(distance: float) -> float:
    """sqlite-vec L2 distance on unit vectors: cosine = 1 - d^2 / 2."""
    cosine = 1.0 - (distance * distance) / 2.0
    if cosine < -1.0:
        return -1.0
    if cosine > 1.0:
        return 1.0
    return cosine


def _fts_rank(conn, query: str, allowed: set[int], limit: int) -> list[int]:
    match = trigram_query(query)
    if not match or not _has_table(conn, "fts_chunks"):
        return []
    try:
        rows = conn.execute(
            """
            SELECT chunk_id, item_id
            FROM fts_chunks
            WHERE fts_chunks MATCH ?
            ORDER BY bm25(fts_chunks)
            LIMIT ?
            """,
            (match, max(limit * 4, limit)),
        ).fetchall()
    except sqlite3.OperationalError:
        return []
    ranked: list[int] = []
    for chunk_id, item_id in rows:
        if int(item_id) in allowed:
            ranked.append(int(chunk_id))
        if len(ranked) >= limit:
            break
    return ranked


def _lexical_rank(conn, query: str, allowed: set[int], limit: int) -> list[int]:
    wanted = {token for token in tokens(query) if len(token) >= 2}
    if not wanted:
        return []
    rows = conn.execute("SELECT id, item_id, text FROM chunk").fetchall()
    scored: list[tuple[int, int]] = []
    for chunk_id, item_id, text in rows:
        if int(item_id) not in allowed:
            continue
        overlap = len(wanted & set(tokens(text or "")))
        if overlap:
            scored.append((overlap, int(chunk_id)))
    scored.sort(key=lambda item: item[0], reverse=True)
    return [chunk_id for _overlap, chunk_id in scored[:limit]]


def _hydrate(conn, fused: Sequence[tuple[int, float]], allowed: set[int], *, query: str) -> list[dict[str, Any]]:
    if not fused:
        return []
    best = fused[0][1] or 1.0
    query_tokens = {token for token in tokens(query) if len(token) >= 2}
    hits: list[dict[str, Any]] = []
    for chunk_id, score in fused:
        row = conn.execute(
            """
            SELECT c.id, c.item_id, c.ordinal, c.text, c.token_count,
                   i.title, i.url, i.platform, i.source_kind, i.freshness_state, i.source_id
            FROM chunk AS c
            JOIN content_item AS i ON i.id = c.item_id
            WHERE c.id = ?
            """,
            (chunk_id,),
        ).fetchone()
        if row is None or int(row[1]) not in allowed:
            continue
        overlap = len(query_tokens & set(tokens(row[3] or "")))
        blended = (score / best) + min(overlap, 12) * 0.01
        hits.append(
            {
                "chunk_id": int(row[0]),
                "item_id": int(row[1]),
                "ordinal": int(row[2]),
                "text": row[3] or "",
                "token_count": int(row[4] or token_count(row[3] or "")),
                "title": row[5] or row[10] or "未命名",
                "url": row[6] or "",
                "platform": row[7] or "local_file",
                "source_kind": row[8] or "local_file",
                "freshness_state": row[9] or "unknown",
                "filename": row[10] or "",
                "score": round(min(1.0, blended), 4),
            }
        )
    hits.sort(key=lambda item: item["score"], reverse=True)
    return hits


def _number(hits: list[dict[str, Any]]) -> list[dict[str, Any]]:
    numbered = []
    for index, hit in enumerate(hits, start=1):
        numbered.append({**hit, "n": index})
    return numbered


def _matches_scope(text: str, terms: list[str]) -> bool:
    haystack = text or ""
    lowered = haystack.lower()
    return any(term.lower() in lowered for term in terms)


def _item_of(conn, chunk_id: int) -> int | None:
    row = conn.execute("SELECT item_id FROM chunk WHERE id = ?", (chunk_id,)).fetchone()
    if row is None:
        return None
    return int(row[0])


def _has_table(conn, name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE name = ? AND type IN ('table', 'view')",
        (name,),
    ).fetchone()
    return row is not None


def _cosine(left: Sequence[float], right: Sequence[float]) -> float:
    size = min(len(left), len(right))
    if size == 0:
        return 0.0
    dot = 0.0
    left_norm = 0.0
    right_norm = 0.0
    for index in range(size):
        dot += left[index] * right[index]
        left_norm += left[index] * left[index]
        right_norm += right[index] * right[index]
    if left_norm == 0.0 or right_norm == 0.0:
        return 0.0
    return dot / math.sqrt(left_norm * right_norm)


def _connect() -> sqlite3.Connection:
    path = get_engine().url.database
    if not path:
        raise RuntimeError("database path is missing")
    return sqlite3.connect(path, timeout=30)
