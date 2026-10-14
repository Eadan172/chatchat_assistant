"""POST /chat — SSE stub that accepts a confirmed or skipped ClarifiedBrief.

Generation and retrieval stay placeholders. A brief_id is stored on the
conversation and handed to the retrieval hook. clarify=skip writes brief=skipped
and takes the same path.
"""

from __future__ import annotations

import json
from typing import Literal

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ValidationError

from app.clarifier.session import ClarifyError, attach_chat_turn, resolve_chat_brief
from app.db.session import open_session
from app.orchestrator.chat_pipeline import prepare_chat_context

router = APIRouter()

STUB_TEXT = "（M0 stub）sidecar 已连通，问答主路径尚未实现。"


class ChatMessageIn(BaseModel):
    role: str = "user"
    content: str = ""


class ChatBody(BaseModel):
    message: str | None = None
    query: str | None = None
    keyword: str | None = None
    mode: str = "personal"
    messages: list[ChatMessageIn] = []
    conversation_id: int | None = None
    brief_id: int | None = None
    clarify: Literal["auto", "force", "skip"] = "auto"
    sources: list[str] | None = None
    verify: Literal["auto", "force", "skip"] = "auto"
    provider: str | None = None
    model: str | None = None
    top_k: int | None = None
    domain_hint: str | None = None


def _user_text(body: ChatBody) -> str:
    for candidate in (body.message, body.query, body.keyword):
        if candidate and candidate.strip():
            return candidate.strip()
    for message in reversed(body.messages):
        if message.role == "user" and message.content.strip():
            return message.content.strip()
    return ""


def _assistant_text(status: str | None) -> str:
    label = status or "unknown"
    return f"已接收澄清结果（{label}）。检索与生成仍是占位，brief 已写入会话。"


async def _parse_body(request: Request) -> ChatBody:
    raw = await request.body()
    if not raw or not raw.strip():
        return ChatBody()
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=422, detail="invalid json") from exc
    if not isinstance(data, dict):
        raise HTTPException(status_code=422, detail="invalid json")
    try:
        return ChatBody.model_validate(data)
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail="invalid chat body") from exc


def _prepare(body: ChatBody) -> dict | None:
    text = _user_text(body)
    with open_session() as db:
        try:
            row = resolve_chat_brief(
                db,
                brief_id=body.brief_id,
                conversation_id=body.conversation_id,
                clarify=body.clarify,
                keyword=text,
                domain_hint=body.domain_hint,
            )
            if row is None:
                return None
            brief = dict(row.brief_json or {})
            context = prepare_chat_context(
                brief=brief,
                mode=body.mode,
                sources=body.sources,
                verify=body.verify,
            )
            status = brief.get("status") or brief.get("brief")
            assistant = _assistant_text(str(status) if status else None)
            brief_id = row.id
            conversation_id = attach_chat_turn(
                db,
                session_row=row,
                conversation_id=body.conversation_id,
                user_text=text,
                assistant_text=assistant,
                mode=body.mode,
                sources=body.sources,
                verify=body.verify,
                clarify=body.clarify,
            )
            retrieval = context["retrieval"]
            return {
                "assistant_text": assistant,
                "brief_id": brief_id,
                "conversation_id": conversation_id,
                "brief": brief,
                "retrieval": {
                    "accepted_brief": True,
                    "brief_status": retrieval.get("brief_status"),
                    "mode": retrieval.get("mode"),
                    "sources": retrieval.get("sources"),
                    "hits": retrieval.get("hits") or [],
                },
            }
        except ClarifyError as exc:
            db.rollback()
            raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc


def _sse(event: str, payload: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"


@router.post("")
async def chat(request: Request) -> StreamingResponse:
    body = await _parse_body(request)
    outcome = _prepare(body)

    def events():
        if outcome is None:
            yield _sse("delta", {"text": STUB_TEXT})
            yield _sse(
                "done",
                {
                    "provider": None,
                    "model": None,
                    "prompt_tokens": 0,
                    "completion_tokens": 0,
                    "latency_ms": 0,
                },
            )
            return
        yield _sse("delta", {"text": outcome["assistant_text"]})
        yield _sse(
            "done",
            {
                "provider": None,
                "model": None,
                "prompt_tokens": 0,
                "completion_tokens": 0,
                "latency_ms": 0,
                "brief_id": outcome["brief_id"],
                "conversation_id": outcome["conversation_id"],
                "brief": outcome["brief"],
                "retrieval": outcome["retrieval"],
            },
        )

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
