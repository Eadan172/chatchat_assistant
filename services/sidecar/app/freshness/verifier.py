"""fresh / stale / unknown / verifying.

Local files re-read ``source_path``. A changed hash or a newer mtime becomes
``stale`` and asks the user before re-ingest. A missing path becomes
``unknown``.

Imported ima rows (platform ``ima``) are re-fetched through the official API
and compared with ``content_hash``. A matching hash is ``fresh``, a different
hash is ``stale``, and a failed re-fetch is ``unknown``. Other connectors and
web pages stay ``unknown`` without a network call.
"""

from __future__ import annotations

import logging
import threading
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from sqlmodel import Session

from app.db.models import ContentItem, VerifyJob, utcnow
from app.db.session import open_session
from app.freshness.hasher import content_hash, file_mtime
from app.freshness.policies import debounce_seconds
from app.ingest.local_file import MAX_BYTES, extract_text, reingest_local

logger = logging.getLogger(__name__)

_LOCK = threading.Lock()
_INFLIGHT: dict[int, int] = {}
_EVENTS: dict[int, threading.Event] = {}

_REMOTE_KINDS = {"web", "connector"}


class VerifyRequestError(Exception):
    def __init__(self, status_code: int, detail: str) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


def read_source_bytes(path: Path) -> bytes:
    """Read the whole source. Tests patch this to watch the verifying state."""
    return path.read_bytes()


def run_verify(item_ids: list[int], *, mode: str = "hash_mtime", registry=None) -> int:
    """Check the items and return the job id. Concurrent calls share one job."""
    ids = _ids(item_ids)
    if mode not in {"hash_mtime", "refetch"}:
        raise VerifyRequestError(422, "mode must be hash_mtime or refetch")
    if not ids:
        raise VerifyRequestError(400, "item_ids required")

    with _LOCK:
        existing = _covering(ids)
        if existing is not None:
            event = _EVENTS.get(existing)
            job_id = existing
            joined = True
        else:
            job_id = _create_job(ids, mode)
            event = threading.Event()
            _EVENTS[job_id] = event
            for item_id in ids:
                _INFLIGHT[item_id] = job_id
            joined = False
    if joined:
        if event is not None:
            event.wait(timeout=60)
        return job_id
    try:
        _execute(job_id, ids, mode, registry)
    finally:
        with _LOCK:
            for item_id in ids:
                if _INFLIGHT.get(item_id) == job_id:
                    _INFLIGHT.pop(item_id, None)
            event.set()
            _EVENTS.pop(job_id, None)
    return job_id


def job_snapshot(job_id: int) -> dict[str, Any] | None:
    with open_session() as db:
        job = db.get(VerifyJob, job_id)
        if job is None:
            return None
        return _public_job(job)


def _ids(item_ids: list[int]) -> list[int]:
    found: list[int] = []
    for raw in item_ids:
        if isinstance(raw, bool) or not isinstance(raw, int):
            continue
        if raw not in found:
            found.append(raw)
    return found


def _covering(ids: list[int]) -> int | None:
    jobs = { _INFLIGHT.get(item_id) for item_id in ids }
    jobs.discard(None)
    if len(jobs) == 1 and all(item_id in _INFLIGHT for item_id in ids):
        return next(iter(jobs))
    return None


def _create_job(ids: list[int], mode: str) -> int:
    with open_session() as db:
        job = VerifyJob(item_ids=ids, state="pending", stats={"mode": mode, "items": []})
        db.add(job)
        db.commit()
        db.refresh(job)
        return int(job.id or 0)


def _execute(job_id: int, ids: list[int], mode: str, registry) -> None:
    with open_session() as db:
        job = db.get(VerifyJob, job_id)
        if job is not None:
            job.state = "running"
            db.add(job)
            db.commit()
    results: list[dict[str, Any]] = []
    failed = False
    for item_id in ids:
        try:
            results.append(_verify_one(item_id, mode=mode, registry=registry))
        except Exception as exc:
            logger.exception("verify item %s failed", item_id)
            failed = True
            _force_unknown(item_id, "error")
            results.append(_row(item_id, "unknown", "error", "none", detail=str(exc)[:200]))
    with open_session() as db:
        job = db.get(VerifyJob, job_id)
        if job is None:
            return
        job.state = "failed" if failed else "done"
        job.stats = {"mode": mode, "items": results}
        job.finished_at = utcnow()
        db.add(job)
        db.commit()


