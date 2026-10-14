"""POST /chat — trivial SSE stub. No retrieval and no LLM call in M0."""

from __future__ import annotations

import json

from fastapi import APIRouter
from fastapi.responses import StreamingResponse

router = APIRouter()


@router.post("")
async def chat() -> StreamingResponse:
    async def events():
        delta = {"text": "（M0 stub）sidecar 已连通，问答主路径尚未实现。"}
        yield "event: delta\ndata: " + json.dumps(delta, ensure_ascii=False) + "\n\n"
        done = {
            "provider": None,
            "model": None,
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "latency_ms": 0,
        }
        yield "event: done\ndata: " + json.dumps(done) + "\n\n"

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
