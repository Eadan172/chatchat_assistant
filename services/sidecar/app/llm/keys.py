"""API key helpers. keyring is imported only when a function runs."""

from __future__ import annotations

SERVICE = "zhihu-connect-engine"


def set_api_key(ref: str, value: str) -> None:
    raise NotImplementedError("key storage is an M0 stub")


def get_api_key(ref: str) -> str | None:
    raise NotImplementedError("key storage is an M0 stub")


def delete_api_key(ref: str) -> None:
    raise NotImplementedError("key storage is an M0 stub")
