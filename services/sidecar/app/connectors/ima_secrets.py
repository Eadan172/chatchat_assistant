"""Local ima credentials.

Resolution order matches the official helper: environment variables, then a
local file. The file lives under DATA_DIR and is not a SQLite column. Values
are never returned by HTTP handlers and must not be logged.
"""

from __future__ import annotations

import json
import os
import stat
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class ImaCredentials:
    client_id: str
    api_key: str
    source: str  # env | settings


def secrets_path() -> Path:
    root = Path(os.getenv("DATA_DIR") or ".")
    return root / "secrets" / "ima.json"


def load_credentials(*, env_only: bool = False) -> ImaCredentials | None:
    client_id = (os.getenv("IMA_CLIENT_ID") or os.getenv("IMA_OPENAPI_CLIENTID") or "").strip()
    api_key = (os.getenv("IMA_API_KEY") or os.getenv("IMA_OPENAPI_APIKEY") or "").strip()
    if client_id and api_key:
        return ImaCredentials(client_id, api_key, "env")
    if env_only:
        return None
    return _read_file()


def credential_source() -> str | None:
    found = load_credentials()
    return found.source if found else None


def configured() -> bool:
    return load_credentials() is not None


def save_credentials(client_id: str, api_key: str) -> None:
    client = client_id.strip()
    key = api_key.strip()
    if not client or not key:
        raise ValueError("empty")
    path = secrets_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        path.parent.chmod(stat.S_IRWXU)
    except OSError:
        pass
    payload = json.dumps({"client_id": client, "api_key": key}, ensure_ascii=False)
    path.write_text(payload, encoding="utf-8")
    try:
        path.chmod(stat.S_IRUSR | stat.S_IWUSR)
    except OSError:
        pass


def clear_saved_credentials() -> bool:
    path = secrets_path()
    if not path.is_file():
        return False
    path.unlink()
    return True


def _read_file() -> ImaCredentials | None:
    path = secrets_path()
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    client_id = str(payload.get("client_id") or "").strip()
    api_key = str(payload.get("api_key") or "").strip()
    if not client_id or not api_key:
        return None
    return ImaCredentials(client_id, api_key, "settings")
