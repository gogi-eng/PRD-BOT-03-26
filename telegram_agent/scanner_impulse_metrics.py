"""Метрики импульса для spike/market scanner: объём (z-score), ATR spike, ускорение цены."""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Dict, List, Mapping, Optional, Sequence


def _sf(value: Any, default: float = 0.0) -> float:
    try:
        return float(str(value).replace(",", ".").strip())
    except (TypeError, ValueError):
        return default


@dataclass(frozen=True)
class ImpulseMetricsConfig:
    atr_period: int = 14
    volume_lookback_bars: int = 20
    volume_zscore_min: float = 2.0
    min_volume_ratio: float = 1.25
    atr_spike_ratio_min: float = 1.5
    price_accel_bars: int = 3
    price_accel_min_pct: float = 0.12
    require_volatility_spike: bool = False
    require_price_accel: bool = False

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any], *, fallback_volume_lookback: int = 8) -> "ImpulseMetricsConfig":
        lookback = int(raw.get("volume_lookback_bars", fallback_volume_lookback))
        return cls(
            atr_period=int(raw.get("atr_period", 14)),
            volume_lookback_bars=max(3, lookback),
            volume_zscore_min=float(raw.get("volume_zscore_min", 2.0)),
            min_volume_ratio=float(raw.get("min_volume_ratio", 1.25)),
            atr_spike_ratio_min=float(raw.get("atr_spike_ratio_min", 1.5)),
            price_accel_bars=max(2, int(raw.get("price_accel_bars", 3))),
            price_accel_min_pct=float(raw.get("price_accel_min_pct", 0.12)),
            require_volatility_spike=bool(raw.get("require_volatility_spike", False)),
            require_price_accel=bool(raw.get("require_price_accel", False)),
        )


@dataclass(frozen=True)
class ImpulseMetrics:
    atr_pct: float
    atr_spike_ratio: float
    volume_ratio: float
    volume_zscore: float
    volume_spike: bool
    price_accel_pct: float
    price_accel_ok: bool
    volatility_spike: bool


def candle_true_range(candle: Mapping[str, Any], prev_close: float = 0.0) -> float:
    high = _sf(candle.get("high"))
    low = _sf(candle.get("low"))
    if high <= 0 or low <= 0:
        return 0.0
    if prev_close > 0:
        return max(high - low, abs(high - prev_close), abs(low - prev_close))
    return high - low


def compute_atr_value(klines: Sequence[Mapping[str, Any]], period: int = 14) -> float:
    if len(klines) < 2:
        return 0.0
    n = min(max(period, 5), max(5, len(klines) - 1))
    trs: list[float] = []
    prev_close = _sf(klines[0].get("close"))
    for i in range(1, len(klines)):
        tr = candle_true_range(klines[i], prev_close)
        if tr > 0:
            trs.append(tr)
        prev_close = _sf(klines[i].get("close"))
    chunk = trs[-n:] if trs else []
    return sum(chunk) / max(len(chunk), 1)


def compute_atr_pct(klines: Sequence[Mapping[str, Any]], period: int = 14) -> float:
    if not klines:
        return 0.0
    price = _sf(klines[-1].get("close"))
    if price <= 0:
        return 0.0
    return compute_atr_value(klines, period) / price * 100.0


def _impulse_index(klines: Sequence[Mapping[str, Any]], impulse: Mapping[str, Any]) -> int:
    if not klines:
        return -1
    if klines[-1] is impulse:
        return len(klines) - 1
    if len(klines) >= 2 and klines[-2] is impulse:
        return len(klines) - 2
    for idx in range(len(klines) - 1, -1, -1):
        if klines[idx] is impulse:
            return idx
    return len(klines) - 1


def compute_volume_stats(
    klines: Sequence[Mapping[str, Any]],
    *,
    impulse: Mapping[str, Any],
    lookback: int,
) -> tuple[float, float, bool]:
    idx = _impulse_index(klines, impulse)
    if idx < 0:
        return 0.0, 0.0, False
    history = list(klines[max(0, idx - lookback) : idx])
    impulse_vol = _sf(impulse.get("volume"))
    if impulse_vol <= 0:
        return 0.0, 0.0, False
    base_vals = [_sf(k.get("volume")) for k in history if _sf(k.get("volume")) > 0]
    if not base_vals:
        return impulse_vol, 0.0, False
    mean = sum(base_vals) / len(base_vals)
    ratio = impulse_vol / max(mean, 1e-12)
    if len(base_vals) < 2:
        return ratio, 0.0, False
    variance = sum((v - mean) ** 2 for v in base_vals) / len(base_vals)
    std = math.sqrt(variance)
    if std <= 1e-12:
        return ratio, 0.0, False
    zscore = (impulse_vol - mean) / std
    return ratio, zscore, True


def compute_price_acceleration(klines: Sequence[Mapping[str, Any]], bars: int = 3) -> float:
    if len(klines) < bars + 1:
        return 0.0
    moves: list[float] = []
    for i in range(len(klines) - bars, len(klines)):
        prev_close = _sf(klines[i - 1].get("close"))
        close = _sf(klines[i].get("close"))
        if prev_close <= 0 or close <= 0:
            continue
        moves.append(abs(close - prev_close) / prev_close * 100.0)
    if len(moves) < 2:
        return 0.0
    latest = moves[-1]
    baseline = sum(moves[:-1]) / max(len(moves) - 1, 1)
    return latest - baseline


def analyze_impulse_metrics(
    klines: List[Dict[str, Any]],
    impulse: Mapping[str, Any],
    cfg: ImpulseMetricsConfig,
) -> ImpulseMetrics:
    idx = _impulse_index(klines, impulse)
    base_klines = list(klines[:idx]) if idx > 0 else list(klines[:-1])
    price = _sf(impulse.get("close")) or _sf(klines[-1].get("close"))
    atr_value = compute_atr_value(base_klines or klines, cfg.atr_period)
    if atr_value <= 0 and price > 0:
        atr_value = price * 0.01
    atr_pct = (atr_value / price * 100.0) if price > 0 else 0.0

    prev_close = _sf(base_klines[-1].get("close")) if base_klines else 0.0
    impulse_tr = candle_true_range(impulse, prev_close)
    atr_spike_ratio = impulse_tr / max(atr_value, 1e-12) if impulse_tr > 0 else 0.0

    volume_ratio, volume_zscore, zscore_reliable = compute_volume_stats(
        klines,
        impulse=impulse,
        lookback=cfg.volume_lookback_bars,
    )
    volume_spike = (
        (zscore_reliable and volume_zscore >= cfg.volume_zscore_min)
        or volume_ratio >= cfg.min_volume_ratio
    )
    volatility_spike = atr_spike_ratio >= cfg.atr_spike_ratio_min

    price_accel_pct = compute_price_acceleration(klines, cfg.price_accel_bars)
    price_accel_ok = price_accel_pct >= cfg.price_accel_min_pct

    return ImpulseMetrics(
        atr_pct=atr_pct,
        atr_spike_ratio=atr_spike_ratio,
        volume_ratio=volume_ratio,
        volume_zscore=volume_zscore,
        volume_spike=volume_spike,
        price_accel_pct=price_accel_pct,
        price_accel_ok=price_accel_ok,
        volatility_spike=volatility_spike,
    )


def impulse_metrics_pass_filters(metrics: ImpulseMetrics, cfg: ImpulseMetricsConfig) -> bool:
    if not metrics.volume_spike:
        return False
    if cfg.require_volatility_spike and not metrics.volatility_spike:
        return False
    if cfg.require_price_accel and not metrics.price_accel_ok:
        return False
    return True
