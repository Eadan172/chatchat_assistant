"""Hybrid retrieval stub. The reranker is not imported at startup."""

from __future__ import annotations

RRF_K = 60


def _load_reranker():
    from FlagEmbedding import FlagReranker

    return FlagReranker("BAAI/bge-reranker-v2-m3", use_fp16=True)


def rrf_fuse(rankings: list[list[int]], k: int = RRF_K) -> list[tuple[int, float]]:
    scores: dict[int, float] = {}
    for ranking in rankings:
        for rank, doc_id in enumerate(ranking, start=1):
            scores[doc_id] = scores.get(doc_id, 0.0) + 1.0 / (k + rank)
    return sorted(scores.items(), key=lambda item: item[1], reverse=True)


async def hybrid_search(query: str, *, mode: str, top_k: int = 8, candidate_k: int = 40) -> list[dict]:
    raise NotImplementedError("hybrid search is an M0 stub")
