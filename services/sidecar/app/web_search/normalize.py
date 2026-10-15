"""Map a future search hit into a temporary evidence dict. Nothing is stored."""

from __future__ import annotations


def normalize_web_result(raw: dict) -> dict:
    return {
        "source_kind": "web",
        "platform": "web",
        "title": raw.get("title") or "",
        "url": raw.get("url") or "",
        "text": raw.get("snippet") or raw.get("text") or "",
    }
