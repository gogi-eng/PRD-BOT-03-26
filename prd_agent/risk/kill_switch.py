"""Файловый kill-switch: новые входы блокируются, открытые позиции ведутся дальше."""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Tuple


def _bot_root_from_cfg(cfg: Dict[str, Any]) -> Path:
    raw = str(cfg.get("_config_path") or "config.yaml")
    return Path(raw).resolve().parent


def kill_switch_path(cfg: Dict[str, Any]) -> Path | None:
    risk = cfg.get("risk") or {}
    rel = str(risk.get("kill_switch_file") or "").strip()
    if not rel:
        return None
    p = Path(rel)
    if p.is_absolute():
        return p
    return _bot_root_from_cfg(cfg) / rel


def kill_switch_active(cfg: Dict[str, Any]) -> Tuple[bool, str]:
    """
    True = блокировать новые входы.
    Файл-сентинел на диске (touch STOP_TRADING на сервере).
    """
    path = kill_switch_path(cfg)
    if path is None:
        return False, ""
    if path.is_file():
        hint = str((cfg.get("risk") or {}).get("kill_switch_hint") or path.name)
        return True, f"kill-switch: {hint} — новые входы заблокированы"
    return False, ""
