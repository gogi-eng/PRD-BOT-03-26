"""Загрузка config.yaml с наложением data/runtime_config_overrides.yaml и подстановкой из .env."""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict, Optional

import yaml

try:
    from dotenv import load_dotenv
except ImportError:
    load_dotenv = None  # type: ignore

RUNTIME_OVERRIDES_REL_PATH = Path("data") / "runtime_config_overrides.yaml"


def deep_merge_config(base: Dict[str, Any], patch: Dict[str, Any]) -> Dict[str, Any]:
    """Глубокий merge словарей конфигурации (patch поверх base)."""
    out = dict(base)
    for key, val in (patch or {}).items():
        if isinstance(val, dict) and isinstance(out.get(key), dict):
            out[key] = deep_merge_config(out[key], val)
        else:
            out[key] = val
    return out


def runtime_overrides_path(
    root_or_cfg_path: Optional[Path] = None,
    *,
    cfg: Optional[Dict[str, Any]] = None,
) -> Path:
    """Путь к файлу рантайм-оверлеев (data/runtime_config_overrides.yaml)."""
    if isinstance(cfg, dict):
        if cfg.get("_overrides_path"):
            return Path(str(cfg["_overrides_path"]))
        if cfg.get("_root"):
            return Path(str(cfg["_root"])) / RUNTIME_OVERRIDES_REL_PATH
        if cfg.get("_config_path"):
            return Path(str(cfg["_config_path"])).parent / RUNTIME_OVERRIDES_REL_PATH
    if root_or_cfg_path is not None:
        p = Path(root_or_cfg_path)
        base_dir = p.parent if p.suffix in (".yaml", ".yml") else p
        return base_dir / RUNTIME_OVERRIDES_REL_PATH
    root = Path(__file__).resolve().parent.parent
    return root / RUNTIME_OVERRIDES_REL_PATH


def load_runtime_overrides(path: Path) -> Dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        with path.open("r", encoding="utf-8") as f:
            loaded = yaml.safe_load(f) or {}
        return loaded if isinstance(loaded, dict) else {}
    except Exception:
        return {}


def save_runtime_overrides(path: Path, overrides: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        yaml.safe_dump(overrides or {}, f, allow_unicode=True, default_flow_style=False)


def _apply_env_overlay(data: Dict[str, Any]) -> None:
    """Подставляет ключи из .env в config (не перезаписывает явно заданные в yaml)."""
    bybit = data.setdefault("bybit", {})
    tg = data.setdefault("telegram", {})
    oai = data.setdefault("openrouter", {})
    ds = data.setdefault("deepseek", {})
    fcc = data.setdefault("free_claude_code", {})
    ai = data.setdefault("ai", {})

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
    _set(bybit, "read_api_key", "BYBIT_READ_API_KEY")
    _set(bybit, "read_api_secret", "BYBIT_READ_API_SECRET")
    _set(bybit, "testnet", "TESTNET", cast=bool)
    _set(tg, "bot_token", "TELEGRAM_TOKEN")
    _set(tg, "chat_id", "TELEGRAM_CHAT_ID")
    _set(tg, "channel_id", "TELEGRAM_CHANNEL_ID")
    _set(oai, "api_key", "OPENROUTER_API_KEY")
    _set(ds, "api_key", "DEEPSEEK_API_KEY")
    _set(fcc, "auth_token", "FCC_AUTH_TOKEN")
    if os.environ.get("PRD_AI_PROVIDER", "").strip():
        ai["provider"] = os.environ.get("PRD_AI_PROVIDER", "").strip().lower()


def load_config(path: Path | None = None, *, reload: bool = False) -> Dict[str, Any]:
    default_root = Path(__file__).resolve().parent.parent
    cfg_path = Path(path) if path is not None else (default_root / "config.yaml")
    root = cfg_path.parent if path is not None else default_root
    if load_dotenv:
        env_path = root / ".env"
        if env_path.exists():
            load_dotenv(env_path, override=False)

    if not cfg_path.exists():
        example = root / "config.example.yaml"
        raise FileNotFoundError(
            f"Нет {cfg_path}. Скопируйте {example} в config.yaml и заполните ключи."
        )
    with cfg_path.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    ov_path = runtime_overrides_path(cfg_path)
    overrides = load_runtime_overrides(ov_path)
    if overrides:
        data = deep_merge_config(data, overrides)
    _apply_env_overlay(data)
    data["_root"] = str(root)
    data["_config_path"] = str(cfg_path)
    data["_overrides_path"] = str(ov_path)
    return data
