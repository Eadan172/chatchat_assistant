"""Clarifier routes. Registered in M0 and intentionally unimplemented."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

router = APIRouter()


def _not_ready() -> None:
    raise HTTPException(status_code=501, detail="not implemented")


@router.post("/start")
def start_clarify() -> None:
    _not_ready()


@router.post("/{session_id}/answer")
def answer_clarify(session_id: int) -> None:
    _not_ready()


@router.post("/{session_id}/confirm")
def confirm_clarify(session_id: int) -> None:
    _not_ready()
