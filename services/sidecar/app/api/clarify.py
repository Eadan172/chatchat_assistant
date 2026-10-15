"""Clarifier routes: one frontier question at a time, then a confirmable brief."""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlmodel import Session

from app.clarifier.session import (
    ClarifyError,
    answer_session,
    confirm_session,
    get_active_session,
    get_session_view,
    start_session,
)
from app.db.session import get_db

router = APIRouter()


class StartIn(BaseModel):
    keyword: str = Field(min_length=1, max_length=500)
    conversation_id: int | None = None
    domain_hint: str | None = None
    skip: bool = False
    max_questions: int | None = Field(default=None, ge=1, le=7)


class AnswerIn(BaseModel):
    question_id: str = Field(min_length=1)
    action: Literal["accept", "edit", "park"]
    value: str | None = None
    park_reason: str | None = None


class ConfirmIn(BaseModel):
    skip: bool = False


def _call(action):
    try:
        return action()
    except ClarifyError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc


@router.post("/start")
def start_clarify(body: StartIn, db: Session = Depends(get_db)) -> dict:
    return _call(
        lambda: start_session(
            db,
            keyword=body.keyword,
            conversation_id=body.conversation_id,
            domain_hint=body.domain_hint,
            skip=body.skip,
            max_questions=body.max_questions,
        )
    )


@router.get("/active")
def active_clarify(db: Session = Depends(get_db)) -> dict:
    return {"session": _call(lambda: get_active_session(db))}


@router.get("/{session_id}")
def read_clarify(session_id: int, db: Session = Depends(get_db)) -> dict:
    return _call(lambda: get_session_view(db, session_id))


@router.post("/{session_id}/answer")
def answer_clarify(session_id: int, body: AnswerIn, db: Session = Depends(get_db)) -> dict:
    return _call(
        lambda: answer_session(
            db,
            session_id,
            question_id=body.question_id,
            action=body.action,
            value=body.value,
            park_reason=body.park_reason,
        )
    )


@router.post("/{session_id}/confirm")
def confirm_clarify(
    session_id: int,
    body: ConfirmIn | None = None,
    db: Session = Depends(get_db),
) -> dict:
    skip = bool(body.skip) if body else False
    return _call(lambda: confirm_session(db, session_id, skip=skip))
