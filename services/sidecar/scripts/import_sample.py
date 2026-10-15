#!/usr/bin/env python3
"""Index the bundled M3 sample into the local library.

Uses the sidecar HTTP API when SIDECAR_URL is set. Otherwise it writes straight
into DATA_DIR (default: a ``.data`` directory at the repo root).
"""

from __future__ import annotations

import base64
import json
import os
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
SAMPLE = ROOT / "samples" / "m3" / "qingjiao-notes.md"
SIDECAR = ROOT / "services" / "sidecar"


def _via_http(url: str, token: str, filename: str, data: bytes) -> dict:
    body = json.dumps(
        {"filename": filename, "content_base64": base64.b64encode(data).decode("ascii")}
    ).encode("utf-8")
    request = urllib.request.Request(
        url.rstrip("/") + "/library/import",
        data=body,
        headers={"content-type": "application/json", "x-sidecar-token": token},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=60) as response:
        return json.loads(response.read().decode("utf-8"))


def _direct(data_dir: Path, filename: str, data: bytes) -> dict:
    os.environ["DATA_DIR"] = str(data_dir)
    sys.path.insert(0, str(SIDECAR))
    from app.db.session import init_db, open_session
    from app.ingest.local_file import import_payload

    init_db(str(data_dir))
    with open_session() as db:
        return import_payload(db, filename=filename, data=data)


def main() -> None:
    if not SAMPLE.is_file():
        raise SystemExit(f"missing sample: {SAMPLE}")
    data = SAMPLE.read_bytes()
    base = os.getenv("SIDECAR_URL")
    if base:
        payload = _via_http(base, os.getenv("SIDECAR_TOKEN") or "", SAMPLE.name, data)
    else:
        data_dir = Path(os.getenv("DATA_DIR") or (ROOT / ".data"))
        payload = _direct(data_dir, SAMPLE.name, data)
    print(json.dumps(payload, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
