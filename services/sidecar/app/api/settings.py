"""Settings and provider listing. Keys are not written in M0."""

from __future__ import annotations

from fastapi import APIRouter, Request

router = APIRouter()


@router.post("/settings/key")
def set_key() -> dict:
    return {"ok": False, "stored": False, "stub": True}


@router.get("/providers")
def list_providers(request: Request) -> dict:
    registry = getattr(request.app.state, "registry", None)
    if registry is None:
        return {"providers": [], "stub": True}
    return {
        "providers": [provider.public_dict() for provider in registry.enabled()],
        "defaults": registry.defaults,
        "stub": True,
    }
