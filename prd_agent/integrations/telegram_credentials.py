"""Token + chat_id для Telegram без зависимости от python-telegram-bot."""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

_ENV_LOADED = False


def _load_dotenv_file(env_path: Path) -> None:
    global _ENV_LOADED
    if _ENV_LOADED or not env_path.is_file():
        return
    try:
        from dotenv import load_dotenv

        load_dotenv(env_path, override=False)
        _ENV_LOADED = True
        return
    except ImportError:
        pass
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key = key.strip()
        val = val.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = val
    _ENV_LOADED = True


def _first_env(*keys: str) -> str:
    for key in keys:
        val = os.environ.get(key, "").strip()
        if val:
            return val
    return ""


def resolve_telegram(cfg: dict[str, Any] | None = None, *, root: Path | None = None) -> tuple[str, str]:
    """
    Возвращает (bot_token, chat_id).
    Порядок: config telegram → переменные окружения → .env в корне проекта.
    """
    if root is None:
        root = Path(cfg.get("_root", "")) if cfg else Path(__file__).resolve().parents[2]
        if not str(root):
            root = Path(__file__).resolve().parents[2]
    _load_dotenv_file(root / ".env")

    tg = (cfg or {}).get("telegram") or {}
    token = str(tg.get("bot_token") or "").strip()
    chat_id = str(tg.get("chat_id") or tg.get("channel_id") or "").strip()

    if not token:
        token = _first_env(
            "TELEGRAM_TOKEN",
            "TELEGRAM_BOT_TOKEN",
            "BOT_TOKEN",
            "TG_BOT_TOKEN",
        )
    if not chat_id:
        chat_id = _first_env(
            "TELEGRAM_CHAT_ID",
            "TELEGRAM_CHANNEL_ID",
            "CHAT_ID",
            "TG_CHAT_ID",
        )

    return token, chat_id