def _verify_one(item_id: int, *, mode: str, registry) -> dict[str, Any]:
    with open_session() as db:
        item = db.get(ContentItem, item_id)
        if item is None:
            return _row(item_id, "unknown", "missing_item", "none")
        ima = _is_ima(item)
    if ima:
        return _verify_ima(item_id, mode=mode, registry=registry)
    with open_session() as db:
        item = db.get(ContentItem, item_id)
        if item is None:
            return _row(item_id, "unknown", "missing_item", "none")
        deferred = _deferred(item)
        if deferred:
            _save_state(item, "unknown", deferred, fetched=False)
            db.add(item)
            db.commit()
            return _row(item_id, "unknown", deferred, deferred if deferred.startswith("deferred") else "none")
        path = _source_path(item)
        if path is None:
            _save_state(item, "unknown", "missing", fetched=False)
            db.add(item)
            db.commit()
            return _row(item_id, "unknown", "missing", "none")
        if mode == "hash_mtime" and _debounced(item, path):
            state = item.freshness_state if item.freshness_state in {"fresh", "stale", "unknown"} else "unknown"
            return _row(
                item_id,
                state,
                item.freshness_reason or "debounced",
                "debounced",
                prompt_update=state == "stale",
                source_path=str(path),
            )
        item.freshness_state = "verifying"
        item.freshness_reason = "verifying"
        db.add(item)
        db.commit()
        if mode == "refetch":
            return _refetch(db, item, path, registry)
        return _compare(db, item, path)


def _refetch(db: Session, item: ContentItem, path: Path, registry) -> dict[str, Any]:
    item_id = int(item.id or 0)
    if not path.is_file():
        _save_state(item, "unknown", "missing", fetched=False)
        db.add(item)
        db.commit()
        return _row(item_id, "unknown", "missing", "none", source_path=str(path))
    try:
        data = read_source_bytes(path)
        if not data or len(data) > MAX_BYTES:
            raise OSError("unreadable source")
        reingest_local(db, item, data, source=path, registry=registry)
    except OSError as exc:
        logger.info("refetch could not read %s: %s", path, exc)
        _save_state(item, "unknown", "unreadable", fetched=False)
        db.add(item)
        db.commit()
        return _row(item_id, "unknown", "unreadable", "none", source_path=str(path))
    except Exception as exc:
        logger.exception("refetch failed for %s", item_id)
        _save_state(item, "unknown", "unreadable", fetched=False)
        db.add(item)
        db.commit()
        return _row(item_id, "unknown", "unreadable", "none", source_path=str(path), detail=str(exc)[:200])
    db.refresh(item)
    return _row(item_id, item.freshness_state or "fresh", item.freshness_reason or "updated", "updated", source_path=str(path))


def _compare(db: Session, item: ContentItem, path: Path) -> dict[str, Any]:
    item_id = int(item.id or 0)
    if not path.is_file():
        _save_state(item, "unknown", "missing", fetched=False)
        db.add(item)
        db.commit()
        return _row(item_id, "unknown", "missing", "none", source_path=str(path))
    try:
        data = read_source_bytes(path)
        if len(data) > MAX_BYTES:
            raise OSError("source is too large")
        name = (item.metrics or {}).get("filename") or item.source_id or path.name
        body = extract_text(str(name), data).strip()
    except Exception as exc:
        logger.info("verify could not read %s: %s", path, exc)
        _save_state(item, "unknown", "unreadable", fetched=False)
        db.add(item)
        db.commit()
        return _row(item_id, "unknown", "unreadable", "none", source_path=str(path))
    title = item.title or Path(str((item.metrics or {}).get("filename") or item.source_id or path.name)).stem
    digest = content_hash(title, body)
    current_mtime = file_mtime(path)
    now = utcnow()
    item.fetched_at = now
    item.verified_at = now
    if digest != (item.content_hash or ""):
        item.freshness_state = "stale"
        item.freshness_reason = "hash_changed"
        action = "prompt_update"
        prompt = True
    elif _mtime_newer(current_mtime, item.source_mtime):
        item.freshness_state = "stale"
        item.freshness_reason = "mtime_changed"
        action = "prompt_update"
        prompt = True
    else:
        item.freshness_state = "fresh"
        item.freshness_reason = "matched"
        item.source_mtime = current_mtime or item.source_mtime
        action = "none"
        prompt = False
    db.add(item)
    db.commit()
    return _row(
        item_id,
        item.freshness_state,
        item.freshness_reason or "",
        action,
        prompt_update=prompt,
        source_path=str(path),
        content_hash=item.content_hash,
    )


