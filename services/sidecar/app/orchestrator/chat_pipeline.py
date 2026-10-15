"""Chat pipeline.

M1 hands a confirmed or skipped ClarifiedBrief to retrieval. M2 records Jev
probes in shadow mode and does not switch channels, clarification, or
generation. The full pipeline still raises.
"""

from __future__ import annotations

from typing import Any, Mapping

from app.decision.client import DecisionClient
from app.decision.shadow import observe_chat
from app.retrieval.multi_source import consume_brief


def prepare_chat_context(
    *,
    brief: dict[str, Any] | None,
    mode: str,
    sources: list[str] | None,
    verify: str,
) -> dict[str, Any]:
    """Record the brief on the retrieval path without running search."""
    return {
        "verify": verify,
        "retrieval": consume_brief(brief, mode=mode, sources=sources),
    }


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


async def run_chat_pipeline(*_args, **_kwargs) -> None:
    raise NotImplementedError("full chat generation is not implemented")
