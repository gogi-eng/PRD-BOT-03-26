"""
SPIKE HTF-фильтр: не входить против тренда старшего ТФ (по умолчанию 1h).

Включается через market_scanner.spike_scalp.require_htf_trend_align.
Логика: PUMP/BUY запрещён при bearish HTF; DUMP/SELL — при bullish HTF.
Нейтральный HTF по умолчанию разрешён (htf_allow_neutral: true).

Тренд = EMA fast/slow на свечах HTF (та же идея, что MarketAnalyzer.trend).

Опционально (htf_sr_context_enabled): против тренда разрешить вход, если есть
контекст S/R на HTF — разворот у уровня или продолжение после пробоя.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from analysis.structure_zones import StructureZoneAnalyzer, ZoneContext


class HtfTrend(IntEnum):
    BEARISH = -1
    NEUTRAL = 0
    BULLISH = 1


@dataclass(frozen=True)
class SpikeHtfConfig:
    enabled: bool = False
    intervals: Tuple[str, ...] = ("60",)
    allow_neutral: bool = True
    kline_limit: int = 80
    ema_fast: int = 21
    ema_slow: int = 55
    # S/R-исключения против тренда (разворот у уровня / пробой с продолжением).
    sr_context_enabled: bool = False
    sr_near_pct: float = 0.35
    allow_against_at_sr: bool = True
    allow_against_on_breakout: bool = True
    sr_breakout_lookback_bars: int = 3


@dataclass(frozen=True)
class SpikeHtfDecision:
    allowed: bool
    reason: str
    trend_label: str = "neutral"
    interval: str = ""


def _spike_raw(cfg: Mapping[str, Any]) -> Dict[str, Any]:
    mc = cfg.get("market_scanner") if isinstance(cfg.get("market_scanner"), dict) else {}
    agent = cfg.get("telegram_signal_agent") if isinstance(cfg.get("telegram_signal_agent"), dict) else {}
    raw = mc.get("spike_scalp") if isinstance(mc.get("spike_scalp"), dict) else {}
    if not raw and isinstance(agent.get("spike_scalp"), dict):
        raw = agent["spike_scalp"]
    return dict(raw) if isinstance(raw, dict) else {}


def read_spike_htf_cfg(cfg: Mapping[str, Any]) -> SpikeHtfConfig:
    raw = _spike_raw(cfg)
    intervals_raw = raw.get("htf_trend_intervals", raw.get("htf_intervals", ["60"]))
    if isinstance(intervals_raw, (str, int)):
        intervals: Tuple[str, ...] = (str(intervals_raw),)
    elif isinstance(intervals_raw, Sequence):
        intervals = tuple(str(x).strip() for x in intervals_raw if str(x).strip())
    else:
        intervals = ("60",)
    if not intervals:
        intervals = ("60",)
    near_pct = float(raw.get("htf_sr_near_pct", 0.35) or 0.35)
    near_pct = max(0.05, min(5.0, near_pct))
    lookback = int(raw.get("htf_sr_breakout_lookback_bars", 3) or 3)
    lookback = max(1, min(12, lookback))
    return SpikeHtfConfig(
        enabled=bool(raw.get("require_htf_trend_align", False)),
        intervals=intervals,
        allow_neutral=bool(raw.get("htf_allow_neutral", True)),
        kline_limit=max(30, int(raw.get("htf_kline_limit", 80) or 80)),
        ema_fast=max(2, int(raw.get("htf_ema_fast", 21) or 21)),
        ema_slow=max(3, int(raw.get("htf_ema_slow", 55) or 55)),
        sr_context_enabled=bool(raw.get("htf_sr_context_enabled", False)),
        sr_near_pct=near_pct,
        allow_against_at_sr=bool(raw.get("htf_allow_against_at_sr", True)),
        allow_against_on_breakout=bool(raw.get("htf_allow_against_on_breakout", True)),
        sr_breakout_lookback_bars=lookback,
    )


def spike_htf_align_enabled(cfg: Mapping[str, Any]) -> bool:
    return bool(read_spike_htf_cfg(cfg).enabled)


def _normalize_side(side: str) -> str:
    s = str(side or "").strip().upper()
    if s in ("LONG", "BUY", "PUMP"):
        return "BUY"
    if s in ("SHORT", "SELL", "DUMP"):
        return "SELL"
    return s


def _ema(data: Sequence[float], period: int) -> float:
    if len(data) < period:
        return float(data[-1]) if data else 0.0
    multiplier = 2.0 / (period + 1)
    ema = sum(data[:period]) / period
    for val in data[period:]:
        ema = (val - ema) * multiplier + ema
    return float(ema)


def _resolve_trend(price: float, ema_fast: float, ema_slow: float) -> HtfTrend:
    if price > ema_fast > ema_slow:
        return HtfTrend.BULLISH
    if price < ema_fast < ema_slow:
        return HtfTrend.BEARISH
    if ema_fast > ema_slow * 1.001:
        return HtfTrend.BULLISH
    if ema_fast < ema_slow * 0.999:
        return HtfTrend.BEARISH
    return HtfTrend.NEUTRAL


def trend_from_klines(
    klines: Sequence[Mapping[str, Any]],
    *,
    ema_fast: int = 21,
    ema_slow: int = 55,
) -> HtfTrend:
    """Тренд по EMA на переданных свечах."""
    bars = list(klines or [])
    need = max(ema_slow + 5, 30)
    if len(bars) < need:
        return HtfTrend.NEUTRAL
    closes: List[float] = []
    for k in bars:
        try:
            c = float(k.get("close", 0) or 0)
        except (TypeError, ValueError):
            c = 0.0
        if c > 0:
            closes.append(c)
    if len(closes) < need:
        return HtfTrend.NEUTRAL
    fast = _ema(closes, ema_fast)
    slow = _ema(closes, ema_slow)
    return _resolve_trend(closes[-1], fast, slow)


def _label(trend: HtfTrend) -> str:
    if trend == HtfTrend.BULLISH:
        return "bullish"
    if trend == HtfTrend.BEARISH:
        return "bearish"
    return "neutral"


def _bar_close(bar: Mapping[str, Any]) -> float:
    try:
        return float(bar.get("close", 0) or 0)
    except (TypeError, ValueError):
        return 0.0


def _pct_away(price: float, level: float) -> float:
    if price <= 0 or level <= 0:
        return 999.0
    return abs(price - level) / price * 100.0


def _nearest_support(levels: Sequence[float], price: float) -> Optional[float]:
    below = [float(x) for x in levels if float(x) > 0 and float(x) <= price]
    return max(below) if below else None


def _nearest_resistance(levels: Sequence[float], price: float) -> Optional[float]:
    above = [float(x) for x in levels if float(x) > 0 and float(x) >= price]
    return min(above) if above else None


def _zone_ctx_from_klines(
    klines: Sequence[Mapping[str, Any]],
    price: float,
) -> Optional[ZoneContext]:
    bars = list(klines or [])
    if price <= 0 or len(bars) < 6:
        return None
    return StructureZoneAnalyzer().analyze(bars, float(price))


def _near_support_level(
    ctx: ZoneContext,
    price: float,
    near_pct: float,
) -> Optional[float]:
    """Цена у поддержки / в бычьей зоне — кандидат на разворот вверх."""
    z = ctx.price_near_bullish_zone(price, tolerance_pct=near_pct)
    if z is not None:
        return float(z.mid)
    z_in = ctx.price_in_bullish_zone(price)
    if z_in is not None:
        return float(z_in.mid)
    supports = list(ctx.support_levels)
    for z in ctx.all_bullish_zones:
        supports.append(float(z.low))
        supports.append(float(z.mid))
    nearest = _nearest_support(supports, price)
    if nearest is not None and _pct_away(price, nearest) <= near_pct:
        return nearest
    return None


def _near_resistance_level(
    ctx: ZoneContext,
    price: float,
    near_pct: float,
) -> Optional[float]:
    """Цена у сопротивления / в медвежьей зоне — кандидат на разворот вниз."""
    z = ctx.price_near_bearish_zone(price, tolerance_pct=near_pct)
    if z is not None:
        return float(z.mid)
    z_in = ctx.price_in_bearish_zone(price)
    if z_in is not None:
        return float(z_in.mid)
    resistances = list(ctx.resistance_levels)
    for z in ctx.all_bearish_zones:
        resistances.append(float(z.high))
        resistances.append(float(z.mid))
    nearest = _nearest_resistance(resistances, price)
    if nearest is not None and _pct_away(price, nearest) <= near_pct:
        return nearest
    return None


def detect_htf_sr_breakout(
    side: str,
    klines: Sequence[Mapping[str, Any]],
    *,
    lookback_bars: int = 3,
    confirm_bars: int = 1,
) -> Tuple[bool, float]:
    """
    Пробой S/R в сторону сигнала по HTF-свечам.
    BUY: закрытие выше сопротивления, которое было над ценой N баров назад.
    SELL: закрытие ниже поддержки, которая была под ценой N баров назад.
    """
    side_u = _normalize_side(side)
    bars = list(klines or [])
    lb = max(1, int(lookback_bars))
    conf = max(1, int(confirm_bars))
    if len(bars) < lb + 6:
        return False, 0.0
    price = _bar_close(bars[-1])
    if price <= 0:
        return False, 0.0
    ref_idx = -(lb + 1)
    ref_price = _bar_close(bars[ref_idx])
    if ref_price <= 0:
        return False, 0.0
    # Зоны «как было» до недавнего движения — уровень для пробоя.
    ref_ctx = _zone_ctx_from_klines(bars[: len(bars) - lb], ref_price)
    if ref_ctx is None:
        return False, 0.0
    recent = bars[-conf:]
    recent_closes = [_bar_close(b) for b in recent]
    if any(c <= 0 for c in recent_closes):
        return False, 0.0

    if side_u == "BUY":
        level = _nearest_resistance(ref_ctx.resistance_levels, ref_price)
        if level is None:
            for z in ref_ctx.all_bearish_zones:
                hi = float(z.high)
                if hi >= ref_price and (level is None or hi < level):
                    level = hi
        if level is None or level <= 0:
            return False, 0.0
        if all(c > level for c in recent_closes) and price > level:
            return True, float(level)
        return False, 0.0

    if side_u == "SELL":
        level = _nearest_support(ref_ctx.support_levels, ref_price)
        if level is None:
            for z in ref_ctx.all_bullish_zones:
                lo = float(z.low)
                if lo <= ref_price and (level is None or lo > level):
                    level = lo
        if level is None or level <= 0:
            return False, 0.0
        if all(c < level for c in recent_closes) and price < level:
            return True, float(level)
        return False, 0.0

    return False, 0.0


def evaluate_against_trend_sr_context(
    side: str,
    *,
    trend: HtfTrend,
    klines: Sequence[Mapping[str, Any]],
    htf_cfg: SpikeHtfConfig,
    interval: str = "60",
) -> SpikeHtfDecision:
    """
    Исключение: сигнал против HTF, но у S/R (разворот) или после пробоя (продолжение).
    """
    side_u = _normalize_side(side)
    label = _label(trend)
    iv = str(interval)
    bars = list(klines or [])
    price = _bar_close(bars[-1]) if bars else 0.0

    if not htf_cfg.sr_context_enabled:
        return SpikeHtfDecision(
            allowed=False,
            reason=f"htf_align: {side_u} against {label} + no SR context → block ({iv})",
            trend_label=label,
            interval=iv,
        )

    if side_u not in ("BUY", "SELL") or price <= 0 or len(bars) < 6:
        return SpikeHtfDecision(
            allowed=False,
            reason=f"htf_align: {side_u} against {label} + no SR context → block ({iv})",
            trend_label=label,
            interval=iv,
        )

    # 1) Пробой в сторону сигнала → продолжение после breakout.
    if htf_cfg.allow_against_on_breakout:
        broke, level = detect_htf_sr_breakout(
            side_u,
            bars,
            lookback_bars=htf_cfg.sr_breakout_lookback_bars,
        )
        if broke:
            what = "resistance" if side_u == "BUY" else "support"
            return SpikeHtfDecision(
                allowed=True,
                reason=(
                    f"htf_align: {side_u} against {label} but broke {what} "
                    f"{level:.6g} → allow ({iv})"
                ),
                trend_label=label,
                interval=iv,
            )

    # 2) Разворот у противоположного S/R.
    if htf_cfg.allow_against_at_sr:
        ctx = _zone_ctx_from_klines(bars, price)
        if ctx is not None:
            if side_u == "BUY" and trend == HtfTrend.BEARISH:
                near = _near_support_level(ctx, price, htf_cfg.sr_near_pct)
                if near is not None:
                    return SpikeHtfDecision(
                        allowed=True,
                        reason=(
                            f"htf_align: {side_u} against {label} but near support "
                            f"{near:.6g} → allow ({iv})"
                        ),
                        trend_label=label,
                        interval=iv,
                    )
            if side_u == "SELL" and trend == HtfTrend.BULLISH:
                near = _near_resistance_level(ctx, price, htf_cfg.sr_near_pct)
                if near is not None:
                    return SpikeHtfDecision(
                        allowed=True,
                        reason=(
                            f"htf_align: {side_u} against {label} but near resistance "
                            f"{near:.6g} → allow ({iv})"
                        ),
                        trend_label=label,
                        interval=iv,
                    )

    return SpikeHtfDecision(
        allowed=False,
        reason=f"htf_align: {side_u} against {label} + no SR context → block ({iv})",
        trend_label=label,
        interval=iv,
    )


def decide_spike_htf_align(
    side: str,
    *,
    trend: HtfTrend,
    allow_neutral: bool = True,
    interval: str = "60",
) -> SpikeHtfDecision:
    """Чистая проверка: сторона сделки vs HTF-тренд (без S/R)."""
    side_u = _normalize_side(side)
    label = _label(trend)
    if side_u not in ("BUY", "SELL"):
        return SpikeHtfDecision(
            allowed=False,
            reason=f"htf_align: unknown_side={side!r}",
            trend_label=label,
            interval=str(interval),
        )
    if trend == HtfTrend.NEUTRAL:
        if allow_neutral:
            return SpikeHtfDecision(
                allowed=True,
                reason=f"htf_align: neutral ok ({interval})",
                trend_label=label,
                interval=str(interval),
            )
        return SpikeHtfDecision(
            allowed=False,
            reason=f"htf_align: neutral blocked ({interval})",
            trend_label=label,
            interval=str(interval),
        )
    opposite = (side_u == "BUY" and trend == HtfTrend.BEARISH) or (
        side_u == "SELL" and trend == HtfTrend.BULLISH
    )
    if opposite:
        return SpikeHtfDecision(
            allowed=False,
            reason=f"htf_align: {side_u} against {label} ({interval})",
            trend_label=label,
            interval=str(interval),
        )
    return SpikeHtfDecision(
        allowed=True,
        reason=f"htf_align: {side_u} with {label} ({interval})",
        trend_label=label,
        interval=str(interval),
    )


def evaluate_spike_htf_klines(
    side: str,
    interval_klines: Mapping[str, Sequence[Mapping[str, Any]]],
    htf_cfg: SpikeHtfConfig,
) -> SpikeHtfDecision:
    """
    Проверяет все интервалы из конфига.
    Блок, если хоть один ТФ явно против стороны (и нет S/R-исключения).
    """
    if not htf_cfg.enabled:
        return SpikeHtfDecision(allowed=True, reason="htf_align: disabled")

    last_ok = SpikeHtfDecision(allowed=True, reason="htf_align: no_intervals")
    for iv in htf_cfg.intervals:
        bars = list(interval_klines.get(str(iv)) or [])
        trend = trend_from_klines(
            bars,
            ema_fast=htf_cfg.ema_fast,
            ema_slow=htf_cfg.ema_slow,
        )
        decision = decide_spike_htf_align(
            side,
            trend=trend,
            allow_neutral=htf_cfg.allow_neutral,
            interval=str(iv),
        )
        if decision.allowed:
            last_ok = decision
            continue
        # Против тренда — проверить S/R (разворот у уровня / пробой).
        if "against" in decision.reason and htf_cfg.sr_context_enabled:
            sr = evaluate_against_trend_sr_context(
                side,
                trend=trend,
                klines=bars,
                htf_cfg=htf_cfg,
                interval=str(iv),
            )
            if sr.allowed:
                last_ok = sr
                continue
            return sr
        if "against" in decision.reason and not htf_cfg.sr_context_enabled:
            # Явная формулировка для логов (как при включённом SR без контекста).
            return SpikeHtfDecision(
                allowed=False,
                reason=(
                    f"htf_align: {_normalize_side(side)} against {_label(trend)} "
                    f"+ no SR context → block ({iv})"
                ),
                trend_label=decision.trend_label,
                interval=str(iv),
            )
        return decision
    return last_ok


def evaluate_spike_htf_align(
    side: str,
    klines: Optional[Sequence[Mapping[str, Any]]],
    cfg: Mapping[str, Any],
    *,
    interval: Optional[str] = None,
) -> SpikeHtfDecision:
    """Удобная обёртка для одного набора свечей (обычно 1h)."""
    htf_cfg = read_spike_htf_cfg(cfg)
    if not htf_cfg.enabled:
        return SpikeHtfDecision(allowed=True, reason="htf_align: disabled")
    iv = str(interval or (htf_cfg.intervals[0] if htf_cfg.intervals else "60"))
    return evaluate_spike_htf_klines(
        side,
        {iv: list(klines or [])},
        htf_cfg,
    )
