"""Минимальный R:R для SL/TP перед отправкой ордера на биржу."""
from __future__ import annotations

from typing import Tuple


def rr_ratio(entry: float, stop_loss: float, take_profit: float, side: str) -> float:
    if entry <= 0 or stop_loss <= 0 or take_profit <= 0:
        return 0.0
    is_buy = str(side or "").lower() in ("buy", "long")
    risk = abs(entry - stop_loss)
    reward = abs(take_profit - entry) if is_buy else abs(entry - take_profit)
    if risk <= 1e-12:
        return 0.0
    return reward / risk


def stretch_take_profit_for_min_rr(
    side: str,
    entry: float,
    stop_loss: float,
    take_profit: float,
    min_rr: float,
) -> float:
    """Расширяет TP при том же SL, пока RR < min_rr."""
    side_u = str(side or "").upper()
    min_rr_f = float(min_rr or 0.0)
    e, slv, tpv = float(entry or 0.0), float(stop_loss or 0.0), float(take_profit or 0.0)
    if min_rr_f <= 0.0 or e <= 0.0 or slv <= 0.0 or tpv <= 0.0:
        return tpv
    if side_u in ("SELL", "SHORT"):
        risk = slv - e
        reward = e - tpv
        if risk <= 1e-12 or reward <= 1e-12:
            return tpv
        need_reward = min_rr_f * risk
        if reward + 1e-12 >= need_reward:
            return tpv
        return e - need_reward
    if side_u in ("BUY", "LONG"):
        risk = e - slv
        reward = tpv - e
        if risk <= 1e-12 or reward <= 1e-12:
            return tpv
        need_reward = min_rr_f * risk
        if reward + 1e-12 >= need_reward:
            return tpv
        return e + need_reward
    return tpv


def resolve_effective_min_rr(
    *,
    quality_min_rr: float = 0.0,
    preserve_min_rr: float = 0.0,
    target_initial_tp_rr: float = 0.0,
    fallback: float = 2.0,
) -> float:
    """Единый порог RR: quality_gate, preserve_min_rr, target_initial_tp_rr (как в orchestrator)."""
    vals = [
        float(quality_min_rr or 0.0),
        float(preserve_min_rr or 0.0),
        float(target_initial_tp_rr or 0.0),
    ]
    best = max(vals)
    if best > 0:
        return best
    return float(fallback or 2.0)


def enforce_min_rr_levels(
    *,
    side: str,
    entry: float,
    stop_loss: float,
    take_profit: float,
    min_rr: float,
) -> Tuple[float, float, bool]:
    """
    Подгоняет TP под min_rr относительно фактической цены входа.
    Возвращает (sl, tp, ok). ok=False если геометрия SL/entry некорректна.
    """
    e = float(entry or 0.0)
    sl = float(stop_loss or 0.0)
    tp = float(take_profit or 0.0)
    min_rr_f = float(min_rr or 0.0)
    if e <= 0 or sl <= 0 or tp <= 0 or min_rr_f <= 0:
        return sl, tp, False

    side_u = str(side or "").lower()
    if side_u in ("buy", "long"):
        if sl >= e or tp <= e:
            return sl, tp, False
    elif side_u in ("sell", "short"):
        if sl <= e or tp >= e:
            return sl, tp, False
    else:
        return sl, tp, False

    new_tp = stretch_take_profit_for_min_rr(side, e, sl, tp, min_rr_f)
    if rr_ratio(e, sl, new_tp, side) + 1e-9 < min_rr_f:
        return sl, tp, False
    return sl, new_tp, True