def _debounced(item: ContentItem, path: Path) -> bool:
    verified = _as_dt(item.verified_at)
    if verified is None or item.freshness_state not in {"fresh", "stale", "unknown"}:
        return False
    age = utcnow() - verified
    if age < timedelta(0) or age.total_seconds() >= debounce_seconds():
        return False
    if not path.is_file():
        return False
    current = file_mtime(path)
    stored = _as_dt(item.source_mtime)
    if current is None or stored is None:
        return False
    if _mtime_newer(current, stored):
        return False
    return True


def _mtime_newer(current: datetime | None, stored: datetime | None) -> bool:
    left = _as_dt(current)
    right = _as_dt(stored)
    if left is None or right is None:
        return False
    return left - right > timedelta(seconds=1)


def _is_ima(item: ContentItem) -> bool:
    if (item.platform or "") == "ima":
        return True
    metrics = item.metrics or {}
    return metrics.get("source") == "ima"


def _verify_ima(item_id: int, *, mode: str, registry) -> dict[str, Any]:
    with open_session() as db:
        item = db.get(ContentItem, item_id)
        if item is None:
            return _row(item_id, "unknown", "missing_item", "none")
        if mode == "hash_mtime" and _debounced_remote(item):
            state = item.freshness_state if item.freshness_state in {"fresh", "stale", "unknown"} else "unknown"
            return _row(item_id, state, item.freshness_reason or "debounced", "debounced", prompt_update=state == "stale")
        item.freshness_state = "verifying"
        item.freshness_reason = "verifying"
        db.add(item)
        db.commit()
        metrics = dict(item.metrics or {})
        title = item.title or ""
    try:
        from app.connectors.ima_service import fetch_imported_body, open_ima_client

        with open_ima_client() as client:
            body = fetch_imported_body(client, metrics, title=title)
    except Exception as exc:
        reason = _ima_unknown_reason(exc)
        logger.info("ima verify item %s unavailable (%s)", item_id, reason)
        _force_unknown(item_id, reason)
        return _row(item_id, "unknown", reason, "none")
    with open_session() as db:
        item = db.get(ContentItem, item_id)
        if item is None:
            return _row(item_id, "unknown", "missing_item", "none")
        if mode == "refetch":
            return _refetch_ima(db, item, body, registry)
        return _compare_ima(db, item, body)


def _compare_ima(db: Session, item: ContentItem, body: str) -> dict[str, Any]:
    item_id = int(item.id or 0)
    text = (body or "").strip()
    if not text:
        _save_state(item, "unknown", "empty", fetched=False)
        db.add(item)
        db.commit()
        return _row(item_id, "unknown", "empty", "none")
    title = item.title or ""
    digest = content_hash(title, text)
    now = utcnow()
    item.fetched_at = now
    item.verified_at = now
    if digest != (item.content_hash or ""):
        item.freshness_state = "stale"
        item.freshness_reason = "hash_changed"
        action = "prompt_update"
        prompt = True
    else:
        item.freshness_state = "fresh"
        item.freshness_reason = "matched"
        action = "none"
        prompt = False
    db.add(item)
    db.commit()
    return _row(item_id, item.freshness_state, item.freshness_reason or "", action, prompt_update=prompt, content_hash=item.content_hash)


