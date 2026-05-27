"""Два изолированных клиента Bybit: источник (Copy Trading) и цель (субаккаунт)."""
from __future__ import annotations

from typing import Any, Dict

from prd_agent.exchange.bybit_adapter import BybitAdapter


def build_adapter(cfg: Dict[str, Any], section: str) -> BybitAdapter:
    b = cfg.get(section, {})
    wrapped = {
        "_root": cfg["_root"],
        "bybit": {
            "api_key": b["api_key"],
            "api_secret": b["api_secret"],
            "testnet": b.get("testnet", False),
            "category": b.get("category", "linear"),
        },
    }
    return BybitAdapter(wrapped)
