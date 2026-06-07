"""Вход на откате: не гнаться за импульсом, но входить после реального отката."""
from __future__ import annotations

from typing import Any, Dict, List, Tuple

from prd_agent.signals.pump_dump_mode import (
    is_agent_world_signal,
    is_pump_dump_signal,
    pump_dump_trade_enabled,
)
from prd_agent.signals.types import UnifiedSignal


def _closes_from_klines(klines: List[Dict]) -> List[float]:
    out: List[float] = []
    for k in klines:
        try:
            out.append(float(k.get("close", 0) or 0))
        except (TypeError, ValueError):
            continue
    return out


def _momentum_pct(closes: List[float], bars: int) -> float:
    if len(closes) < bars + 1:
        return 0.0
    base = closes[-1 - bars]
    last = closes[-1]
    if base <= 0 or last <= 0:
        return 0.0
    return (last / base - 1.0) * 100.0


def _retrace_pct_from_extreme(side_u: str, closes: List[float], lookback: int) -> float:
    window = closes[-lookback:] if lookback > 0 else closes
    if not window:
        return 0.0
    last = closes[-1]
    if side_u == "BUY":
        hi = max(window)
        if hi <= 0:
            return 0.0
        return (hi - last) / hi * 100.0
    lo = min(window)
    if lo <= 0:
        return 0.0
    return (last - lo) / lo * 100.0


def _counter_trend_bar_count(side_u: str, closes: List[float], n: int) -> int:
    """Сколько последних свечей идут против импульса (откат)."""
    if n <= 0 or len(closes) < 2:
        return 0
    count = 0
    start = max(1, len(closes) - n)
    for i in range(start, len(closes)):
        if side_u == "BUY" and closes[i] < closes[i - 1]:
            count += 1
        elif side_u == "SELL" and closes[i] > closes[i - 1]:
            count += 1
    return count


def _source_skipped(sig: UnifiedSignal, pe: Dict[str, Any]) -> bool:
    skip_sources = pe.get("skip_sources") or pe.get("skip_for_sources") or []
    if not isinstance(skip_sources, list) or not skip_sources:
        return False
    src = str(sig.source or "").lower()
    for tag in skip_sources:
        if str(tag).lower() in src:
            return True
    return False


def check_pullback_entry(
    sig: UnifiedSignal,
    klines: List[Dict],
    cfg: Dict[str, Any],
) -> Tuple[bool, str]:
    """
    Блокируем только сильный импульс по % (не любой Δ в долларах).
    Если импульс есть — пропускаем, пока нет отката от локального экстремума
    или N свечей против тренда.
    """
    pe = cfg.get("pullback_entry", {})
    if not isinstance(pe, dict):
        pe = {}
    if not bool(pe.get("enabled", True)):
        return True, ""

    if _source_skipped(sig, pe):
        return True, ""

    skip_pd = bool(pe.get("skip_for_pump_dump", True))
    if skip_pd and pump_dump_trade_enabled(cfg) and is_pump_dump_signal(sig):
        return True, "pump_dump: вход без ожидания отката (быстрый импульс)"

    if bool(pe.get("skip_for_agent_world", True)) and is_agent_world_signal(sig):
        return True, "agent_world: вход по новости без ожидания отката"

    bars = max(3, int(pe.get("momentum_bars", 5) or 5))
    min_momentum_pct = float(pe.get("min_momentum_pct", 0.35) or 0.35)
    min_retrace_pct = float(pe.get("min_retrace_pct", 0.12) or 0.12)
    retrace_lookback = max(bars, int(pe.get("retrace_lookback_bars", bars) or bars))
    require_counter = max(0, int(pe.get("require_counter_bars", 1) or 1))

    closes = _closes_from_klines(klines)
    if len(closes) < bars + 2:
        return True, ""

    side_u = str(sig.side or "").upper()
    if side_u not in ("BUY", "SELL"):
        return True, ""

    mom_pct = _momentum_pct(closes, bars)
    chasing = (side_u == "BUY" and mom_pct > min_momentum_pct) or (
        side_u == "SELL" and mom_pct < -min_momentum_pct
    )
    if not chasing:
        return True, ""

    retrace = _retrace_pct_from_extreme(side_u, closes, retrace_lookback)
    if retrace >= min_retrace_pct:
        return True, (
            f"pullback_entry: откат {retrace:.2f}% от экстремума "
            f"(импульс {bars}x15m {mom_pct:+.2f}%)"
        )

    counter = _counter_trend_bar_count(side_u, closes, require_counter)
    if require_counter > 0 and counter >= require_counter:
        return True, (
            f"pullback_entry: {counter} свечей отката "
            f"(импульс {bars}x15m {mom_pct:+.2f}%)"
        )

    return False, (
        f"pullback_entry: импульс {bars}x15m {mom_pct:+.2f}% "
        f"(нужен откат ≥{min_retrace_pct:.2f}% или {require_counter} свечей против)"
    )
