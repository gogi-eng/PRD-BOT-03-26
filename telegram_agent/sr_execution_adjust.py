"""Подгонка SL/TP под зоны поддержки/сопротивления (FVG/OB/swing) перед автоисполнением Telegram-агента."""
from __future__ import annotations

from typing import Any, List

from analysis.structure_zones import StructureZoneAnalyzer, ZoneContext
from telegram_agent.risk_pipeline import compute_rr


def simple_atr(klines: List[dict], period: int = 14) -> float:
    if len(klines) < 2:
        return 0.0
    n = min(max(period, 5), max(5, len(klines) - 1))
    trs: list[float] = []
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


def zones_meaningful(zc: ZoneContext) -> bool:
    return bool(zc.support_levels or zc.resistance_levels or zc.all_bullish_zones or zc.all_bearish_zones)


def infer_side_from_zones(
    price: float,
    zc: ZoneContext,
    *,
    near_tolerance_pct: float = 0.35,
) -> str | None:
    """
    BUY/SELL по положению цены относительно SMC-зон (поддержка/сопротивление, OB/FVG).
    Используется когда направление в посте не надёжно или задано только на графике/скрине.
    """
    if price <= 0:
        return None
    tol = max(0.05, float(near_tolerance_pct or 0.35))

    if zc.price_in_bullish_zone(price) or zc.price_near_bullish_zone(price, tol):
        return "BUY"
    if zc.price_in_bearish_zone(price) or zc.price_near_bearish_zone(price, tol):
        return "SELL"

    sup_below = sorted([float(s) for s in zc.support_levels if float(s) < price * 0.999], reverse=True)
    res_above = sorted([float(r) for r in zc.resistance_levels if float(r) > price * 1.001])

    dist_sup = price - sup_below[0] if sup_below else None
    dist_res = res_above[0] - price if res_above else None

    if dist_sup is not None and dist_res is not None:
        if dist_sup * 1.12 < dist_res:
            return "BUY"
        if dist_res * 1.12 < dist_sup:
            return "SELL"
    elif dist_sup is not None:
        return "BUY"
    elif dist_res is not None:
        return "SELL"

    if zc.bullish_confluence and not zc.bearish_confluence:
        return "BUY"
    if zc.bearish_confluence and not zc.bullish_confluence:
        return "SELL"

    bl = zc.best_long_entry_zone()
    sh = zc.best_short_entry_zone()
    if bl is not None and sh is None:
        return "BUY"
    if sh is not None and bl is None:
        return "SELL"
    if bl is not None and sh is not None:
        db = abs(bl.mid - price)
        ds = abs(sh.mid - price)
        if db + 1e-12 < ds * 0.85:
            return "BUY"
        if ds + 1e-12 < db * 0.85:
            return "SELL"
    return None


def adjust_telegram_sl_tp_with_sr_zones(
    signal: Any,
    klines: List[dict],
    *,
    sl_extra_atr: float,
    tp_extra_atr: float,
    preserve_min_rr: float = 0.0,
) -> bool:
    """
    Совмещает исходные SL/TP с уровнями из StructureZoneAnalyzer (поддержка/сопротивление + отступ в ATR).
    LONG: стоп ниже зоны поддержки (− доп.буфер×ATR), тейк у ближайшего сопротивления (− доп.буфер×ATR);
    SHORT: симметрично.

    Возвращает True, если signal.stop_loss / signal.take_profit изменены.
    """
    if len(klines) < 10:
        return False
    entry = float(getattr(signal, "entry", 0.0) or 0.0)
    if entry <= 0:
        return False
    side = str(getattr(signal, "side", "") or "").upper()
    if side not in {"BUY", "SELL"}:
        return False

    zc = StructureZoneAnalyzer().analyze(klines, entry)
    if not zones_meaningful(zc):
        return False

    atr = simple_atr(klines)
    if atr <= 0:
        atr = entry * 0.005

    orig_sl = float(getattr(signal, "stop_loss", 0.0) or 0.0)
    orig_tp = float(getattr(signal, "take_profit", 0.0) or 0.0)
    sl_ex = max(0.0, float(sl_extra_atr or 0.0))
    tp_ex = max(0.0, float(tp_extra_atr or 0.0))

    if side == "BUY":
        sl_sr = zc.structural_sl_long(entry, atr) - sl_ex * atr
        tp1, _ = zc.structural_tp_long(entry, atr)
        tp_sr = tp1 - tp_ex * atr
        if sl_sr >= entry or tp_sr <= entry:
            return False
        new_sl = _merge_sl_long(orig_sl, sl_sr, entry)
        new_tp = _merge_tp_long(orig_tp, tp_sr, entry)
        if new_sl <= 0 or new_tp <= 0 or new_sl >= entry or new_tp <= entry:
            return False
    else:
        sl_sr = zc.structural_sl_short(entry, atr) + sl_ex * atr
        tp1, _ = zc.structural_tp_short(entry, atr)
        tp_sr = tp1 + tp_ex * atr
        if sl_sr <= entry or tp_sr >= entry:
            return False
        new_sl = _merge_sl_short(orig_sl, sl_sr, entry)
        new_tp = _merge_tp_short(orig_tp, tp_sr, entry)
        if new_sl <= 0 or new_tp <= 0 or new_sl <= entry or new_tp >= entry:
            return False

    tol = max(abs(entry) * 1e-12, 1e-12)
    if abs(new_sl - orig_sl) < tol and abs(new_tp - orig_tp) < tol:
        return False

    rr_need = float(preserve_min_rr or 0.0)
    if rr_need > 1e-9:
        rr_new = compute_rr(side, entry, new_sl, new_tp)
        if rr_new + 1e-9 < rr_need:
            return False

    signal.stop_loss = new_sl
    signal.take_profit = new_tp
    return True


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
    """SHORT: TP ниже входа — берём более высокую из двух целей (раньше фиксация у зоны)."""
    if sr >= entry:
        return orig if (0 < orig < entry) else sr
    if orig > 0 and orig < entry:
        return max(orig, sr)
    return sr
