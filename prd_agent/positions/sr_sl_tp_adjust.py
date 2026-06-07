"""
Поджатие SL/TP к зонам поддержки/сопротивления (SMC) перед ордером unified-бота.
"""
from __future__ import annotations

from typing import List, Tuple

from analysis.structure_zones import StructureZoneAnalyzer, ZoneContext


def _simple_atr(klines: List[dict], period: int = 14) -> float:
    if len(klines) < 2:
        return 0.0
    n = min(max(period, 5), max(5, len(klines) - 1))
    trs: List[float] = []
    for i in range(1, len(klines)):
        h = float(klines[i].get("high", 0) or 0)
        low_v = float(klines[i].get("low", 0) or 0)
        prev_c = float(klines[i - 1].get("close", 0) or 0)
        if h <= 0 or low_v <= 0:
            continue
        tr = max(h - low_v, abs(h - prev_c), abs(low_v - prev_c))
        trs.append(tr)
    chunk = trs[-n:] if trs else []
    return sum(chunk) / max(len(chunk), 1)


def _zones_meaningful(zc: ZoneContext) -> bool:
    return bool(
        zc.support_levels
        or zc.resistance_levels
        or zc.all_bullish_zones
        or zc.all_bearish_zones
    )


def _rr_ratio(entry: float, sl: float, tp: float, side: str) -> float:
    if entry <= 0 or sl <= 0 or tp <= 0:
        return 0.0
    is_buy = str(side).lower() in ("buy", "long")
    risk = abs(entry - sl)
    reward = abs(tp - entry) if is_buy else abs(entry - tp)
    if risk <= 0:
        return 0.0
    return reward / risk


def _merge_sl_long(orig: float, sr: float, entry: float) -> float:
    if sr >= entry:
        return orig if (orig > 0 and orig < entry) else sr
    if orig > 0 and orig < entry:
        return min(orig, sr)
    return sr


def _merge_tp_long(orig: float, sr: float, entry: float) -> float:
    if sr <= entry:
        return orig if (orig > entry) else sr
    if orig > 0 and orig > entry:
        return min(orig, sr)
    return sr


def _merge_sl_short(orig: float, sr: float, entry: float) -> float:
    if sr <= entry:
        return orig if (orig > entry) else sr
    if orig > 0 and orig > entry:
        return max(orig, sr)
    return sr


def _merge_tp_short(orig: float, sr: float, entry: float) -> float:
    if sr >= entry:
        return orig if (0 < orig < entry) else sr
    if orig > 0 and orig < entry:
        return max(orig, sr)
    return sr


def adjust_sl_tp_with_sr_zones(
    *,
    entry: float,
    side: str,
    stop_loss: float,
    take_profit: float,
    klines: List[dict],
    sl_extra_atr: float = 0.1,
    tp_extra_atr: float = 0.08,
    preserve_min_rr: float = 0.0,
    sl_sr_level_index: int = 1,
    min_tp_distance_pct: float = 1.0,
) -> Tuple[float, float, bool]:
    """
    LONG: SL ниже поддержки, TP у сопротивления (+ отступ в ATR).
    Возвращает (stop_loss, take_profit, changed).
    """
    if len(klines) < 10 or entry <= 0:
        return stop_loss, take_profit, False
    side_u = str(side or "").strip().upper()
    if side_u in ("LONG",):
        side_u = "BUY"
    elif side_u in ("SHORT",):
        side_u = "SELL"
    if side_u not in ("BUY", "SELL"):
        return stop_loss, take_profit, False

    zc = StructureZoneAnalyzer().analyze(klines, entry)
    if not _zones_meaningful(zc):
        return stop_loss, take_profit, False

    atr = _simple_atr(klines)
    if atr <= 0:
        atr = entry * 0.005

    orig_sl = float(stop_loss or 0)
    orig_tp = float(take_profit or 0)
    sl_ex = max(0.0, float(sl_extra_atr or 0))
    tp_ex = max(0.0, float(tp_extra_atr or 0))

    sl_idx = max(0, int(sl_sr_level_index))
    if side_u == "BUY":
        sl_sr = zc.structural_sl_long(entry, atr, level_index=sl_idx) - sl_ex * atr
        tp1, _ = zc.structural_tp_long(entry, atr)
        tp_sr = tp1 - tp_ex * atr
        if sl_sr >= entry or tp_sr <= entry:
            return stop_loss, take_profit, False
        new_sl = _merge_sl_long(orig_sl, sl_sr, entry)
        new_tp = _merge_tp_long(orig_tp, tp_sr, entry)
        if new_sl <= 0 or new_tp <= 0 or new_sl >= entry or new_tp <= entry:
            return stop_loss, take_profit, False
    else:
        sl_sr = zc.structural_sl_short(entry, atr, level_index=sl_idx) + sl_ex * atr
        tp1, _ = zc.structural_tp_short(entry, atr)
        tp_sr = tp1 + tp_ex * atr
        if sl_sr <= entry or tp_sr >= entry:
            return stop_loss, take_profit, False
        new_sl = _merge_sl_short(orig_sl, sl_sr, entry)
        new_tp = _merge_tp_short(orig_tp, tp_sr, entry)
        if new_sl <= 0 or new_tp <= 0 or new_sl <= entry or new_tp >= entry:
            return stop_loss, take_profit, False

    min_tp_pct = max(0.0, float(min_tp_distance_pct or 0))
    if min_tp_pct > 0:
        if side_u == "BUY":
            tp_dist_pct = (new_tp - entry) / entry * 100.0
            if tp_dist_pct < min_tp_pct:
                floor_tp = entry * (1.0 + min_tp_pct / 100.0)
                new_tp = orig_tp if orig_tp > floor_tp else floor_tp
        else:
            tp_dist_pct = (entry - new_tp) / entry * 100.0
            if tp_dist_pct < min_tp_pct:
                floor_tp = entry * (1.0 - min_tp_pct / 100.0)
                new_tp = orig_tp if 0 < orig_tp < floor_tp else floor_tp

    tol = max(abs(entry) * 1e-12, 1e-12)
    if abs(new_sl - orig_sl) < tol and abs(new_tp - orig_tp) < tol:
        return stop_loss, take_profit, False

    rr_need = float(preserve_min_rr or 0)
    if rr_need > 1e-9:
        side_check = "Buy" if side_u == "BUY" else "Sell"
        if _rr_ratio(entry, new_sl, new_tp, side_check) + 1e-9 < rr_need:
            return stop_loss, take_profit, False

    return new_sl, new_tp, True
