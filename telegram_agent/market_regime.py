"""Classify linear market as trend vs chop (range) from recent klines — for Telegram signal agent weighting."""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from exchange.bybit_client import BybitClient


@dataclass
class MarketRegimeConfig:
    enabled: bool = False
    kline_interval: str = "15"
    lookback_bars: int = 96
    chop_max_range_pct: float = 2.8
    chop_max_abs_return_pct: float = 1.2
    trend_min_abs_return_pct: float = 2.0
    trend_min_slope_pct_per_bar: float = 0.04


def market_regime_from_agent_cfg(raw: dict[str, Any] | None) -> MarketRegimeConfig:
    if not isinstance(raw, dict):
        return MarketRegimeConfig()
    return MarketRegimeConfig(
        enabled=bool(raw.get("enabled", False)),
        kline_interval=str(raw.get("kline_interval", "15") or "15"),
        lookback_bars=max(12, int(raw.get("lookback_bars", 96) or 96)),
        chop_max_range_pct=float(raw.get("chop_max_range_pct", 2.8) or 2.8),
        chop_max_abs_return_pct=float(raw.get("chop_max_abs_return_pct", 1.2) or 1.2),
        trend_min_abs_return_pct=float(raw.get("trend_min_abs_return_pct", 2.0) or 2.0),
        trend_min_slope_pct_per_bar=float(raw.get("trend_min_slope_pct_per_bar", 0.04) or 0.04),
    )


async def classify_regime(bybit: BybitClient, symbol: str, cfg: MarketRegimeConfig) -> str:
    """Return 'trend', 'chop', or 'unknown'."""
    if not cfg.enabled:
        return "unknown"
    sym = str(symbol or "").upper().strip()
    if not sym:
        return "unknown"
    try:
        klines = await bybit.get_klines(sym, interval=cfg.kline_interval, limit=cfg.lookback_bars)
    except Exception:
        return "unknown"
    if len(klines) < 12:
        return "unknown"
    highs = [float(k.get("high", 0) or 0) for k in klines]
    lows = [float(k.get("low", 0) or 0) for k in klines]
    closes = [float(k.get("close", 0) or 0) for k in klines]
    if min(highs) <= 0 or min(lows) <= 0 or min(closes) <= 0:
        return "unknown"
    top, bottom = max(highs), min(lows)
    mid = (top + bottom) / 2.0
    range_pct = (top - bottom) / max(mid, 1e-12) * 100.0
    first_c, last_c = closes[0], closes[-1]
    abs_ret_pct = abs(last_c - first_c) / max(first_c, 1e-12) * 100.0
    n = len(closes)
    slope_pct_per_bar = abs(last_c - first_c) / max(n - 1, 1) / max(first_c, 1e-12) * 100.0

    if range_pct <= cfg.chop_max_range_pct and abs_ret_pct <= cfg.chop_max_abs_return_pct:
        return "chop"
    if abs_ret_pct >= cfg.trend_min_abs_return_pct or slope_pct_per_bar >= cfg.trend_min_slope_pct_per_bar:
        return "trend"
    return "unknown"
