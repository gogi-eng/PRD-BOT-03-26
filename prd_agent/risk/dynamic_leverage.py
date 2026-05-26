"""
Маппинг итогового score советника → плечо 20–50x (вызывается из trade_advisor).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict


@dataclass(frozen=True)
class DynamicLeverageSettings:
    enabled: bool = False
    min_leverage: int = 20
    max_leverage: int = 50
    min_confidence: float = 0.68
    max_confidence: float = 0.95
    fallback_leverage: int = 20


def load_dynamic_leverage_settings(cfg: Dict[str, Any]) -> DynamicLeverageSettings:
    t = cfg.get("trading", {}) if isinstance(cfg.get("trading"), dict) else {}
    dl = t.get("dynamic_leverage", {})
    if not isinstance(dl, dict):
        dl = {}

    min_lev = max(1, int(dl.get("min", t.get("leverage", 20))))
    max_lev = max(1, int(dl.get("max", 50)))
    if max_lev < min_lev:
        max_lev, min_lev = min_lev, max_lev

    min_conf = float(dl.get("min_confidence", t.get("min_signal_confidence", 0.68)))
    max_conf = float(dl.get("max_confidence", 0.95))
    if max_conf <= min_conf:
        max_conf = min(1.0, min_conf + 0.05)

    enabled = bool(dl.get("enabled", False))
    fallback = max(1, int(t.get("leverage", min_lev)))

    return DynamicLeverageSettings(
        enabled=enabled,
        min_leverage=min_lev,
        max_leverage=max_lev,
        min_confidence=min_conf,
        max_confidence=max_conf,
        fallback_leverage=fallback,
    )


def resolve_trade_leverage(confidence: float, settings: DynamicLeverageSettings) -> int:
    """Чем выше confidence, тем больше плечо (линейно между min и max)."""
    if not settings.enabled:
        return settings.fallback_leverage

    c = max(0.0, min(1.0, float(confidence)))
    if c <= settings.min_confidence:
        return settings.min_leverage
    if c >= settings.max_confidence:
        return settings.max_leverage

    ratio = (c - settings.min_confidence) / (settings.max_confidence - settings.min_confidence)
    lev = settings.min_leverage + ratio * (settings.max_leverage - settings.min_leverage)
    return int(round(lev))
