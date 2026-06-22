"""Быстрый детектор памп/дамп: движение одной 15m свечи >= порога (скальп)."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Mapping, Optional, Tuple


def _sf(value: Any, default: float = 0.0) -> float:
    try:
        return float(str(value).replace(",", ".").strip())
    except (TypeError, ValueError):
        return default


@dataclass(frozen=True)
class SpikeScanConfig:
    enabled: bool = False
    interval_sec: float = 90.0
    kline_interval: str = "15"
    kline_limit: int = 12
    min_move_pct: float = 3.0
    min_24h_volume_usdt: float = 5_000_000.0
    max_symbols: int = 50
    top_n: int = 2
    symbol_cooldown_sec: int = 1800
    auto_execute: bool = True
    execute_min_score: int = 72
    min_volume_ratio: float = 1.25
    sl_buffer_pct: float = 0.25
    min_rr: float = 1.5
    use_closed_candle: bool = False
    volume_lookback_bars: int = 8

    @classmethod
    def from_cfg(cls, cfg: Mapping[str, Any]) -> "SpikeScanConfig":
        mc = cfg.get("market_scanner") if isinstance(cfg.get("market_scanner"), dict) else {}
        agent = cfg.get("telegram_signal_agent") if isinstance(cfg.get("telegram_signal_agent"), dict) else {}
        raw = mc.get("spike_scalp") if isinstance(mc.get("spike_scalp"), dict) else {}
        if not raw and isinstance(agent.get("spike_scalp"), dict):
            raw = agent["spike_scalp"]
        if not isinstance(raw, dict):
            raw = {}
        t = cfg.get("trading") if isinstance(cfg.get("trading"), dict) else {}
        min_vol = float(
            raw.get(
                "min_24h_volume_usdt",
                agent.get(
                    "market_scanner_min_24h_volume_usdt",
                    t.get("min_24h_volume_usdt", 5_000_000),
                ),
            )
        )
        return cls(
            enabled=bool(raw.get("enabled", False)),
            interval_sec=float(raw.get("interval_sec", 90)),
            kline_interval=str(raw.get("kline_interval", "15")),
            kline_limit=int(raw.get("kline_limit", 12)),
            min_move_pct=float(raw.get("min_move_pct", 3.0)),
            min_24h_volume_usdt=min_vol,
            max_symbols=int(raw.get("max_symbols", 50)),
            top_n=int(raw.get("top_n", 2)),
            symbol_cooldown_sec=int(raw.get("symbol_cooldown_sec", 1800)),
            auto_execute=bool(raw.get("auto_execute", True)),
            execute_min_score=int(raw.get("execute_min_score", 72)),
            min_volume_ratio=float(raw.get("min_volume_ratio", 1.25)),
            sl_buffer_pct=float(raw.get("sl_buffer_pct", 0.25)),
            min_rr=float(raw.get("min_rr", 1.5)),
            use_closed_candle=bool(raw.get("use_closed_candle", False)),
            volume_lookback_bars=int(raw.get("volume_lookback_bars", 8)),
        )


def candle_move_pct(candle: Mapping[str, Any]) -> float:
    o = _sf(candle.get("open"))
    c = _sf(candle.get("close"))
    if o <= 0:
        return 0.0
    return (c - o) / o * 100.0


def pick_impulse_candle(klines: List[Dict[str, Any]], *, use_closed: bool) -> Optional[Dict[str, Any]]:
    if not klines:
        return None
    if use_closed and len(klines) >= 2:
        return klines[-2]
    return klines[-1]


def _avg_volume(klines: List[Dict[str, Any]], n: int) -> float:
    if not klines or n <= 0:
        return 0.0
    chunk = klines[-n:]
    vals = [_sf(k.get("volume")) for k in chunk if _sf(k.get("volume")) > 0]
    if not vals:
        return 0.0
    return sum(vals) / len(vals)


def spike_invalidation_and_target(
    *,
    scenario: str,
    price: float,
    candle: Mapping[str, Any],
    sl_buffer_pct: float,
    min_rr: float,
) -> Tuple[float, float]:
    low = _sf(candle.get("low"))
    high = _sf(candle.get("high"))
    px = float(price or 0.0)
    buf = max(0.0, float(sl_buffer_pct or 0.0)) / 100.0
    rr = max(1.0, float(min_rr or 1.5))
    scen = str(scenario or "").upper()
    if px <= 0:
        return low, high
    if scen == "PUMP":
        inv = low * (1.0 - buf) if low > 0 else px * 0.97
        risk = px - inv
        if risk <= 0:
            inv = low if low > 0 else px * 0.985
            risk = max(px - inv, px * 0.005)
        return inv, px + risk * rr
    if scen == "DUMP":
        inv = high * (1.0 + buf) if high > 0 else px * 1.03
        risk = inv - px
        if risk <= 0:
            inv = high if high > 0 else px * 1.015
            risk = max(inv - px, px * 0.005)
        return inv, px - risk * rr
    return low, high


def compute_spike_score(
    *,
    move_pct: float,
    min_move_pct: float,
    volume_ratio: float,
    min_volume_ratio: float,
    turnover_24h: float,
    min_24h_volume_usdt: float,
) -> Tuple[int, List[str]]:
    reasons: List[str] = []
    score = 68
    excess = abs(move_pct) - min_move_pct
    bump = min(24, int(max(0.0, excess) * 4.0))
    score += bump
    reasons.append(f"импульс 15m {move_pct:+.2f}% (порог {min_move_pct:g}%)")
    if volume_ratio >= min_volume_ratio:
        score += 10
        reasons.append(f"объём свечи {volume_ratio:.2f}x к среднему")
    elif volume_ratio >= 1.05:
        score += 4
        reasons.append(f"объём слегка выше среднего: {volume_ratio:.2f}x")
    if turnover_24h >= min_24h_volume_usdt * 2:
        score += 5
        reasons.append(f"оборот 24ч {turnover_24h / 1_000_000:.1f}M USDT")
    return max(0, min(100, int(score))), reasons


def analyze_spike_setup(
    *,
    symbol: str,
    klines: List[Dict[str, Any]],
    turnover_24h: float,
    cfg: SpikeScanConfig,
) -> Optional[Dict[str, Any]]:
    if not cfg.enabled or len(klines) < 4:
        return None
    if turnover_24h < cfg.min_24h_volume_usdt:
        return None

    impulse = pick_impulse_candle(klines, use_closed=cfg.use_closed_candle)
    if not impulse:
        return None

    move_pct = candle_move_pct(impulse)
    if move_pct >= cfg.min_move_pct:
        scenario = "PUMP"
    elif move_pct <= -cfg.min_move_pct:
        scenario = "DUMP"
    else:
        return None

    price = _sf(klines[-1].get("close")) or _sf(impulse.get("close"))
    if price <= 0:
        return None

    lookback = max(3, cfg.volume_lookback_bars)
    base_vol = _avg_volume(klines[:-1], lookback)
    impulse_vol = _sf(impulse.get("volume"))
    volume_ratio = impulse_vol / max(base_vol, 1e-12) if impulse_vol > 0 else 0.0
    if volume_ratio < cfg.min_volume_ratio:
        return None

    window = klines[-max(4, lookback) :]
    range_low = min(_sf(k.get("low")) for k in window if _sf(k.get("low")) > 0)
    range_high = max(_sf(k.get("high")) for k in window if _sf(k.get("high")) > 0)
    invalidation, target = spike_invalidation_and_target(
        scenario=scenario,
        price=price,
        candle=impulse,
        sl_buffer_pct=cfg.sl_buffer_pct,
        min_rr=cfg.min_rr,
    )
    score, reasons = compute_spike_score(
        move_pct=move_pct,
        min_move_pct=cfg.min_move_pct,
        volume_ratio=volume_ratio,
        min_volume_ratio=cfg.min_volume_ratio,
        turnover_24h=turnover_24h,
        min_24h_volume_usdt=cfg.min_24h_volume_usdt,
    )

    bos_level = _sf(impulse.get("high")) if scenario == "PUMP" else _sf(impulse.get("low"))
    return {
        "symbol": str(symbol).upper(),
        "scenario": scenario,
        "score": score,
        "price": price,
        "turnover_24h": turnover_24h,
        "range_low": range_low,
        "range_high": range_high,
        "range_pct": abs(move_pct),
        "atr_pct": abs(move_pct),
        "volume_ratio": volume_ratio,
        "bos_level": bos_level,
        "invalidation": invalidation,
        "target": target,
        "reasons": reasons,
        "move_pct": move_pct,
    }
