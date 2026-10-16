"""Persist ClarifySession rows and restore an unfinished design tree."""

from __future__ import annotations

import os
from typing import Any

from sqlalchemy import desc
from sqlalchemy.orm.attributes import flag_modified
from sqlmodel import Session, select

from app.clarifier.design_tree import (
    DEFAULT_MAX_QUESTIONS,
    DesignTree,
    DesignTreeError,
    build_clarified_brief,
    build_tree,
    clamp_max_questions,
    skipped_brief,
)
from app.clarifier.questioner import render_question
from app.db.models import ClarifySession, Conversation, Message, utcnow


class ClarifyError(Exception):
    def __init__(self, status_code: int, detail: str) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


def start_session(
    db: Session,
    *,
    keyword: str,
    conversation_id: int | None,
    domain_hint: str | None,
    skip: bool,
    max_questions: int | None,
    ambiguities: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    text = keyword.strip()
    if not text:
        raise ClarifyError(422, "请填写关键词")
    if len(text) > 500:
        raise ClarifyError(422, "关键词过长")
    hint = (domain_hint or "").strip().lower() or None
    limit = clamp_max_questions(max_questions, fallback=_default_max_questions())
    try:
        tree = build_tree(text, hint, limit, ambiguities=ambiguities)
    except DesignTreeError as exc:
        raise ClarifyError(exc.status_code, exc.detail) from exc
    row = ClarifySession(
        conversation_id=conversation_id,
        keyword=text,
        state="active",
        tree_json=tree.to_dict(),
        brief_json=None,
    )
    db.add(row)
    db.flush()
    if skip:
        tree.park_open("跳过澄清")
        row.state = "confirmed"
        row.brief_json = skipped_brief(text, hint)
        row.tree_json = tree.to_dict()
        row.updated_at = utcnow()
        _link_conversation(db, row)
    else:
        if _present_current(tree):
            row.tree_json = tree.to_dict()
        row.updated_at = utcnow()
    flag_modified(row, "tree_json")
    if row.brief_json is not None:
        flag_modified(row, "brief_json")
    db.commit()
    db.refresh(row)
    return session_view(row)


def answer_session(
    db: Session,
    session_id: int,
    *,
    question_id: str,
    action: str,
    value: str | None,
    park_reason: str | None,
) -> dict[str, Any]:
    row = _require_row(db, session_id)
    if row.state != "active":
        raise ClarifyError(409, "这个澄清会话已经结束")
    tree = _load_tree(row)
    _present_current(tree)
    try:
        tree.apply(question_id, action, value, park_reason)
    except DesignTreeError as exc:
        raise ClarifyError(exc.status_code, exc.detail) from exc
    _present_current(tree)
    row.tree_json = tree.to_dict()
    row.updated_at = utcnow()
    flag_modified(row, "tree_json")
    db.commit()
    db.refresh(row)
    return session_view(row)


def confirm_session(db: Session, session_id: int, *, skip: bool) -> dict[str, Any]:
    row = _require_row(db, session_id)
    if row.state == "confirmed":
        return session_view(row)
    if row.state != "active":
        raise ClarifyError(409, "这个澄清会话已经结束")
    tree = _load_tree(row)
    if skip:
        tree.park_open("跳过澄清")
        row.brief_json = skipped_brief(row.keyword, tree.domain_hint)
    else:
        if not tree.is_complete():
            raise ClarifyError(409, "还有未回答的问题")
        row.brief_json = build_clarified_brief(tree)
    row.state = "confirmed"
    row.tree_json = tree.to_dict()
    row.updated_at = utcnow()
    flag_modified(row, "tree_json")
    flag_modified(row, "brief_json")
    _link_conversation(db, row)
    db.commit()
    db.refresh(row)
    return session_view(row)


def get_session_view(db: Session, session_id: int) -> dict[str, Any]:
    row = _require_row(db, session_id)
    if row.state == "active":
        tree = _load_tree(row)
        if _present_current(tree):
            row.tree_json = tree.to_dict()
            row.updated_at = utcnow()
            flag_modified(row, "tree_json")
            db.commit()
            db.refresh(row)
    return session_view(row)


def get_active_session(db: Session) -> dict[str, Any] | None:
    row = db.exec(
        select(ClarifySession)
        .where(ClarifySession.state == "active")
        .order_by(desc(ClarifySession.updated_at))
    ).first()
    if row is None:
        return None
    return get_session_view(db, int(row.id or 0))


def resolve_chat_brief(
    db: Session,
    *,
    brief_id: int | None,
    conversation_id: int | None,
    clarify: str,
    keyword: str,
    domain_hint: str | None,
) -> ClarifySession | None:
    """Load a confirmed brief, create a skipped one, or continue a linked conversation."""
    if brief_id is not None:
        return _require_confirmed(db, brief_id)
    if clarify == "skip":
        text = keyword.strip() or "（未填写）"
        view = start_session(
            db,
            keyword=text,
            conversation_id=conversation_id,
            domain_hint=domain_hint,
            skip=True,
            max_questions=_default_max_questions(),
        )
        row = _require_row(db, int(view["session_id"]))
        return row
    if conversation_id is not None:
        conversation = db.get(Conversation, conversation_id)
        if conversation and conversation.clarify_session_id:
            linked = db.get(ClarifySession, conversation.clarify_session_id)
            if linked and linked.state == "confirmed" and linked.brief_json:
                return linked
    return None


def attach_chat_turn(
    db: Session,
    *,
    session_row: ClarifySession,
    conversation_id: int | None,
    user_text: str,
    assistant_text: str,
    mode: str,
    sources: list[str] | None,
    verify: str,
    clarify: str,
    citations: list[Any] | None = None,
    provider: str | None = None,
    model: str | None = None,
    prompt_tokens: int = 0,
    completion_tokens: int = 0,
    latency_ms: int = 0,
) -> int:
    conversation = None
    if conversation_id is not None:
        conversation = db.get(Conversation, conversation_id)
    if conversation is None and session_row.conversation_id is not None:
        conversation = db.get(Conversation, session_row.conversation_id)
    if conversation is None:
        conversation = Conversation(title=(session_row.keyword or "对话")[:80], mode=mode)
        db.add(conversation)
        db.flush()
    conversation.mode = mode or conversation.mode
    conversation.clarify_session_id = session_row.id
    session_row.conversation_id = conversation.id
    brief = dict(session_row.brief_json or {})
    conversation.source_policy = {
        "sources": list(sources or ["local"]),
        "verify": verify,
        "clarify": clarify,
        "brief_status": brief.get("status"),
        "brief": brief,
    }
    flag_modified(conversation, "source_policy")
    db.add(
        Message(
            conversation_id=int(conversation.id or 0),
            role="user",
            content=user_text or session_row.keyword,
        )
    )
    stored_citations: list[Any] = [
        {
            "brief_id": session_row.id,
            "brief_status": brief.get("status"),
            "source_kind": "clarify",
        }
    ]
    if citations:
        stored_citations.extend(citations)
    db.add(
        Message(
            conversation_id=int(conversation.id or 0),
            role="assistant",
            content=assistant_text,
            citations=stored_citations,
            provider=provider,
            model=model,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            latency_ms=latency_ms,
        )
    )
    session_row.updated_at = utcnow()
    db.commit()
    db.refresh(conversation)
    return int(conversation.id or 0)


def record_plain_turn(
    db: Session,
    *,
    conversation_id: int | None,
    user_text: str,
    assistant_text: str,
    mode: str,
    sources: list[str] | None,
    verify: str,
    clarify: str,
    citations: list[Any],
    provider: str | None,
    model: str | None,
    prompt_tokens: int,
    completion_tokens: int,
    latency_ms: int,
) -> int:
    """Store a library Q&A turn that has no clarification session."""
    conversation = db.get(Conversation, conversation_id) if conversation_id is not None else None
    if conversation is None:
        title = (user_text or "对话").strip()[:80] or "对话"
        conversation = Conversation(title=title, mode=mode or "personal")
        db.add(conversation)
        db.flush()
    conversation.mode = mode or conversation.mode
    conversation.source_policy = {
        "sources": list(sources or ["local"]),
        "verify": verify,
        "clarify": clarify,
    }
    flag_modified(conversation, "source_policy")
    db.add(Message(conversation_id=int(conversation.id or 0), role="user", content=user_text))
    db.add(
        Message(
            conversation_id=int(conversation.id or 0),
            role="assistant",
            content=assistant_text,
            citations=citations,
            provider=provider,
            model=model,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            latency_ms=latency_ms,
        )
    )
    db.commit()
    db.refresh(conversation)
    return int(conversation.id or 0)


def session_view(row: ClarifySession) -> dict[str, Any]:
    tree = _load_tree(row)
    state = _api_state(row, tree)
    question = None
    if state == "active":
        current = tree.current()
        if current is not None:
            question = current.public_question()
    brief = None
    brief_id = None
    if row.state == "confirmed":
        brief = row.brief_json
        brief_id = row.id
    elif state == "ready_to_confirm":
        brief = build_clarified_brief(tree)
    return {
        "session_id": row.id,
        "brief_id": brief_id,
        "conversation_id": row.conversation_id,
        "keyword": row.keyword,
        "state": state,
        "question": question,
        "tree_progress": tree.progress(),
        "brief": brief,
    }


def _default_max_questions() -> int:
    raw = os.getenv("CLARIFY_MAX_QUESTIONS", str(DEFAULT_MAX_QUESTIONS))
    try:
        value = int(raw)
    except ValueError:
        return DEFAULT_MAX_QUESTIONS
    if value < 1 or value > 7:
        return DEFAULT_MAX_QUESTIONS
    return value


def _require_row(db: Session, session_id: int) -> ClarifySession:
    row = db.get(ClarifySession, session_id)
    if row is None:
        raise ClarifyError(404, "未找到澄清会话")
    return row


def _require_confirmed(db: Session, session_id: int) -> ClarifySession:
    row = _require_row(db, session_id)
    if row.state != "confirmed" or not row.brief_json:
        raise ClarifyError(409, "澄清结果尚未确认")
    return row


def _load_tree(row: ClarifySession) -> DesignTree:
    raw = row.tree_json
    if isinstance(raw, str):
        import json

        raw = json.loads(raw)
    try:
        return DesignTree.from_dict(raw or {})
    except ValueError as exc:
        raise ClarifyError(409, "澄清会话里没有设计树") from exc


def _api_state(row: ClarifySession, tree: DesignTree) -> str:
    if row.state in {"confirmed", "aborted"}:
        return row.state
    if tree.is_complete():
        return "ready_to_confirm"
    return "active"


def _present_current(tree: DesignTree) -> bool:
    current = tree.current()
    if current is None:
        return False
    if (
        current.presented_text
        and current.presented_reason
        and current.presented_recommendation is not None
    ):
        return False
    text, recommendation, reason = render_question(
        node_id=current.id,
        keyword=tree.keyword,
        domain_hint=tree.domain_hint,
        text=current.text,
        recommendation=current.recommendation,
        reason=current.reason,
    )
    current.presented_text = text
    current.presented_recommendation = recommendation
    current.presented_reason = reason
    return True


def _link_conversation(db: Session, row: ClarifySession) -> None:
    conversation = None
    if row.conversation_id is not None:
        conversation = db.get(Conversation, row.conversation_id)
    if conversation is None:
        conversation = Conversation(title=row.keyword[:80], mode="personal", clarify_session_id=row.id)
        db.add(conversation)
        db.flush()
        row.conversation_id = conversation.id
    conversation.clarify_session_id = row.id
