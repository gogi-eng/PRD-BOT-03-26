"""Загрузка config.copy_mirror.yaml + ключи из .env (отдельно от trading_bot)."""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict, Tuple

import yaml

try:
    from dotenv import load_dotenv
except ImportError:
    load_dotenv = None  # type: ignore


def _root() -> Path:
    return Path(__file__).resolve().parents[2]


def load_mirror_config(path: Path | None = None) -> Dict[str, Any]:
    root = _root()
    if load_dotenv:
        env_path = root / ".env"
        if env_path.exists():
            load_dotenv(env_path, override=False)

    cfg_path = path or (root / "config.copy_mirror.yaml")
    if not cfg_path.exists():
        example = root / "config.copy_mirror.example.yaml"
        raise FileNotFoundError(
            f"Нет {cfg_path}. Скопируйте {example} → config.copy_mirror.yaml"
        )
    with cfg_path.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}

    data["_root"] = str(root)
    m = data.setdefault("copy_mirror", {})
    _overlay_env(m)
    data["bybit_source"] = _bybit_section(
        m.get("source", {}),
        "BYBIT_MIRROR_SOURCE_KEY",
        "BYBIT_MIRROR_SOURCE_SECRET",
    )
    data["bybit_target"] = _bybit_section(
        m.get("target", {}),
        "BYBIT_MIRROR_TARGET_KEY",
        "BYBIT_MIRROR_TARGET_SECRET",
    )
    tg = data.setdefault("telegram", {})
    if not tg.get("bot_token"):
        tg["bot_token"] = os.environ.get("TELEGRAM_TOKEN", "").strip()
    if not tg.get("chat_id"):
        tg["chat_id"] = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
    return data


def _overlay_env(m: Dict[str, Any]) -> None:
    if os.environ.get("BYBIT_MAIN_UID", "").strip():
        m["main_uid"] = os.environ.get("BYBIT_MAIN_UID", "").strip()
    if os.environ.get("BYBIT_SUB_UID", "").strip():
        m["sub_uid"] = os.environ.get("BYBIT_SUB_UID", "").strip()


def _bybit_section(section: Dict[str, Any], key_env: str, sec_env: str) -> Dict[str, Any]:
    return {
        "api_key": (section.get("api_key") or os.environ.get(key_env, "")).strip(),
        "api_secret": (section.get("api_secret") or os.environ.get(sec_env, "")).strip(),
        "testnet": bool(section.get("testnet", False)),
        "category": section.get("category", "linear"),
    }


def validate_credentials(cfg: Dict[str, Any]) -> Tuple[bool, str]:
    src = cfg.get("bybit_source", {})
    tgt = cfg.get("bybit_target", {})
    if not src.get("api_key") or not src.get("api_secret"):
        return False, "Нет BYBIT_MIRROR_SOURCE_KEY (ключ Copy Trading API)"
    if not tgt.get("api_key") or not tgt.get("api_secret"):
        return False, "Нет BYBIT_MIRROR_TARGET_KEY (ключ субаккаунта)"
    return True, ""
