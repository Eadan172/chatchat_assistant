"""Search and DeepSeek credentials.

Resolution matches ima: environment variables, then a file under DATA_DIR.
The file is not a SQLite column. Values are never returned by HTTP handlers
and must not be logged.
"""

from __future__ import annotations

import json
import os
import stat
from pathlib import Path

ENV_NAMES = {
    "deepseek": "DEEPSEEK_API_KEY",
    "serper": "SERPER_API_KEY",
    "serpapi": "SERPAPI_API_KEY",
    "tavily": "TAVILY_API_KEY",
}

FILE_FIELDS = {
    "deepseek": "deepseek_api_key",
    "serper": "serper_api_key",
    "serpapi": "serpapi_api_key",
    "tavily": "tavily_api_key",
}

NAMES = ("deepseek", "serper", "serpapi", "tavily")


def secrets_path() -> Path:
    root = Path(os.getenv("DATA_DIR") or ".")
    return root / "secrets" / "ask.json"


def secret_value(name: str) -> str | None:
    env_name = ENV_NAMES[name]
    from_env = (os.getenv(env_name) or "").strip()
    if from_env:
        return from_env
    payload = _read()
    if payload is None:
        return None
    value = str(payload.get(FILE_FIELDS[name]) or "").strip()
    return value or None


def secret_source(name: str) -> str | None:
    if (os.getenv(ENV_NAMES[name]) or "").strip():
        return "env"
    payload = _read()
    if payload and str(payload.get(FILE_FIELDS[name]) or "").strip():
        return "settings"
    return None


def public_status() -> dict:
    return {
        "configured": {name: secret_value(name) is not None for name in NAMES},
        "credential_source": {name: secret_source(name) for name in NAMES},
    }


def known_secret_values() -> list[str]:
    found: list[str] = []
    for name in NAMES:
        env_value = (os.getenv(ENV_NAMES[name]) or "").strip()
        if env_value:
            found.append(env_value)
    payload = _read() or {}
    for field in FILE_FIELDS.values():
        value = str(payload.get(field) or "").strip()
        if value and value not in found:
            found.append(value)
    return found


def update_secrets(values: dict[str, str | None]) -> dict:
    """Update only the fields present in ``values``. Empty string clears one key."""
    current = _read() or {}
    for name in NAMES:
        field = FILE_FIELDS[name]
        if field not in values:
            continue
        raw = values[field]
        if raw is None:
            continue
        text = str(raw).strip()
        if text:
            current[field] = text
        else:
            current.pop(field, None)
    if not any(str(current.get(FILE_FIELDS[name]) or "").strip() for name in NAMES):
        path = secrets_path()
        if path.is_file():
            path.unlink()
        return public_status()
    _write({field: str(current[field]) for field in FILE_FIELDS.values() if str(current.get(field) or "").strip()})
    return public_status()


def _read() -> dict | None:
    path = secrets_path()
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    return payload


def _write(payload: dict[str, str]) -> None:
    path = secrets_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        path.parent.chmod(stat.S_IRWXU)
    except OSError:
        pass
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    try:
        path.chmod(stat.S_IRUSR | stat.S_IWUSR)
    except OSError:
        pass
