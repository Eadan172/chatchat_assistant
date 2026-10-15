"""Shadow log for decision probes.

Shadow mode writes decision_log rows and leaves chat behavior alone. Real
routing defaults off. A decider failure is stored as fallback and does not
raise to /chat.
"""

from __future__ import annotations

import json
import logging
import os
import re
import threading
from pathlib import Path
from typing import Any, Mapping

from sqlalchemy import desc, func
from sqlmodel import Session, select

from app.db.models import DecisionLog, Message
from app.db.session import open_session
from app.decision.calibrate import describe_confidence, eligible_to_apply
from app.decision.client import DecisionClient, DecisionUnavailable, build_decision_client
from app.decision.policies import POLICIES, QUESTION_VERSION, policy_defaults, policy_options

_LOCK = threading.Lock()
logger = logging.getLogger(__name__)
_SECRET_KEY = re.compile(r"(api[_-]?key|token|password|secret|authorization|credential)", re.IGNORECASE)
_SECRET_VALUE = re.compile(r"sk-[A-Za-z0-9_\-]{6,}|Bearer\s+[A-Za-z0-9._\-]{8,}", re.IGNORECASE)

_DEFAULTS: dict[str, Any] = {
    "shadow": True,
    "real_routing": False,
    "confidence_floor": None,
}


def settings_path() -> Path:
    root = Path(os.getenv("DATA_DIR") or ".")
    return root / "config" / "decision.json"


def load_settings() -> dict[str, Any]:
    path = settings_path()
    if not path.is_file():
        return dict(_DEFAULTS)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return dict(_DEFAULTS)
    if not isinstance(raw, dict):
        return dict(_DEFAULTS)
    return _normalize_settings(raw)


def update_settings(
    *,
    shadow: bool | None = None,
    real_routing: bool | None = None,
    confidence_floor: float | None = None,
    clear_confidence_floor: bool = False,
) -> dict[str, Any]:
    current = load_settings()
    if shadow is not None:
        current["shadow"] = bool(shadow)
    if real_routing is not None:
        current["real_routing"] = bool(real_routing)
    if clear_confidence_floor:
        current["confidence_floor"] = None
    elif confidence_floor is not None:
        current["confidence_floor"] = _floor(confidence_floor)
    _write_settings(current)
    return current


def redact(value: Any) -> Any:
    """Drop credential-shaped fields and token-like substrings before logging."""
    token = os.getenv("SIDECAR_TOKEN") or ""
    return _redact(value, token)


def observe_chat(
    *,
    message: str,
    mode: str,
    clarify: str,
    verify: str,
    sources: list[str] | None,
    conversation_id: int | None,
    brief: Mapping[str, Any] | None,
    client: DecisionClient | None = None,
    link_trace: bool = False,
) -> list[dict[str, Any]]:
    """Probe the decider, persist rows, and return SSE payloads. Never raises."""
    settings = load_settings()
    context = _context(message, mode, clarify, verify, sources, brief)
    try:
        active = client or build_decision_client("rule")
    except DecisionUnavailable as exc:
        return _finish(
            _failure_rows(exc, context, settings),
            conversation_id,
            fallback="llm",
            link_trace=link_trace,
        )
    except Exception as exc:
        return _finish(
            _failure_rows(exc, context, settings),
            conversation_id,
            fallback="default",
            link_trace=link_trace,
        )

    rows: list[dict[str, Any]] = []
    for policy in POLICIES:
        rows.append(_run_policy(policy, active, context, settings))
    return _finish(rows, conversation_id, fallback=None, link_trace=link_trace)


def list_logs(*, limit: int, offset: int) -> dict[str, Any]:
    bounded = min(max(int(limit), 1), 100)
    start = max(int(offset), 0)
    with open_session() as db:
        total_raw = db.exec(select(func.count(DecisionLog.id))).one()
        total = int(total_raw[0] if isinstance(total_raw, tuple) else total_raw or 0)
        statement = select(DecisionLog).order_by(desc(DecisionLog.id)).offset(start).limit(bounded)
        items = [_public_row(row) for row in db.exec(statement).all()]
    return {"items": items, "limit": bounded, "offset": start, "total": total}


