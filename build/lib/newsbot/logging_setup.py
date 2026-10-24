"""Logging mit automatischer Maskierung von Zugangsdaten."""

from __future__ import annotations

import logging
import re
import sys

_PATTERNS = [
    re.compile(r"(?<!\d)\d{6,12}:[A-Za-z0-9_-]{25,}"),        # Telegram-Bot-Token
    re.compile(r"sk-ant-[A-Za-z0-9_\-]{10,}"),              # Anthropic-Schlüssel
    re.compile(r"(?i)(api[_-]?key|token|secret)=([^&\s]+)"),
]


def redact(text: str) -> str:
    for pat in _PATTERNS:
        if pat.groups >= 2:
            text = pat.sub(lambda m: f"{m.group(1)}=***", text)
        else:
            text = pat.sub("***", text)
    return text


class RedactingFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        msg = record.getMessage()
        record.msg = redact(msg)
        record.args = ()
        if record.exc_info and record.exc_info[1] is not None:
            record.exc_text = redact(logging.Formatter().formatException(record.exc_info))
            record.exc_info = None
        return True


def setup_logging(level: str = "INFO") -> None:
    root = logging.getLogger()
    root.handlers.clear()
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    handler.addFilter(RedactingFilter())
    root.addHandler(handler)
    root.setLevel(level.upper())
    # httpx loggt sonst vollständige URLs, die bei Telegram das Token enthalten.
    for noisy in ("httpx", "httpcore", "anthropic"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
