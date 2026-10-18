"""POST /ask — clarify, then search web / ima / local, then answer.

The route stays 501 while FEATURE_ASK is off. /chat is not modified.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field, ValidationError

from app.ask.flags import ask_enabled
from app.clarifier.session import ClarifyError
from app.db.session import open_session
from app.i18n import locale_from, t
from app import styles as style_catalog

router = APIRouter()
logger = logging.getLogger(__name__)


class AskAnswerIn(BaseModel):
    question_id: str = Field(min_length=1)
    action: Literal["accept", "edit", "park"]
    value: str | None = None
    park_reason: str | None = None


class AskBody(BaseModel):
    message: str = ""
    session_id: int | None = None
    answers: list[AskAnswerIn] | None = None
    kb_name: str | None = None
    mode: str = "personal"
    verify: Literal["auto", "force", "skip"] = "auto"
    clarify: Literal["auto", "force", "skip"] = "auto"
    skip_rest: bool = False
    conversation_id: int | None = None
    locale: str | None = None
    style: str = "auto"


def _recent_history(db, conversation_id: int | None, limit: int = 6) -> list[dict]:
    if conversation_id is None:
        return []
    from sqlmodel import select

    from app.db.models import Message

    rows = list(
        db.exec(
            select(Message)
            .where(Message.conversation_id == conversation_id)
            .order_by(Message.id.desc())
            .limit(limit)
        ).all()
    )
    rows.reverse()
    return [{"role": row.role, "content": row.content} for row in rows if (row.content or "").strip()]


def _guard() -> None:
    if not ask_enabled():
        raise HTTPException(status_code=501, detail="not implemented")


def _sse(event: str, payload: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"


async def _parse(request: Request) -> AskBody:
    raw = await request.body()
    if not raw or not raw.strip():
        return AskBody()
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=422, detail="invalid json") from exc
    if not isinstance(data, dict):
        raise HTTPException(status_code=422, detail="invalid json")
    try:
        return AskBody.model_validate(data)
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail="invalid ask body") from exc


@router.post("")
async def ask(request: Request) -> StreamingResponse:
    _guard()
    body = await _parse(request)
    locale = locale_from(body.locale, request.headers)
    if body.session_id is None and not body.message.strip():
        raise HTTPException(status_code=422, detail=t("ask.empty_question", locale))
    registry = getattr(request.app.state, "registry", None)
    answers: list[dict[str, Any]] | None = None
    if body.answers:
        answers = [item.model_dump() for item in body.answers]
    style = style_catalog.resolve(body.style, body.message, locale)

    def events():
        from app.ask.pipeline import iter_ask

        try:
            with open_session() as db:
                try:
                    history = _recent_history(db, body.conversation_id)
                    produced = iter_ask(
                        message=body.message,
                        session_id=body.session_id,
                        answers=answers,
                        kb_name=body.kb_name,
                        mode=body.mode,
                        verify=body.verify,
                        clarify=body.clarify,
                        skip_rest=body.skip_rest,
                        registry=registry,
                        db=db,
                        locale=locale,
                        style=style,
                        conversation_id=body.conversation_id,
                        history=history,
                    )
                    for name, payload in produced:
                        if name == "done" and isinstance(payload, dict) and payload.get("phase") == "answer":
                            if locale != "zh":
                                payload = {**payload, "locale": locale}
                            if style["id"] != "general-plain":
                                payload = {**payload, "style": style_catalog.public(style, locale)}
                        yield _sse(name, payload)
                except ClarifyError as exc:
                    db.rollback()
                    yield _sse("error", {"text": exc.detail})
                    yield _sse("done", {"phase": "error"})
        except Exception:
            logger.exception("ask pipeline failed")
            yield _sse("error", {"text": t("ask.failed", locale)})
            yield _sse("done", {"phase": "error"})

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
