"""UI preferences and app-level settings surfaced by the Settings screen."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Request
from pydantic import BaseModel

from app import styles as style_catalog
from app.i18n import locale_from, normalize_locale

router = APIRouter()

DEFAULTS: dict[str, Any] = {"locale": "zh", "default_style": "auto", "answer_detail": "balanced"}


def _config_path() -> Path:
    root = Path(os.getenv("DATA_DIR") or ".")
    return root / "config" / "ui.json"


def load_preferences() -> dict:
    path = _config_path()
    payload: dict[str, Any] = {}
    if path.is_file():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                payload = data
        except (OSError, json.JSONDecodeError):
            payload = {}
    merged = dict(DEFAULTS)
    merged.update({key: value for key, value in payload.items() if key in DEFAULTS})
    merged["locale"] = normalize_locale(merged.get("locale"))
    return merged


def save_preferences(values: dict[str, Any]) -> dict:
    current = load_preferences()
    for key, value in values.items():
        if key not in DEFAULTS or value is None:
            continue
        if key == "locale":
            current[key] = normalize_locale(str(value))
        else:
            current[key] = value
    path = _config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(current, ensure_ascii=False, indent=2), encoding="utf-8")
    return current


class PreferencesIn(BaseModel):
    locale: str | None = None
    default_style: str | None = None
    answer_detail: str | None = None


@router.get("/styles")
def list_styles(request: Request, locale: str | None = None) -> dict:
    code = locale_from(locale, request.headers)
    return {"styles": style_catalog.list_styles(code), "auto": True, "locale": code}


@router.get("/settings/ui")
def get_ui_settings(request: Request) -> dict:
    prefs = load_preferences()
    prefs["locale"] = locale_from(prefs.get("locale"), request.headers)
    return prefs


@router.post("/settings/ui")
def set_ui_settings(body: PreferencesIn) -> dict:
    return save_preferences(body.model_dump(exclude_unset=True))


@router.get("/settings/app")
def app_settings(request: Request) -> dict:
    data_dir = Path(os.getenv("DATA_DIR") or ".")
    db_path = data_dir / "data" / "app.db"
    size = db_path.stat().st_size if db_path.is_file() else 0
    return {
        "version": request.app.version,
        "data_dir": str(data_dir),
        "db_path": str(db_path),
        "db_bytes": size,
        "features": {
            "ask": os.getenv("FEATURE_ASK", "") in ("1", "true"),
            "ima": os.getenv("FEATURE_IMA", "") in ("1", "true"),
            "verify": os.getenv("FEATURE_VERIFY", "") in ("1", "true"),
        },
        "preferences": load_preferences(),
        "limits": {
            "fork_tokens": int(os.getenv("CONTEXT_FORK_TOKENS") or 1000000),
            "import_max_bytes": 8 * 1024 * 1024,
        },
    }
