"""Freshness verification routes. Registered in M0 and intentionally unimplemented."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

router = APIRouter()


@router.post("")
def start_verify() -> None:
    raise HTTPException(status_code=501, detail="not implemented")


@router.get("/{job_id}")
def get_verify(job_id: int) -> None:
    raise HTTPException(status_code=501, detail="not implemented")
