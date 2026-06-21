"""Маскирование секретов (Telegram token и т.п.) в log-записях."""
from __future__ import annotations

import logging
import re

# https://api.telegram.org/bot<token>/method  (token = digits:secret)
_TG_API_URL_RE = re.compile(
    r"https?://api\.telegram\.org/bot\d+:[A-Za-z0-9_-]+",
    re.IGNORECASE,
)
# bot123456789:AAH... в тексте или URL
_TG_BOT_TOKEN_RE = re.compile(r"bot\d+:[A-Za-z0-9_-]+")
_REDACTED = "bot***REDACTED***"


def redact_secrets(text: str) -> str:
    if not text:
        return text
    out = _TG_API_URL_RE.sub(f"https://api.telegram.org/{_REDACTED}", text)
    out = _TG_BOT_TOKEN_RE.sub(_REDACTED, out)
    return out


class RedactSecretsFilter(logging.Filter):
    """Фильтр до форматирования — правит msg и строковые args."""

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            record.msg = redact_secrets(record.msg)
        args = record.args
        if not args:
            return True
        if isinstance(args, dict):
            record.args = {
                k: redact_secrets(v) if isinstance(v, str) else v
                for k, v in args.items()
            }
        elif isinstance(args, tuple):
            record.args = tuple(
                redact_secrets(a) if isinstance(a, str) else a for a in args
            )
        return True


class RedactingFormatter(logging.Formatter):
    """Финальная строка лога — без токенов (последний рубеж)."""

    def format(self, record: logging.LogRecord) -> str:
        return redact_secrets(super().format(record))


def harden_http_client_logging() -> None:
    """httpx/httpcore на INFO пишут полный URL с bot token — глушим до WARNING."""
    for name in ("httpx", "httpcore"):
        logging.getLogger(name).setLevel(logging.WARNING)


def attach_redaction(handler: logging.Handler) -> None:
    handler.addFilter(RedactSecretsFilter())


def redacting_formatter(fmt: str, datefmt: str | None = None) -> RedactingFormatter:
    return RedactingFormatter(fmt, datefmt=datefmt)
