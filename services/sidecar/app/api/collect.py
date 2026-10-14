"""Collect routes. M0 returns an idle stub and does not launch crawlers."""

from __future__ import annotations

from fastapi import APIRouter

router = APIRouter()


@router.post("")
def start_collect() -> dict:
    return {"job_id": 0, "state": "idle", "stub": True}


@router.get("/{job_id}")
def get_collect(job_id: int) -> dict:
    return {"job_id": job_id, "state": "idle", "stats": {}, "stub": True}
