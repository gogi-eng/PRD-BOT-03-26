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


def effective_buffer_pct(
    base_buffer_pct: float,
    *,
    entry: float = 0.0,
    liq_price: float = 0.0,
    leverage: int = 0,
) -> float:
    """Адаптивный буфер от цены ликвидации с учётом дистанции entry <-> liq_price / плеча."""
    base = max(0.05, float(base_buffer_pct or 0.85))
    dist_pct = 0.0
    if entry > 0 and liq_price > 0:
        dist_pct = abs(entry - liq_price) / entry * 100.0
    elif leverage > 0:
        dist_pct = max(0.5, (100.0 / float(leverage)) - 0.5)
    if dist_pct <= 0:
        return base
    return min(base, max(0.20, 0.40 * dist_pct))


def protective_level(
    liq_price: float,
    side: str,
    buffer_pct: float,
    *,
    entry: float = 0.0,
    leverage: int = 0,
) -> float:
    """Уровень раннего выхода между mark и биржевой ликвидацией."""
    if liq_price <= 0:
        return 0.0
    eff_buf = effective_buffer_pct(
        buffer_pct,
        entry=entry,
        liq_price=liq_price,
        leverage=leverage,
    )
    buf = max(0.05, eff_buf) / 100.0
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
    entry: float = 0.0,
    stop_loss: float = 0.0,
    leverage: int = 0,
) -> Tuple[bool, str]:
    if not cfg.enabled or not cfg.emergency_close:
        return False, ""
    if cfg.skip_manual and str(origin or "").lower() == "manual":
        return False, ""
    eff_buf = effective_buffer_pct(
        cfg.buffer_pct,
        entry=entry,
        liq_price=liq_price,
        leverage=leverage,
    )
    guard = protective_level(
        liq_price,
        side,
        cfg.buffer_pct,
        entry=entry,
        leverage=leverage,
    )
    if guard <= 0:
        return False, ""
    side_u = str(side or "").upper()
    sl = float(stop_loss or 0.0)
    if side_u in ("BUY", "LONG"):
        # Если установлен валидный SL безопаснее цены ликвидации, не закрываем раньше штатного SL
        if sl > liq_price:
            if mark_price > sl:
                return False, ""
            if guard > sl:
                guard = (sl + liq_price) / 2.0
        if mark_price <= guard:
            return True, (
                f"liquidation_stop: mark {mark_price:.6g} <= guard {guard:.6g} "
                f"(liq {liq_price:.6g}, buffer {eff_buf:.2f}%)"
            )
    if side_u in ("SELL", "SHORT"):
        if 0 < sl < liq_price:
            if mark_price < sl:
                return False, ""
            if guard < sl:
                guard = (sl + liq_price) / 2.0
        if mark_price >= guard:
            return True, (
                f"liquidation_stop: mark {mark_price:.6g} >= guard {guard:.6g} "
                f"(liq {liq_price:.6g}, buffer {eff_buf:.2f}%)"
            )
    return False, ""
