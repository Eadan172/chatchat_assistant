"""Chat pipeline.

M1 hands a confirmed or skipped ClarifiedBrief to retrieval. M2 records Jev
probes in shadow mode and does not switch channels, clarification, or
generation. M3 runs local retrieval when the library already has chunks.
Shadow decisions stay advisory: ``sources`` on the retrieval record is the
caller whitelist, not the decider's channel list.
"""

from __future__ import annotations

from typing import Any, Mapping

from app.decision.client import DecisionClient
from app.decision.shadow import observe_chat
from app.index.pipeline import indexed_chunk_count
from app.retrieval.hybrid import scope_terms
from app.retrieval.multi_source import consume_brief, search_local_library


def probe_decisions(
    *,
    message: str,
    mode: str,
    clarify: str,
    verify: str,
    sources: list[str] | None,
    conversation_id: int | None,
    brief: Mapping[str, Any] | None,
    client: DecisionClient | None = None,
    link_trace: bool = False,
) -> list[dict[str, Any]]:
    """Log intent and channel probes. The return value is advisory SSE only."""
    return observe_chat(
        message=message,
        mode=mode,
        clarify=clarify,
        verify=verify,
        sources=sources,
        conversation_id=conversation_id,
        brief=brief,
        client=client,
        link_trace=link_trace,
    )


def prepare_chat_context(
    *,
    brief: dict[str, Any] | None,
    mode: str,
    sources: list[str] | None,
    verify: str,
    query: str = "",
    top_k: int = 8,
    registry=None,
) -> dict[str, Any]:
    """Attach the brief, then search the local library when it has chunks."""
    retrieval = consume_brief(brief, mode=mode, sources=sources)
    searched = bool(query.strip()) and indexed_chunk_count() > 0
    if searched:
        retrieval["hits"] = search_local_library(
            query,
            mode=mode,
            sources=retrieval["sources"],
            brief=brief,
            top_k=top_k,
            registry=registry,
        )
        retrieval["scope_applied"] = bool(scope_terms(brief))
    return {"verify": verify, "retrieval": retrieval, "searched": searched}


def run_chat_pipeline(**kwargs) -> dict[str, Any]:
    """Synchronous retrieval context. Generation lives in the chat route."""
    return prepare_chat_context(
        brief=kwargs.get("brief"),
        mode=str(kwargs.get("mode") or "personal"),
        sources=kwargs.get("sources"),
        verify=str(kwargs.get("verify") or "auto"),
        query=str(kwargs.get("query") or ""),
        top_k=int(kwargs.get("top_k") or 8),
        registry=kwargs.get("registry"),
    )
