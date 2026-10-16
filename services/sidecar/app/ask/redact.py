"""Drop API keys from log records. Installed on first use of the ask path."""

from __future__ import annotations

import logging
import traceback

_INSTALLED = False
_FILTER: SecretFilter | None = None
_SEEN_HANDLERS: set[int] = set()


def scrub(text: str, secrets: list[str]) -> str:
    cleaned = text or ""
    for secret in secrets:
        if secret and len(secret) >= 8 and secret in cleaned:
            cleaned = cleaned.replace(secret, "[redacted]")
    return cleaned


class SecretFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        try:
            from app.ask.secrets import known_secret_values

            secrets = known_secret_values()
        except Exception:
            return True
        if not secrets:
            return True
        record.msg = scrub(record.getMessage(), secrets)
        record.args = ()
        if record.exc_info:
            formatted = "".join(traceback.format_exception(*record.exc_info))
            record.exc_text = scrub(formatted, secrets)
            record.exc_info = None
        elif record.exc_text:
            record.exc_text = scrub(record.exc_text, secrets)
        return True


def install_redaction() -> None:
    """Attach the scrubber to the root logger and to handlers that already exist.

    A filter on the root logger does not see records created by child loggers.
    Handler filters do, which is what test capture and file logs use.
    """
    global _INSTALLED, _FILTER
    if _FILTER is None:
        _FILTER = SecretFilter()
    root = logging.getLogger()
    if not _INSTALLED:
        root.addFilter(_FILTER)
        _INSTALLED = True
    for handler in root.handlers:
        marker = id(handler)
        if marker in _SEEN_HANDLERS:
            continue
        handler.addFilter(_FILTER)
        _SEEN_HANDLERS.add(marker)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
