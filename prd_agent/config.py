"""Загрузка config.yaml с подстановкой из .env (если есть)."""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict

import yaml

try:
    from dotenv import load_dotenv
except ImportError:
    load_dotenv = None  # type: ignore


def _apply_env_overlay(data: Dict[str, Any]) -> None:
    """Подставляет ключи из .env в config (не перезаписывает явно заданные в yaml)."""
    bybit = data.setdefault("bybit", {})
    tg = data.setdefault("telegram", {})

    def _set(section: dict, key: str, env_key: str, cast=None):
        if section.get(key):
            return
        val = os.environ.get(env_key, "").strip()
        if not val:
            return
        if cast is bool:
            section[key] = val.lower() in ("1", "true", "yes", "on")
        else:
            section[key] = cast(val) if cast else val

    _set(bybit, "api_key", "BYBIT_API_KEY")
    _set(bybit, "api_secret", "BYBIT_API_SECRET")
    _set(bybit, "testnet", "TESTNET", cast=bool)
    _set(tg, "bot_token", "TELEGRAM_TOKEN")
    _set(tg, "chat_id", "TELEGRAM_CHAT_ID")
    _set(tg, "channel_id", "TELEGRAM_CHANNEL_ID")


def load_config(path: Path | None = None, *, reload: bool = False) -> Dict[str, Any]:
    root = Path(__file__).resolve().parent.parent
    if load_dotenv:
        env_path = root / ".env"
        if env_path.exists():
            load_dotenv(env_path, override=False)

    cfg_path = path or (root / "config.yaml")
    if not cfg_path.exists():
        example = root / "config.example.yaml"
        raise FileNotFoundError(
            f"Нет {cfg_path}. Скопируйте {example} в config.yaml и заполните ключи."
        )
    with cfg_path.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    _apply_env_overlay(data)
    data["_root"] = str(root)
    data["_config_path"] = str(cfg_path)
    return data
