"""Feature flag for the M6 ask pipeline. Default off."""

from __future__ import annotations

import os


def ask_enabled() -> bool:
    raw = (os.getenv("FEATURE_ASK") or os.getenv("VITE_FEATURE_ASK") or "").strip().lower()
    return raw in {"1", "true", "yes", "on"}
