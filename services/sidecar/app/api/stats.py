"""GET /stats — zeroed counters. Usage tracking is not implemented in M0."""

from __future__ import annotations

from fastapi import APIRouter

router = APIRouter()


@router.get("")
def stats() -> dict:
    return {
        "items": 0,
        "indexed": 0,
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "stub": True,
    }
