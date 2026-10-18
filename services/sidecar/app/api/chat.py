"""POST /chat — local RAG over SSE, with the M0 stub kept for an empty library.

A confirmed or skipped brief is still stored. M2 shadow decisions are logged
and do not change retrieval channels. When the library has indexed chunks the
reply is grounded in those chunks; an empty hit list says the library has no
match and does not call a model for invented facts.
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ValidationError

from app.clarifier.session import ClarifyError, attach_chat_turn, record_plain_turn, resolve_chat_brief
from app.db.session import open_session
from app.freshness.policies import apply_chat_freshness
from app.i18n import locale_from, t
from app.orchestrator.chat_pipeline import prepare_chat_context, probe_decisions
from app.retrieval.answer import compose_answer
from app import styles as style_catalog

router = APIRouter()
logger = logging.getLogger(__name__)

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
    locale: str | None = None
    style: str = "auto"


def _user_text(body: ChatBody) -> str:
    for candidate in (body.message, body.query, body.keyword):
        if candidate and candidate.strip():
            return candidate.strip()
    for message in reversed(body.messages):
        if message.role == "user" and message.content.strip():
            return message.content.strip()
    return ""


def _assistant_text(status: str | None, locale: str = "zh") -> str:
    label = status or "unknown"
    return t("chat.brief_ack", locale, status=label)


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


def _public_hit(hit: dict[str, Any]) -> dict[str, Any]:
    return {
        "n": hit.get("n"),
        "chunk_id": hit.get("chunk_id"),
        "item_id": hit.get("item_id"),
        "ordinal": hit.get("ordinal"),
        "title": hit.get("title"),
        "filename": hit.get("filename"),
        "url": hit.get("url"),
        "platform": hit.get("platform"),
        "source_kind": hit.get("source_kind"),
        "freshness_state": hit.get("freshness_state"),
        "score": hit.get("score"),
        "text": hit.get("text"),
    }


def _prepare(body: ChatBody, registry, client=None, locale: str = "zh") -> dict | None:
    text = _user_text(body)
    style = style_catalog.resolve(body.style, text, locale)
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
            brief = dict(row.brief_json or {}) if row is not None else None
            started = time.perf_counter()
            context = prepare_chat_context(
                brief=brief,
                mode=body.mode,
                sources=body.sources,
                verify=body.verify,
                query=text,
                top_k=body.top_k or 8,
                registry=registry,
            )
            retrieval = context["retrieval"]
            if not context.get("searched"):
                if row is None:
                    return None
                status = brief.get("status") if brief else None
                assistant = _assistant_text(str(status) if status else None, locale)
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
                return _outcome(
                    assistant=assistant,
                    citations=[],
                    provider=None,
                    model=None,
                    prompt_tokens=0,
                    completion_tokens=0,
                    latency_ms=0,
                    brief_id=row.id,
                    conversation_id=conversation_id,
                    brief=brief,
                    retrieval=_public_retrieval(retrieval, accepted=True),
                    gap=None,
                    style=style_catalog.public(style, locale),
                )
            hits = [_public_hit(hit) for hit in retrieval.get("hits") or []]
            freshness = apply_chat_freshness(
                message=text,
                verify=body.verify,
                hits=hits,
                client=client,
                registry=registry,
            )
            answer = compose_answer(
                text,
                hits,
                brief,
                registry=registry,
                provider_id=body.provider,
                model=body.model,
                locale=locale,
                style_directive=style_catalog.directive(style),
            )
            answer.text = style_catalog.format_offline(
                answer.text,
                style,
                requested=(body.style or "auto") != "auto",
            )
            if freshness.get("note") and freshness["note"] not in answer.text:
                answer.text = f"{answer.text}\n{freshness['note']}"
            latency_ms = int((time.perf_counter() - started) * 1000)
            public = _public_retrieval(retrieval, accepted=row is not None, hits=hits)
            if row is not None:
                conversation_id = attach_chat_turn(
                    db,
                    session_row=row,
                    conversation_id=body.conversation_id,
                    user_text=text,
                    assistant_text=answer.text,
                    mode=body.mode,
                    sources=body.sources,
                    verify=body.verify,
                    clarify=body.clarify,
                    citations=hits,
                    provider=answer.provider,
                    model=answer.model,
                    prompt_tokens=answer.prompt_tokens,
                    completion_tokens=answer.completion_tokens,
                    latency_ms=latency_ms,
                )
                brief_id = row.id
            else:
                conversation_id = record_plain_turn(
                    db,
                    conversation_id=body.conversation_id,
                    user_text=text,
                    assistant_text=answer.text,
                    mode=body.mode,
                    sources=body.sources,
                    verify=body.verify,
                    clarify=body.clarify,
                    citations=hits,
                    provider=answer.provider,
                    model=answer.model,
                    prompt_tokens=answer.prompt_tokens,
                    completion_tokens=answer.completion_tokens,
                    latency_ms=latency_ms,
                )
                brief_id = None
            return _outcome(
                assistant=answer.text,
                citations=hits,
                provider=answer.provider,
                model=answer.model,
                prompt_tokens=answer.prompt_tokens,
                completion_tokens=answer.completion_tokens,
                latency_ms=latency_ms,
                brief_id=brief_id,
                conversation_id=conversation_id,
                brief=brief,
                retrieval=public,
                gap=answer.gap,
                freshness=freshness.get("events") or [],
                verify_ms=freshness["ms"] if freshness.get("ran") else None,
                style=style_catalog.public(style, locale),
            )
        except ClarifyError as exc:
            db.rollback()
            raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc


def _public_retrieval(retrieval: dict, *, accepted: bool, hits: list | None = None) -> dict:
    return {
        "accepted_brief": accepted,
        "brief_status": retrieval.get("brief_status"),
        "mode": retrieval.get("mode"),
        "sources": retrieval.get("sources"),
        "hits": hits if hits is not None else retrieval.get("hits") or [],
        "scope_applied": bool(retrieval.get("scope_applied")),
    }


def _outcome(**kwargs) -> dict:
    return kwargs


def _sse(event: str, payload: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"


def _deltas(text: str) -> list[str]:
    if not text:
        return [""]
    size = 48
    return [text[index : index + size] for index in range(0, len(text), size)]


def _safe_decisions(request: Request, body: ChatBody, outcome: dict | None) -> list[dict]:
    conversation_id = body.conversation_id
    brief = None
    if outcome:
        conversation_id = outcome.get("conversation_id") or conversation_id
        if isinstance(outcome.get("brief"), dict):
            brief = outcome["brief"]
    try:
        return probe_decisions(
            message=_user_text(body),
            mode=body.mode,
            clarify=body.clarify,
            verify=body.verify,
            sources=body.sources,
            conversation_id=conversation_id,
            brief=brief,
            client=getattr(request.app.state, "decision_client", None),
            link_trace=outcome is not None,
        )
    except Exception:
        logger.exception("decision probe failed")
        return [
            {
                "question_id": "route_channels",
                "qtype": "choice",
                "result": ["local"],
                "confidence": 0.0,
                "applied": False,
                "fallback": "default",
            }
        ]


@router.post("")
async def chat(request: Request) -> StreamingResponse:
    body = await _parse_body(request)
    locale = locale_from(body.locale, request.headers)
    registry = getattr(request.app.state, "registry", None)
    failed: HTTPException | None = None
    try:
        outcome = _prepare(body, registry, getattr(request.app.state, "decision_client", None), locale)
    except HTTPException as exc:
        outcome = None
        failed = exc
    decisions = _safe_decisions(request, body, outcome)
    if failed is not None:
        raise failed

    def events():
        for item in decisions:
            yield _sse("decision", item)
        if outcome is not None:
            for item in outcome.get("freshness") or []:
                yield _sse("freshness", item)
        if outcome is None:
            yield _sse("delta", {"text": t("chat.stub", locale)})
            stub_done = {
                "provider": None,
                "model": None,
                "prompt_tokens": 0,
                "completion_tokens": 0,
                "latency_ms": 0,
            }
            if locale != "zh":
                stub_done["locale"] = locale
            yield _sse("done", stub_done)
            return
        for hit in outcome["citations"]:
            yield _sse("citation", hit)
        # One delta keeps the pre-library stub and brief acknowledgements intact.
        # Grounded replies are split so the SSE stream is more than a single blob.
        pieces = [outcome["assistant"]] if not outcome["citations"] and outcome["gap"] is None else _deltas(outcome["assistant"])
        if outcome["gap"] == "not_found":
            pieces = [outcome["assistant"]]
        for piece in pieces:
            yield _sse("delta", {"text": piece})
        done = {
            "provider": outcome["provider"],
            "model": outcome["model"],
            "prompt_tokens": outcome["prompt_tokens"],
            "completion_tokens": outcome["completion_tokens"],
            "latency_ms": outcome["latency_ms"],
            "conversation_id": outcome["conversation_id"],
            "retrieval": outcome["retrieval"],
        }
        if locale != "zh":
            done["locale"] = locale
        style_public = outcome.get("style")
        if isinstance(style_public, dict) and style_public.get("id") != "general-plain":
            done["style"] = style_public
        if outcome["brief_id"] is not None:
            done["brief_id"] = outcome["brief_id"]
            done["brief"] = outcome["brief"]
        if outcome["gap"]:
            done["gap"] = outcome["gap"]
        if outcome.get("verify_ms") is not None:
            done["verify_ms"] = outcome["verify_ms"]
        yield _sse("done", done)

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
