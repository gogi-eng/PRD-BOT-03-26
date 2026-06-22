"""Защита от ликвидации: ранний выход до цены ликвидации Bybit."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Tuple


@dataclass
class LiquidationGuardConfig:
    enabled: bool = True
    buffer_pct: float = 0.85
    skip_manual: bool = True
    emergency_close: bool = True

    @classmethod
    def from_cfg(cls, cfg: Dict[str, Any]) -> "LiquidationGuardConfig":
        pos = cfg.get("positions", {}) if isinstance(cfg.get("positions"), dict) else {}
        raw = pos.get("liquidation_guard", {})
        if not isinstance(raw, dict):
            raw = {}
        return cls(
            enabled=bool(raw.get("enabled", True)),
            buffer_pct=float(raw.get("buffer_pct", 0.85) or 0.85),
            skip_manual=bool(raw.get("skip_manual", True)),
            emergency_close=bool(raw.get("emergency_close", True)),
        )


def protective_level(liq_price: float, side: str, buffer_pct: float) -> float:
    """Уровень раннего выхода между mark и биржевой ликвидацией."""
    if liq_price <= 0:
        return 0.0
    buf = max(0.05, float(buffer_pct)) / 100.0
    side_u = str(side or "").upper()
    if side_u in ("BUY", "LONG"):
        return liq_price * (1.0 + buf)
    return liq_price * (1.0 - buf)


def distance_to_liq_pct(side: str, mark_price: float, liq_price: float) -> float:
    if mark_price <= 0 or liq_price <= 0:
        return 0.0
    side_u = str(side or "").upper()
    if side_u in ("BUY", "LONG"):
        return (mark_price - liq_price) / mark_price * 100.0
    return (liq_price - mark_price) / mark_price * 100.0


def evaluate_liquidation_stop(
    *,
    side: str,
    mark_price: float,
    liq_price: float,
    cfg: LiquidationGuardConfig,
    origin: str = "bot",
) -> Tuple[bool, str]:
    if not cfg.enabled or not cfg.emergency_close:
        return False, ""
    if cfg.skip_manual and str(origin or "").lower() == "manual":
        return False, ""
    guard = protective_level(liq_price, side, cfg.buffer_pct)
    if guard <= 0:
        return False, ""
    side_u = str(side or "").upper()
    if side_u in ("BUY", "LONG") and mark_price <= guard:
        return True, (
            f"liquidation_stop: mark {mark_price:.6g} <= guard {guard:.6g} "
            f"(liq {liq_price:.6g}, buffer {cfg.buffer_pct:.2f}%)"
        )
    if side_u in ("SELL", "SHORT") and mark_price >= guard:
        return True, (
            f"liquidation_stop: mark {mark_price:.6g} >= guard {guard:.6g} "
            f"(liq {liq_price:.6g}, buffer {cfg.buffer_pct:.2f}%)"
        )
    return False, ""
