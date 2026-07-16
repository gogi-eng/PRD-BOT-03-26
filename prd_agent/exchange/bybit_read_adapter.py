"""
Read-only доступ к Bybit для AI-монитора.

Использует отдельные ключи BYBIT_READ_API_KEY / BYBIT_READ_API_SECRET из .env.
Если read-ключи не заданы — монитор использует основной exchange (только чтение).
"""
from __future__ import annotations

from typing import Any, Dict, Optional

from prd_agent.exchange.bybit_adapter import BybitAdapter


def resolve_read_exchange_cfg(cfg: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Вернуть копию cfg с read-ключами, если они заданы."""
    bybit = cfg.get("bybit", {}) if isinstance(cfg.get("bybit"), dict) else {}
    read_key = str(bybit.get("read_api_key", "") or "").strip()
    read_secret = str(bybit.get("read_api_secret", "") or "").strip()
    if not read_key or not read_secret:
        return None
    merged = dict(cfg)
    merged_bybit = dict(bybit)
    merged_bybit["api_key"] = read_key
    merged_bybit["api_secret"] = read_secret
    merged["bybit"] = merged_bybit
    return merged


def build_read_exchange(cfg: Dict[str, Any]) -> Optional[BybitAdapter]:
    """Отдельный адаптер только для чтения; None — использовать основной exchange."""
    read_cfg = resolve_read_exchange_cfg(cfg)
    if read_cfg is None:
        return None
    return BybitAdapter(read_cfg)
