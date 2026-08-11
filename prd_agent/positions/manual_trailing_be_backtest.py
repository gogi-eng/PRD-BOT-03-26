"""
Бэктест трейлинга / BE+ на ручных позициях (origin=manual).

Сравнивает два сценария по одним и тем же свечам:
1) hold — SL/TP как выставил человек, бот не двигает стоп;
2) manage — бот применяет BE+ и trailing (как position_steward для всех сделок).

Не торгует на бирже — только симуляция.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Dict, List, Mapping, Optional, Sequence

from prd_agent.positions.breakeven_fees import breakeven_stop_price
from prd_agent.positions.exit_management import profit_pct
from prd_agent.positions.trailing_after_be import (
    TrailingAfterBeConfig,
    apply_trailing_after_be_distance,
    sl_is_at_or_beyond_be,
)
from prd_agent.positions.tp_progress_exit import (
    TpProgressExitConfig,
    evaluate_tp_progress_exit,
)


@dataclass
class ManualTrailBeParams:
    """Параметры как в positions.* (sandbox/prod)."""

    trailing_activation_pct: float = 2.5
    trailing_distance_pct: float = 3.5
    trailing_distance_atr_mult: float = 2.2
    trailing_min_distance_pct: float = 1.8
    breakeven_after_pct: float = 2.0
    be_fee_buffer_pct: float = 0.3
    be_lock_extra_pct: float = 1.0
    be_at_profit_pct: float = 1.0
    trailing_after_be_enabled: bool = True
    trailing_after_be_reduce_pct: float = 0.5
    apply_to_manual: bool = True

    @classmethod
    def from_positions_cfg(cls, p: Mapping[str, Any]) -> "ManualTrailBeParams":
        tp = p.get("tp_progress_exit") if isinstance(p.get("tp_progress_exit"), dict) else {}
        tab = p.get("trailing_after_be") if isinstance(p.get("trailing_after_be"), dict) else {}
        return cls(
            trailing_activation_pct=float(p.get("trailing_activation_pct", 2.5) or 2.5),
            trailing_distance_pct=float(p.get("trailing_distance_pct", 3.5) or 3.5),
            trailing_distance_atr_mult=float(p.get("trailing_distance_atr_mult", 2.2) or 2.2),
            trailing_min_distance_pct=float(p.get("trailing_min_distance_pct", 1.8) or 1.8),
            breakeven_after_pct=float(p.get("breakeven_after_pct", 2.0) or 2.0),
            be_fee_buffer_pct=float(tp.get("be_fee_buffer_pct", 0.3) or 0.3),
            be_lock_extra_pct=float(tp.get("be_lock_extra_pct", 1.0) or 1.0),
            be_at_profit_pct=float(tp.get("breakeven_at_profit_pct", 1.0) or 1.0),
            trailing_after_be_enabled=bool(tab.get("enabled", True)),
            trailing_after_be_reduce_pct=float(tab.get("distance_reduce_pct", 0.5) or 0.5),
            apply_to_manual=bool(
                (p.get("companion") or {}).get("apply_to_manual", True)
                if isinstance(p.get("companion"), dict)
                else True
            ),
        )


@dataclass
class ManualTrailBeResult:
    mode: str  # hold | manage
    outcome: str  # take_profit | stop_loss | still_open | invalid
    exit_price: float
    pnl_pct: float
    candles_used: int
    sl_updates: int
    final_sl: float
    final_tp: float

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def _kline_ts_ms(k: Mapping[str, Any]) -> int:
    raw = k.get("startTime") or k.get("timestamp") or k.get("open_time") or 0
    try:
        return int(raw)
    except (TypeError, ValueError):
        return 0


def _ohlc(k: Mapping[str, Any]) -> tuple[float, float, float, float]:
    return (
        float(k.get("open", 0) or 0),
        float(k.get("high", 0) or 0),
        float(k.get("low", 0) or 0),
        float(k.get("close", 0) or 0),
    )


def _simple_atr(klines: Sequence[Mapping[str, Any]], period: int = 14) -> float:
    if len(klines) < 2:
        return 0.0
    trs: List[float] = []
    prev_c = float(klines[0].get("close", 0) or 0)
    for k in klines[1:]:
        h, l, c = float(k.get("high", 0) or 0), float(k.get("low", 0) or 0), float(k.get("close", 0) or 0)
        tr = max(h - l, abs(h - prev_c), abs(l - prev_c))
        trs.append(tr)
        prev_c = c
    if not trs:
        return 0.0
    window = trs[-period:] if len(trs) >= period else trs
    return sum(window) / len(window)


def _norm_side(side: str) -> str:
    s = str(side or "").strip().lower()
    if s in ("buy", "long"):
        return "Buy"
    return "Sell"


def _hit_sl_tp(
    side: str,
    high: float,
    low: float,
    sl: float,
    tp: float,
) -> Optional[str]:
    """Кто сработал на свече. При касании обоих — консервативно SL первым."""
    side_n = side if side in ("Buy", "Sell") else _norm_side(side)
    is_long = side_n == "Buy"
    if is_long:
        sl_hit = sl > 0 and low <= sl
        tp_hit = tp > 0 and high >= tp
        if sl_hit and tp_hit:
            return "stop_loss"
        if sl_hit:
            return "stop_loss"
        if tp_hit:
            return "take_profit"
    else:
        sl_hit = sl > 0 and high >= sl
        tp_hit = tp > 0 and low <= tp
        if sl_hit and tp_hit:
            return "stop_loss"
        if sl_hit:
            return "stop_loss"
        if tp_hit:
            return "take_profit"
    return None


def _calc_trail_sl(
    *,
    side: str,
    entry: float,
    price: float,
    best_price: float,
    current_sl: float,
    atr: float,
    params: ManualTrailBeParams,
    distance_pct: float,
) -> Optional[float]:
    side_n = side if side in ("Buy", "Sell") else _norm_side(side)
    p_pct = profit_pct(side_n, entry, price)
    if p_pct < params.breakeven_after_pct and p_pct < params.trailing_activation_pct:
        return None
    if p_pct < params.trailing_activation_pct:
        return None

    is_long = side_n == "Buy"
    ref = best_price if best_price > 0 else price
    dist_pct = ref * distance_pct / 100.0
    dist_atr = atr * params.trailing_distance_atr_mult if atr > 0 else 0.0
    dist = max(dist_pct, dist_atr)
    if params.trailing_min_distance_pct > 0:
        dist = max(dist, ref * params.trailing_min_distance_pct / 100.0)
    if dist <= 0:
        return None

    lock = params.be_fee_buffer_pct + max(0.0, params.be_lock_extra_pct)
    be_floor = breakeven_stop_price(side_n, entry, lock)

    if is_long:
        new_sl = best_price - dist
        new_sl = max(new_sl, be_floor)
        if current_sl > 0:
            new_sl = max(new_sl, current_sl)
        if new_sl >= price:
            return None
        return new_sl

    new_sl = best_price + dist
    new_sl = min(new_sl, be_floor)
    if current_sl > 0:
        new_sl = min(new_sl, current_sl)
    if new_sl <= price:
        return None
    return new_sl


def simulate_manual_trailing_be(
    *,
    side: str,
    entry: float,
    stop_loss: float,
    take_profit: float,
    klines: Sequence[Mapping[str, Any]],
    entry_ts_ms: int = 0,
    params: Optional[ManualTrailBeParams] = None,
    manage: bool = True,
) -> ManualTrailBeResult:
    """
    manage=False → hold исходных SL/TP.
    manage=True  → BE+ + trailing (как бот на ручных при apply_to_manual).
    """
    params = params or ManualTrailBeParams()
    side_n = _norm_side(side)
    mode = "manage" if manage else "hold"
    if entry <= 0 or not klines:
        return ManualTrailBeResult(
            mode=mode,
            outcome="invalid",
            exit_price=0.0,
            pnl_pct=0.0,
            candles_used=0,
            sl_updates=0,
            final_sl=float(stop_loss or 0),
            final_tp=float(take_profit or 0),
        )

    if manage and not params.apply_to_manual:
        manage = False
        mode = "hold"

    sl = float(stop_loss or 0)
    tp = float(take_profit or 0)
    is_long = side_n == "Buy"
    if sl <= 0:
        sl = entry * 0.995 if is_long else entry * 1.005
    if tp <= 0:
        tp = entry * 1.01 if is_long else entry * 0.99

    best = entry
    sl_updates = 0
    used = 0
    tp_cfg = TpProgressExitConfig(
        enabled=True,
        breakeven_at_profit_pct=params.be_at_profit_pct,
        sr_trail_at_profit_pct=99.0,
        sr_trail_enabled=False,
        be_fee_buffer_pct=params.be_fee_buffer_pct,
        be_lock_extra_pct=params.be_lock_extra_pct,
    )
    after_be = TrailingAfterBeConfig(
        enabled=params.trailing_after_be_enabled,
        distance_reduce_pct=params.trailing_after_be_reduce_pct,
    )
    be_total = params.be_fee_buffer_pct + max(0.0, params.be_lock_extra_pct)
    dist_pct = params.trailing_distance_pct

    path = [k for k in klines if _kline_ts_ms(k) >= entry_ts_ms] if entry_ts_ms else list(klines)
    if not path:
        path = list(klines)

    for i, k in enumerate(path):
        used = i + 1
        _o, high, low, close = _ohlc(k)
        if high <= 0 or low <= 0 or close <= 0:
            continue

        # Сначала проверка касания текущих уровней на свече
        hit = _hit_sl_tp(side_n, high, low, sl, tp)
        if hit == "stop_loss":
            pnl = profit_pct(side_n, entry, sl)
            return ManualTrailBeResult(
                mode=mode,
                outcome="stop_loss",
                exit_price=sl,
                pnl_pct=pnl,
                candles_used=used,
                sl_updates=sl_updates,
                final_sl=sl,
                final_tp=tp,
            )
        if hit == "take_profit":
            pnl = profit_pct(side_n, entry, tp)
            return ManualTrailBeResult(
                mode=mode,
                outcome="take_profit",
                exit_price=tp,
                pnl_pct=pnl,
                candles_used=used,
                sl_updates=sl_updates,
                final_sl=sl,
                final_tp=tp,
            )

        if not manage:
            continue

        # Обновление best по экстремуму свечи (в сторону прибыли)
        if is_long:
            best = max(best, high)
        else:
            best = min(best, low) if best > 0 else low

        atr = _simple_atr(path[: i + 1])
        # BE+ через tp_progress (без S/R)
        be_res = evaluate_tp_progress_exit(
            cfg=tp_cfg,
            side=side_n,
            entry=entry,
            price=close,
            take_profit=tp,
            current_sl=sl,
            klines=list(path[: i + 1]),
            atr=atr,
            min_activation_profit_pct=0.0,
        )
        if be_res.suggested_sl and be_res.suggested_sl > 0:
            new_sl = float(be_res.suggested_sl)
            if is_long and new_sl > sl + 1e-12:
                sl = new_sl
                sl_updates += 1
            elif (not is_long) and (sl <= 0 or new_sl < sl - 1e-12):
                sl = new_sl
                sl_updates += 1

        dist_pct, _note = apply_trailing_after_be_distance(
            params.trailing_distance_pct,
            cfg=after_be,
            min_distance_pct=params.trailing_min_distance_pct,
            side=side_n,
            entry=entry,
            stop_loss=sl,
            be_buffer_pct=be_total,
        )

        trail = _calc_trail_sl(
            side=side_n,
            entry=entry,
            price=close,
            best_price=best,
            current_sl=sl,
            atr=atr,
            params=params,
            distance_pct=dist_pct,
        )
        if trail is not None:
            if is_long and trail > sl + 1e-12:
                sl = trail
                sl_updates += 1
            elif (not is_long) and (sl <= 0 or trail < sl - 1e-12):
                sl = trail
                sl_updates += 1

    last_close = float(path[-1].get("close", entry) or entry)
    return ManualTrailBeResult(
        mode=mode,
        outcome="still_open",
        exit_price=last_close,
        pnl_pct=profit_pct(side_n, entry, last_close),
        candles_used=used,
        sl_updates=sl_updates,
        final_sl=sl,
        final_tp=tp,
    )


def compare_hold_vs_manage(
    *,
    side: str,
    entry: float,
    stop_loss: float,
    take_profit: float,
    klines: Sequence[Mapping[str, Any]],
    entry_ts_ms: int = 0,
    params: Optional[ManualTrailBeParams] = None,
) -> Dict[str, Any]:
    hold = simulate_manual_trailing_be(
        side=side,
        entry=entry,
        stop_loss=stop_loss,
        take_profit=take_profit,
        klines=klines,
        entry_ts_ms=entry_ts_ms,
        params=params,
        manage=False,
    )
    manage = simulate_manual_trailing_be(
        side=side,
        entry=entry,
        stop_loss=stop_loss,
        take_profit=take_profit,
        klines=klines,
        entry_ts_ms=entry_ts_ms,
        params=params,
        manage=True,
    )
    return {
        "hold": hold.to_dict(),
        "manage": manage.to_dict(),
        "delta_pnl_pct": manage.pnl_pct - hold.pnl_pct,
        "manage_better": manage.pnl_pct > hold.pnl_pct,
    }


def summarize_comparisons(rows: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
    if not rows:
        return {"n": 0}
    n = len(rows)
    manage_better = sum(1 for r in rows if r.get("manage_better"))
    hold_better = sum(1 for r in rows if (r.get("delta_pnl_pct") or 0) < 0)
    avg_delta = sum(float(r.get("delta_pnl_pct") or 0) for r in rows) / n
    avg_hold = sum(float((r.get("hold") or {}).get("pnl_pct") or 0) for r in rows) / n
    avg_manage = sum(float((r.get("manage") or {}).get("pnl_pct") or 0) for r in rows) / n
    return {
        "n": n,
        "manage_better_n": manage_better,
        "hold_better_n": hold_better,
        "tie_n": n - manage_better - hold_better,
        "avg_delta_pnl_pct": round(avg_delta, 4),
        "avg_hold_pnl_pct": round(avg_hold, 4),
        "avg_manage_pnl_pct": round(avg_manage, 4),
    }
