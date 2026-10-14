"""Chat pipeline.

M1 hands a confirmed or skipped ClarifiedBrief to retrieval. Jev routing,
freshness, and generation stay unimplemented, so the full pipeline still raises.
"""

from __future__ import annotations

from typing import Any

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


async def run_chat_pipeline(*_args, **_kwargs) -> None:
    raise NotImplementedError("full chat generation is not implemented")