def _refetch_ima(db: Session, item: ContentItem, body: str, registry) -> dict[str, Any]:
    from app.ingest.local_file import reingest_text

    item_id = int(item.id or 0)
    try:
        reingest_text(db, item, body, registry=registry)
    except Exception as exc:
        logger.info("ima refetch failed for %s", item_id)
        _save_state(item, "unknown", "unreadable", fetched=False)
        db.add(item)
        db.commit()
        return _row(item_id, "unknown", "unreadable", "none", detail=str(exc)[:200])
    db.refresh(item)
    return _row(item_id, item.freshness_state or "fresh", item.freshness_reason or "updated", "updated")


def _debounced_remote(item: ContentItem) -> bool:
    verified = _as_dt(item.verified_at)
    if verified is None or item.freshness_state not in {"fresh", "stale", "unknown"}:
        return False
    age = utcnow() - verified
    if age < timedelta(0) or age.total_seconds() >= debounce_seconds():
        return False
    return True


def _ima_unknown_reason(exc: Exception) -> str:
    kind = getattr(exc, "kind", "") or ""
    if kind in {"missing_key", "bad_key", "unreachable", "rate_limit"}:
        return "unreachable"
    return "unreachable"


def _deferred(item: ContentItem) -> str | None:
    kind = item.source_kind or ""
    if kind == "web":
        return "deferred_web"
    if kind == "connector":
        return "deferred_connector"
    path = _source_path(item)
    if path is None and _looks_remote(item.url):
        return "deferred_web"
    return None


def _source_path(item: ContentItem) -> Path | None:
    metrics = item.metrics or {}
    candidates = [item.source_path, metrics.get("source_path"), item.url]
    for candidate in candidates:
        if not isinstance(candidate, str) or not candidate.strip():
            continue
        if _looks_remote(candidate):
            continue
        return Path(candidate)
    return None


def _looks_remote(value: str | None) -> bool:
    if not value:
        return False
    lowered = value.lower()
    return lowered.startswith("http://") or lowered.startswith("https://")


def _save_state(item: ContentItem, state: str, reason: str, *, fetched: bool) -> None:
    now = utcnow()
    item.freshness_state = state
    item.freshness_reason = reason
    item.verified_at = now
    if fetched:
        item.fetched_at = now


def _force_unknown(item_id: int, reason: str) -> None:
    try:
        with open_session() as db:
            item = db.get(ContentItem, item_id)
            if item is None:
                return
            _save_state(item, "unknown", reason, fetched=False)
            db.add(item)
            db.commit()
    except Exception:
        logger.exception("could not mark item %s unknown", item_id)


def _as_dt(value: datetime | str | None) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.replace(tzinfo=None) if value.tzinfo else value
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value)
        except ValueError:
            return None
        return parsed.replace(tzinfo=None) if parsed.tzinfo else parsed
    return None


def _row(
    item_id: int,
    state: str,
    reason: str,
    action: str,
    *,
    prompt_update: bool | None = None,
    source_path: str | None = None,
    content_hash_value: str | None = None,
    content_hash: str | None = None,
    detail: str | None = None,
) -> dict[str, Any]:
    prompt = state == "stale" if prompt_update is None else prompt_update
    payload: dict[str, Any] = {
        "item_id": item_id,
        "state": state,
        "reason": reason,
        "action": action,
        "prompt_update": prompt,
    }
    if source_path:
        payload["source_path"] = source_path
    digest = content_hash_value if content_hash_value is not None else content_hash
    if digest:
        payload["content_hash"] = digest
    if detail:
        payload["detail"] = detail
    return payload


def _public_job(job: VerifyJob) -> dict[str, Any]:
    return {
        "job_id": job.id,
        "state": job.state,
        "item_ids": list(job.item_ids or []),
        "stats": job.stats or {},
        "created_at": job.created_at.isoformat(timespec="seconds") if job.created_at else None,
        "finished_at": job.finished_at.isoformat(timespec="seconds") if job.finished_at else None,
    }