def _context(
    message: str,
    mode: str,
    clarify: str,
    verify: str,
    sources: list[str] | None,
    brief: Mapping[str, Any] | None,
) -> dict[str, Any]:
    status = None
    if isinstance(brief, Mapping):
        raw_status = brief.get("status") or brief.get("brief")
        status = raw_status if isinstance(raw_status, str) else None
    return {
        "text": message or "",
        "mode": mode or "personal",
        "clarify": clarify or "auto",
        "verify": verify or "auto",
        "sources": list(sources) if sources is not None else None,
        "has_brief": isinstance(brief, Mapping),
        "brief_status": status,
    }


def _run_policy(policy, client: DecisionClient, context: Mapping[str, Any], settings: Mapping[str, Any]) -> dict[str, Any]:
    question_id = policy.__name__
    try:
        decision = policy(client, context)
    except DecisionUnavailable as exc:
        return _fallback_row(question_id, context, settings, fallback="llm", reason=str(exc))
    except Exception as exc:
        return _fallback_row(question_id, context, settings, fallback="default", reason=type(exc).__name__)
    return _success_row(decision, context, settings)


def _success_row(decision, context: Mapping[str, Any], settings: Mapping[str, Any]) -> dict[str, Any]:
    floor = settings.get("confidence_floor")
    note = describe_confidence(decision.confidence, apply_floor=floor if isinstance(floor, (int, float)) else None)
    applied = eligible_to_apply(
        note,
        shadow=bool(settings.get("shadow", True)),
        real_routing=bool(settings.get("real_routing", False)),
        decider_ok=True,
    )
    fallback = None
    if (
        not settings.get("shadow", True)
        and settings.get("real_routing", False)
        and note.apply_floor is not None
        and note.band == "below_floor"
    ):
        fallback = "llm"
        applied = False
    return {
        "question_id": decision.question_id,
        "qtype": decision.qtype,
        "result_value": decision.result,
        "confidence": note.confidence,
        "applied": applied,
        "fallback": fallback,
        "snapshot": _snapshot(context, decision.question_id),
        "calibration": note.as_dict(),
    }


def _fallback_row(
    question_id: str,
    context: Mapping[str, Any],
    settings: Mapping[str, Any],
    *,
    fallback: str,
    reason: str,
) -> dict[str, Any]:
    qtype, default = policy_defaults()[question_id]
    note = describe_confidence(0.0, apply_floor=_floor_or_none(settings))
    return {
        "question_id": question_id,
        "qtype": qtype,
        "result_value": default,
        "confidence": 0.0,
        "applied": False,
        "fallback": fallback,
        "snapshot": _snapshot(context, question_id, error=reason),
        "calibration": note.as_dict(),
    }


def _failure_rows(exc: Exception, context: Mapping[str, Any], settings: Mapping[str, Any]) -> list[dict[str, Any]]:
    fallback = "llm" if isinstance(exc, DecisionUnavailable) else "default"
    return [
        _fallback_row(question_id, context, settings, fallback=fallback, reason=str(exc) or type(exc).__name__)
        for question_id in policy_defaults()
    ]


def _finish(
    rows: list[dict[str, Any]],
    conversation_id: int | None,
    *,
    fallback: str | None,
    link_trace: bool,
) -> list[dict[str, Any]]:
    if fallback:
        for row in rows:
            row["fallback"] = fallback
            row["applied"] = False
    events = [_event_payload(row) for row in rows]
    try:
        _persist(rows, conversation_id, link_trace=link_trace)
    except Exception:
        logger.exception("decision log persist failed")
        for event in events:
            event["applied"] = False
            if event.get("fallback") is None:
                event["fallback"] = "default"
    return events


def _persist(rows: list[dict[str, Any]], conversation_id: int | None, *, link_trace: bool) -> None:
    trace_id = None
    with open_session() as db:
        for row in rows:
            record = DecisionLog(
                conversation_id=conversation_id,
                question_id=row["question_id"],
                qtype=row["qtype"],
                input_snapshot=redact(row["snapshot"]),
                result=redact({"value": row["result_value"], "calibration": row["calibration"]}),
                confidence=float(row["confidence"]),
                applied=bool(row["applied"]),
                fallback=row["fallback"],
            )
            db.add(record)
            db.flush()
            if row["question_id"] == "route_channels":
                trace_id = record.id
        if link_trace and conversation_id and trace_id:
            _stamp_message(db, conversation_id, int(trace_id))
        db.commit()


