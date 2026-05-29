"""Вход на откате: не покупать/продавать в погоне за импульсом."""
from __future__ import annotations

from typing import Any, Dict, List, Tuple

from prd_agent.signals.types import UnifiedSignal


def _closes_from_klines(klines: List[Dict]) -> List[float]:
    out: List[float] = []
    for k in klines:
        try:
            out.append(float(k.get("close", 0) or 0))
        except (TypeError, ValueError):
            continue
    return out


def check_pullback_entry(
    sig: UnifiedSignal,
    klines: List[Dict],
    cfg: Dict[str, Any],
) -> Tuple[bool, str]:
    """
    WAIT_PULLBACK = последние N свечей шли по импульсу → пропуск (ждём откат).
    ENTER_NOW = флэт или откат уже есть.
    """
    pe = cfg.get("pullback_entry", {})
    if not isinstance(pe, dict):
        pe = {}
    if not bool(pe.get("enabled", True)):
        return True, ""

    bars = max(3, int(pe.get("momentum_bars", 5) or 5))
    closes = _closes_from_klines(klines)
    if len(closes) < bars + 1:
        return True, ""

    momentum = closes[-1] - closes[-1 - bars]
    side_u = str(sig.side or "").upper()
    chasing = (side_u == "BUY" and momentum > 0) or (side_u == "SELL" and momentum < 0)
    if chasing:
        return False, (
            f"pullback_entry: импульс {bars}x15m против входа "
            f"(Δ={momentum:.6g}), ждём откат по тренду"
        )
    return True, ""
