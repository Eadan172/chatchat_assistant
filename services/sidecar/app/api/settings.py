"""Settings and provider listing.

LLM provider keys are still not written. ima credentials, when the feature
flag is on, are stored in a local file outside SQLite.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from app.api.connectors import ima_enabled
from app.ask.flags import ask_enabled
from app.ask.secrets import public_status, update_secrets
from app.connectors.ima_client import BAD_KEY_MESSAGE, MISSING_KEY_MESSAGE, PATH_SEARCH_KNOWLEDGE_BASE, ImaClient, ImaError
from app.connectors.ima_secrets import clear_saved_credentials, configured, credential_source, save_credentials
from app.decision.shadow import load_settings, update_settings

router = APIRouter()
logger = logging.getLogger(__name__)


class DecisionSettingsIn(BaseModel):
    shadow: bool | None = None
    real_routing: bool | None = None
    confidence_floor: float | None = Field(default=None, ge=0.0, le=1.0)
    clear_confidence_floor: bool = False


@router.post("/settings/key")
def set_key() -> dict:
    return {"ok": False, "stored": False, "stub": True}


class ImaSettingsIn(BaseModel):
    client_id: str = ""
    api_key: str = ""


def _ima_guard() -> None:
    if not ima_enabled():
        raise HTTPException(status_code=501, detail="not implemented")


@router.get("/settings/ima")
def get_ima_settings() -> dict:
    _ima_guard()
    return {"configured": configured(), "credential_source": credential_source()}


@router.post("/settings/ima")
def set_ima_settings(body: ImaSettingsIn) -> dict:
    _ima_guard()
    client_id = body.client_id.strip()
    api_key = body.api_key.strip()
    if not client_id or not api_key:
        raise HTTPException(status_code=400, detail=MISSING_KEY_MESSAGE)
    client = ImaClient(client_id, api_key)
    try:
        client.post(PATH_SEARCH_KNOWLEDGE_BASE, {"query": "", "cursor": "", "limit": 1})
    except ImaError as exc:
        status = {"missing_key": 400, "bad_key": 401, "rate_limit": 429, "unreachable": 502, "api": 502}.get(exc.kind, 502)
        detail = BAD_KEY_MESSAGE if exc.kind == "bad_key" else exc.message
        raise HTTPException(status_code=status, detail=detail) from exc
    finally:
        client.close()
    try:
        save_credentials(client_id, api_key)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=MISSING_KEY_MESSAGE) from exc
    return {"ok": True, "configured": True, "credential_source": credential_source()}


@router.delete("/settings/ima")
def delete_ima_settings() -> dict:
    _ima_guard()
    clear_saved_credentials()
    return {"ok": True, "configured": configured(), "credential_source": credential_source()}


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


class AskSettingsIn(BaseModel):
    deepseek_api_key: str | None = None
    serper_api_key: str | None = None
    serpapi_api_key: str | None = None
    tavily_api_key: str | None = None


def _ask_guard() -> None:
    if not ask_enabled():
        raise HTTPException(status_code=501, detail="not implemented")


@router.get("/settings/ask")
def get_ask_settings() -> dict:
    _ask_guard()
    return public_status()


@router.post("/settings/ask")
def set_ask_settings(body: AskSettingsIn) -> dict:
    _ask_guard()
    provided = body.model_dump(exclude_unset=True)
    saved = update_secrets(provided)
    logger.info("ask secrets updated")
    return saved


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
