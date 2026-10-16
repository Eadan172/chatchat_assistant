"""Content hash and source mtime for documents already on disk.

The hash matches the importer: title, a NUL, then the extracted body, SHA-256,
first 32 hex characters. Web pages are not fetched here.
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from pathlib import Path


def content_hash(title: str, body: str) -> str:
    """Fingerprint of title plus body. Importer and verifier must share this."""
    return hashlib.sha256(f"{title or ''}\x00{body or ''}".encode("utf-8")).hexdigest()[:32]


def file_mtime(path: Path) -> datetime | None:
    """UTC mtime without tzinfo, or None when the path cannot be stat'ed."""
    try:
        stamp = path.stat().st_mtime
    except OSError:
        return None
    return datetime.fromtimestamp(stamp, timezone.utc).replace(tzinfo=None)
