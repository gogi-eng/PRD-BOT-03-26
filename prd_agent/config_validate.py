"""Семантическая проверка config.yaml (типы и диапазоны)."""
from __future__ import annotations

from typing import Any, Dict, List, Tuple, Union

Number = Union[int, float]


def _section(data: Dict[str, Any], name: str) -> Dict[str, Any]:
    block = data.get(name)
    return block if isinstance(block, dict) else {}


def _require_section(errors: List[str], data: Dict[str, Any], name: str) -> Dict[str, Any]:
    block = _section(data, name)
    if not block:
        errors.append(f"Отсутствует или пустая секция: {name}")
    return block


def _num(
    errors: List[str],
    block: Dict[str, Any],
    key: str,
    *,
    lo: float,
    hi: float,
    path: str,
    integer: bool = False,
) -> None:
    if key not in block:
        return
    raw = block.get(key)
    if isinstance(raw, bool):
        errors.append(f"{path}.{key}: ожидалось число, получен bool")
        return
    try:
        val = float(raw)
    except (TypeError, ValueError):
        errors.append(f"{path}.{key}: должно быть числом, сейчас {raw!r}")
        return
    if integer and val != int(val):
        errors.append(f"{path}.{key}: должно быть целым числом")
        return
    if val < lo or val > hi:
        errors.append(f"{path}.{key}: допустимо {lo}..{hi}, сейчас {val}")


def validate_config_data(data: Dict[str, Any]) -> Tuple[bool, List[str]]:
    errors: List[str] = []
    if not isinstance(data, dict):
        return False, ["config.yaml: корень должен быть объектом (mapping)"]

    trading = _require_section(errors, data, "trading")
    risk = _require_section(errors, data, "risk")
    qg = _section(data, "quality_gate")

    _num(errors, trading, "leverage", lo=1, hi=100, path="trading", integer=True)
    _num(errors, trading, "loop_interval_sec", lo=15, hi=600, path="trading")
    _num(errors, trading, "max_positions", lo=1, hi=20, path="trading", integer=True)
    _num(errors, trading, "risk_pct_per_trade", lo=0.05, hi=5.0, path="trading")
    _num(errors, trading, "min_signal_confidence", lo=0.0, hi=1.0, path="trading")

    _num(errors, risk, "max_daily_loss_pct", lo=0.5, hi=50.0, path="risk")
    _num(errors, risk, "max_consecutive_losses", lo=1, hi=20, path="risk", integer=True)

    _num(errors, qg, "min_rr_ratio", lo=1.0, hi=5.0, path="quality_gate")
    _num(errors, qg, "min_confidence", lo=0.0, hi=1.0, path="quality_gate")

    api_cache = _section(data, "api_cache")
    _num(errors, api_cache, "price_ttl_sec", lo=1, hi=120, path="api_cache")
    _num(errors, api_cache, "klines_ttl_sec", lo=5, hi=300, path="api_cache")
    _num(errors, api_cache, "max_parallel_requests", lo=1, hi=20, path="api_cache", integer=True)

    al = _section(trading, "adaptive_loop")
    _num(errors, al, "base_sec", lo=15, hi=600, path="trading.adaptive_loop")
    _num(errors, al, "active_sec", lo=15, hi=300, path="trading.adaptive_loop")
    _num(errors, al, "idle_sec", lo=30, hi=900, path="trading.adaptive_loop")

    pos_sync = _section(data, "position_sync")
    _num(errors, pos_sync, "alert_cooldown_sec", lo=60, hi=7200, path="position_sync")

    return len(errors) == 0, errors
