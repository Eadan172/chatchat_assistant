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
    if body.session_id is None and not body.message.strip():
        raise HTTPException(status_code=422, detail="请填写问题")
    registry = getattr(request.app.state, "registry", None)
    answers: list[dict[str, Any]] | None = None
    if body.answers:
        answers = [item.model_dump() for item in body.answers]

    def events():
        from app.ask.pipeline import iter_ask

        try:
            with open_session() as db:
                try:
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
                    )
                    for name, payload in produced:
                        yield _sse(name, payload)
                except ClarifyError as exc:
                    db.rollback()
                    yield _sse("error", {"text": exc.detail})
                    yield _sse("done", {"phase": "error"})
        except Exception:
            logger.exception("ask pipeline failed")
            yield _sse("error", {"text": "问答没有完成，请稍后再试。"})
            yield _sse("done", {"phase": "error"})

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
