"""Settings and provider listing. API keys are still not written."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from app.decision.shadow import load_settings, update_settings

router = APIRouter()


class DecisionSettingsIn(BaseModel):
    shadow: bool | None = None
    real_routing: bool | None = None
    confidence_floor: float | None = Field(default=None, ge=0.0, le=1.0)
    clear_confidence_floor: bool = False


@router.post("/settings/key")
def set_key() -> dict:
    return {"ok": False, "stored": False, "stub": True}


@router.get("/settings/decision")
def get_decision_settings() -> dict:
    return load_settings()


@router.post("/settings/decision")
def set_decision_settings(body: DecisionSettingsIn) -> dict:
    try:
        return update_settings(
            shadow=body.shadow,
            real_routing=body.real_routing,
            confidence_floor=body.confidence_floor,
            clear_confidence_floor=body.clear_confidence_floor,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


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
