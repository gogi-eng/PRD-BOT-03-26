"""Token + chat_id для Telegram без зависимости от python-telegram-bot."""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

_LOADED_ENV_PATHS: set[str] = set()


def _load_dotenv_file(env_path: Path) -> None:
    key = str(env_path.resolve())
    if key in _LOADED_ENV_PATHS or not env_path.is_file():
        return
    try:
        from dotenv import load_dotenv

        load_dotenv(env_path, override=False)
        _LOADED_ENV_PATHS.add(key)
        return
    except ImportError:
        pass
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip().lstrip("\ufeff")
        if not line or line.startswith("#"):
            continue
        if line.lower().startswith("export "):
            line = line[7:].strip()
        if "=" not in line:
            continue
        k, _, val = line.partition("=")
        k = k.strip()
        val = val.strip().strip('"').strip("'")
        if k and k not in os.environ:
            os.environ[k] = val
    _LOADED_ENV_PATHS.add(key)


def _first_env(*keys: str) -> str:
    for key in keys:
        val = os.environ.get(key, "").strip()
        if val:
            return val
    return ""


def resolve_telegram(cfg: dict[str, Any] | None = None, *, root: Path | None = None) -> tuple[str, str]:
    """
    Возвращает (bot_token, chat_id).
    Порядок: os.environ (systemd EnvironmentFile) → config.yaml → .env на диске.
    """
    if root is None:
        root = Path(cfg.get("_root", "")) if cfg else Path(__file__).resolve().parents[2]
        if not str(root):
            root = Path(__file__).resolve().parents[2]
    root = root.resolve()
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