def _stamp_message(db: Session, conversation_id: int, trace_id: int) -> None:
    message = db.exec(
        select(Message)
        .where(Message.conversation_id == conversation_id, Message.role == "assistant")
        .order_by(desc(Message.id))
    ).first()
    if message is not None and message.decision_trace_id is None:
        message.decision_trace_id = trace_id


def _snapshot(context: Mapping[str, Any], question_id: str, error: str | None = None) -> dict[str, Any]:
    text = context.get("text") if isinstance(context.get("text"), str) else ""
    payload: dict[str, Any] = {
        "decider": "rule-based-placeholder",
        "question_version": QUESTION_VERSION,
        "question_id": question_id,
        "mode": context.get("mode"),
        "clarify": context.get("clarify"),
        "verify": context.get("verify"),
        "sources": context.get("sources"),
        "has_brief": bool(context.get("has_brief")),
        "brief_status": context.get("brief_status"),
        "text_chars": len(text),
        "text": text[:500],
        "options": list(policy_options(question_id)),
    }
    if error:
        payload["error"] = error[:200]
    return payload


def _event_payload(row: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "question_id": row["question_id"],
        "qtype": row["qtype"],
        "result": row["result_value"],
        "confidence": row["confidence"],
        "applied": bool(row["applied"]),
        "fallback": row["fallback"],
    }


def _public_row(row: DecisionLog) -> dict[str, Any]:
    created = row.created_at.isoformat(timespec="seconds") if row.created_at else None
    return {
        "id": row.id,
        "conversation_id": row.conversation_id,
        "question_id": row.question_id,
        "qtype": row.qtype,
        "input_snapshot": row.input_snapshot or {},
        "result": row.result or {},
        "confidence": row.confidence,
        "applied": bool(row.applied),
        "fallback": row.fallback,
        "created_at": created,
    }


def _floor_or_none(settings: Mapping[str, Any]) -> float | None:
    floor = settings.get("confidence_floor")
    if isinstance(floor, bool) or not isinstance(floor, (int, float)):
        return None
    return float(floor)


def _normalize_settings(raw: Mapping[str, Any]) -> dict[str, Any]:
    shadow = raw.get("shadow", True)
    routing = raw.get("real_routing", False)
    floor = raw.get("confidence_floor", None)
    parsed_floor = None
    if floor is not None and not isinstance(floor, bool) and isinstance(floor, (int, float)):
        parsed_floor = _floor(float(floor))
    return {
        "shadow": bool(shadow),
        "real_routing": bool(routing),
        "confidence_floor": parsed_floor,
    }


def _floor(value: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("confidence_floor must be a number")
    number = float(value)
    if number < 0.0 or number > 1.0:
        raise ValueError("confidence_floor must be between 0 and 1")
    return round(number, 4)


def _write_settings(payload: Mapping[str, Any]) -> None:
    path = settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(_normalize_settings(payload), ensure_ascii=False, indent=2)
    temporary = path.with_suffix(".json.tmp")
    with _LOCK:
        temporary.write_text(text + "\n", encoding="utf-8")
        temporary.replace(path)


def _redact(value: Any, token: str) -> Any:
    if isinstance(value, dict):
        cleaned: dict[str, Any] = {}
        for key, item in value.items():
            name = str(key)
            if _SECRET_KEY.search(name):
                cleaned[name] = "[redacted]"
            else:
                cleaned[name] = _redact(item, token)
        return cleaned
    if isinstance(value, list):
        return [_redact(item, token) for item in value]
    if isinstance(value, str):
        cleaned_text = _SECRET_VALUE.sub("[redacted]", value)
        if token and token in cleaned_text:
            cleaned_text = cleaned_text.replace(token, "[redacted]")
        return cleaned_text
    return value
