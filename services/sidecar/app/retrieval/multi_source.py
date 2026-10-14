"""Multi-source retrieval entry.

M1 accepts a clarified or skipped brief and returns no hits. Local, connector,
and web fusion stays later work.
"""

from __future__ import annotations

from typing import Any


def consume_brief(
    brief: dict[str, Any] | None,
    *,
    mode: str,
    sources: list[str] | None = None,
) -> dict[str, Any]:
    """Accept a brief into the retrieval path. Search itself is still a stub."""
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


async def multi_source_search(*_args, **_kwargs) -> list[dict]:
    raise NotImplementedError("multi_source search is an M0 stub")
