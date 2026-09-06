"""
Runtime-флаги telegram_signal_agent (agent_runtime_controls в state JSON).
Единая панель в ControlBot — без второго polling и Conflict.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Tuple


def state_path(root: Path) -> Path:
    return root / "telegram_signal_agent_state.json"


# Ключи панели unified ControlBot — signal agent не должен затирать их при _save_state.
PANEL_RTC_KEYS = (
    "pause_all_execution",
    "signal_only_mode",
    "channel_auto_execute",
    "market_scanner_auto_execute",
    "trailing_user_override",
)


def _blank_rtc() -> Dict[str, Any]:
    return {
        "pause_all_execution": False,
        "channel_auto_execute": True,
        "market_scanner_auto_execute": True,
        "signal_only_mode": False,
    }


def load_runtime_controls(root: Path) -> Dict[str, Any]:
    path = state_path(root)
    if not path.exists():
        return _blank_rtc()
    try:
        with path.open("r", encoding="utf-8") as handle:
            data = json.load(handle)
    except Exception:
        return _blank_rtc()
    if not isinstance(data, dict):
        return _blank_rtc()
    rtc = data.get("agent_runtime_controls")
    if not isinstance(rtc, dict):
        rtc = _blank_rtc()
        data["agent_runtime_controls"] = rtc
    for key, default in _blank_rtc().items():
        rtc.setdefault(key, default)
    return rtc


def set_runtime_trailing_override(root: Path, enabled: bool) -> None:
    rtc = load_runtime_controls(root)
    rtc["trailing_user_override"] = bool(enabled)
    save_runtime_controls(root, rtc)


def effective_trailing_enabled(cfg: Dict[str, Any], root: Path) -> bool:
    rtc = load_runtime_controls(root)
    if "trailing_user_override" in rtc:
        return bool(rtc["trailing_user_override"])
    positions = cfg.get("positions", {}) if isinstance(cfg.get("positions"), dict) else {}
    return bool(positions.get("trailing_enabled", True))


def save_runtime_controls(root: Path, rtc: Dict[str, Any]) -> None:
    path = state_path(root)
    data: Dict[str, Any] = {}
    if path.exists():
        try:
            with path.open("r", encoding="utf-8") as handle:
                loaded = json.load(handle)
            if isinstance(loaded, dict):
                data = loaded
        except Exception:
            pass
    data["agent_runtime_controls"] = dict(rtc)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(data, handle, ensure_ascii=False, indent=2)


def toggle_runtime_flag(root: Path, key: str) -> Tuple[bool, Dict[str, Any]]:
    allowed = {
        "pause_all_execution",
        "channel_auto_execute",
        "market_scanner_auto_execute",
        "signal_only_mode",
    }
    if key not in allowed:
        raise ValueError(f"unknown runtime flag: {key}")
    rtc = load_runtime_controls(root)
    rtc[key] = not bool(rtc.get(key))
    save_runtime_controls(root, rtc)
    return bool(rtc[key]), rtc


def runtime_controls_status_text(root: Path) -> str:
    rtc = load_runtime_controls(root)
    pause = bool(rtc.get("pause_all_execution"))
    ch = bool(rtc.get("channel_auto_execute"))
    sc = bool(rtc.get("market_scanner_auto_execute"))
    sig_only = bool(rtc.get("signal_only_mode"))
    return (
        f"Пауза всех входов: <code>{'ВКЛ' if pause else 'ВЫКЛ'}</code>\n"
        f"Signal-only: <code>{'ВКЛ' if sig_only else 'ВЫКЛ'}</code>\n"
        f"Каналы→Bybit: <code>{'ВКЛ' if ch else 'ВЫКЛ'}</code>\n"
        f"Сканер→Bybit: <code>{'ВКЛ' if sc else 'ВЫКЛ'}</code>"
    )


def is_signal_only_active(cfg: Dict[str, Any], root: Path) -> bool:
    bot = cfg.get("bot", {}) if isinstance(cfg.get("bot"), dict) else {}
    if bool(bot.get("signal_only", False)):
        return True
    return bool(load_runtime_controls(root).get("signal_only_mode", False))
