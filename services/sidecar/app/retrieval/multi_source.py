"""Multi-source retrieval entry.

M3 searches the local library. Connector sync and web search stay unplugged:
personal mode never calls them, and this module does not import a search provider.
"""

from __future__ import annotations

from typing import Any


def consume_brief(
    brief: dict[str, Any] | None,
    *,
    mode: str,
    sources: list[str] | None = None,
) -> dict[str, Any]:
    """Record the brief and the caller's channel whitelist. Does not search."""
    status = None
    if isinstance(brief, dict):
        status = brief.get("status") or brief.get("brief")
    return {
        "mode": mode,
        "sources": list(sources or ["local"]),
        "brief_status": status,
        "brief": brief,
        "hits": [],
    }


def search_local_library(
    query: str,
    *,
    mode: str,
    sources: list[str] | None,
    brief: dict[str, Any] | None,
    top_k: int = 8,
    registry=None,
) -> list[dict]:
    """Local hybrid search only. Web and connector providers are not called."""
    from app.retrieval.hybrid import search_local

    return search_local(
        query,
        mode=mode,
        sources=sources,
        brief=brief,
        top_k=top_k,
        registry=registry,
    )


async def multi_source_search(*args, **kwargs) -> list[dict]:
    query = kwargs.get("query")
    if args and query is None:
        query = args[0]
    return search_local_library(
        str(query or ""),
        mode=str(kwargs.get("mode") or "personal"),
        sources=kwargs.get("sources"),
        brief=kwargs.get("brief"),
        top_k=int(kwargs.get("top_k") or 8),
        registry=kwargs.get("registry"),
    )
