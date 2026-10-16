"""When a chat turn should verify local documents.

The flag defaults off. While it is off, chat keeps the M2 shadow behaviour:
`needs_verify` is logged and nothing is re-read. Web re-fetch is not a policy
in this module; a later milestone owns live pages.

Mainland China note: this path does not call a model. If a later remote
decider is wired for the same question, prefer DeepSeek's OpenAI-compatible
endpoint (https://api.deepseek.com). Gemini and api.openai.com time out from
the mainland and should not be the default.
"""

from __future__ import annotations

import os
import time
from typing import Any

from app.decision.client import DecisionClient, DecisionUnavailable, build_decision_client
from app.decision.policies import needs_verify

SKIP_NOTE = "（本次未查证来源时效。）"
STALE_NOTE = "（部分引用可能已过期。可以在知识库里选择更新。）"
UNKNOWN_NOTE = "（有引用来源无法核实时效。）"
FRESH_NOTE = "（已查证：引用与本地文件一致。）"


def verify_enabled() -> bool:
    """True only when FEATURE_VERIFY or VITE_FEATURE_VERIFY is explicitly on."""
    raw = (os.getenv("FEATURE_VERIFY") or os.getenv("VITE_FEATURE_VERIFY") or "").strip().lower()
    return raw in {"1", "true", "yes", "on"}


def debounce_seconds() -> float:
    raw = (os.getenv("VERIFY_DEBOUNCE_S") or "30").strip()
    try:
        value = float(raw)
    except ValueError:
        return 30.0
    if value < 0:
        return 0.0
    return value


def apply_chat_freshness(
    *,
    message: str,
    verify: str,
    hits: list[dict[str, Any]],
    client: DecisionClient | None = None,
    registry=None,
) -> dict[str, Any]:
    """Maybe re-check hit documents. Never raises. Does not write decision logs.

    Shadow `applied` stays whatever M2 computed. This function only reads the
    same `needs_verify` policy when the flag is on and `verify` is `auto`.
    """
    quiet = {"ran": False, "skipped": False, "events": [], "note": None, "ms": 0}
    if not verify_enabled():
        return quiet
    mode = verify or "auto"
    if mode == "skip":
        return {"ran": False, "skipped": True, "events": [], "note": SKIP_NOTE, "ms": 0}
    if mode != "force" and not _wants_verify(message, mode, client):
        return quiet
    item_ids = _item_ids(hits)
    if not item_ids:
        return quiet
    started = time.perf_counter()
    try:
        from app.freshness.verifier import job_snapshot, run_verify

        job_id = run_verify(item_ids, mode="hash_mtime", registry=registry)

        snapshot = job_snapshot(job_id) or {}
    except Exception:
        return quiet
    rows = list((snapshot.get("stats") or {}).get("items") or [])
    by_id = {row.get("item_id"): row for row in rows if isinstance(row, dict)}
    for hit in hits:
        row = by_id.get(hit.get("item_id"))
        if isinstance(row, dict) and row.get("state"):
            hit["freshness_state"] = row["state"]
    events = [_event(row, job_id) for row in rows if isinstance(row, dict) and row.get("item_id") is not None]
    return {
        "ran": True,
        "skipped": False,
        "events": events,
        "note": _note(events),
        "ms": int((time.perf_counter() - started) * 1000),
        "job_id": job_id,
    }


def _wants_verify(message: str, verify: str, client: DecisionClient | None) -> bool:
    active = client
    if active is None:
        try:
            active = build_decision_client("rule")
        except DecisionUnavailable:
            return False
    context = {
        "text": message or "",
        "verify": verify or "auto",
        "mode": "personal",
        "clarify": "auto",
        "has_brief": False,
    }
    try:
        return bool(needs_verify(active, context).result)
    except Exception:
        return False


def _item_ids(hits: list[dict[str, Any]]) -> list[int]:
    found: list[int] = []
    for hit in hits:
        item_id = hit.get("item_id")
        if isinstance(item_id, bool) or not isinstance(item_id, int):
            continue
        if item_id not in found:
            found.append(item_id)
    return found


def _event(row: dict[str, Any], job_id: int) -> dict[str, Any]:
    return {
        "item_id": row.get("item_id"),
        "state": row.get("state"),
        "action": row.get("action"),
        "reason": row.get("reason"),
        "prompt_update": bool(row.get("prompt_update")),
        "job_id": job_id,
    }


def _note(events: list[dict[str, Any]]) -> str | None:
    states = {str(event.get("state") or "") for event in events}
    states.discard("")
    if not states:
        return None
    parts: list[str] = []
    if "stale" in states:
        parts.append(STALE_NOTE)
    if "unknown" in states:
        parts.append(UNKNOWN_NOTE)
    if states <= {"fresh"}:
        parts.append(FRESH_NOTE)
    return "\n".join(parts) if parts else None
