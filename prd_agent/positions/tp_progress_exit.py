"""
Выход по прогрессу к take profit: безубыток на 20–40% пути, трейлинг SL за S/R после 50%+.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from analysis.structure_zones import StructureZoneAnalyzer

from prd_agent.positions.sr_sl_tp_adjust import _simple_atr


@dataclass
class TpProgressExitConfig:
    enabled: bool = True
    breakeven_at_progress_pct: float = 30.0
    sr_trail_at_progress_pct: float = 50.0
    be_fee_buffer_pct: float = 0.05
    sr_trail_enabled: bool = True
    sr_sl_buffer_atr: float = 0.15
    min_valid_tp_distance_pct: float = 0.08

    @classmethod
    def from_cfg(cls, positions_cfg: Dict[str, Any]) -> TpProgressExitConfig:
        raw = positions_cfg.get("tp_progress_exit")
        if not isinstance(raw, dict):
            raw = {}
        return cls(
            enabled=bool(raw.get("enabled", True)),
            breakeven_at_progress_pct=float(raw.get("breakeven_at_progress_pct", 30) or 30),
            sr_trail_at_progress_pct=float(raw.get("sr_trail_at_progress_pct", 50) or 50),
            be_fee_buffer_pct=float(raw.get("be_fee_buffer_pct", 0.05) or 0.05),
            sr_trail_enabled=bool(raw.get("sr_trail_enabled", True)),
            sr_sl_buffer_atr=float(raw.get("sr_sl_buffer_atr", 0.15) or 0.15),
            min_valid_tp_distance_pct=float(raw.get("min_valid_tp_distance_pct", 0.08) or 0.08),
        )


@dataclass
class TpProgressResult:
    progress_pct: Optional[float]
    suggested_sl: Optional[float]
    phase: str
    note: str


def progress_to_take_profit_pct(
    side: str,
    entry: float,
    price: float,
    take_profit: float,
) -> Optional[float]:
    """Доля пути entry → TP в процентах (0 = вход, 100 = TP)."""
    if entry <= 0 or price <= 0 or take_profit <= 0:
        return None
    is_buy = str(side).lower() in ("buy", "long")
    if is_buy:
        total = take_profit - entry
        if total <= 0:
            return None
        return (price - entry) / total * 100.0
    total = entry - take_profit
    if total <= 0:
        return None
    return (entry - price) / total * 100.0


def is_take_profit_valid(
    side: str,
    entry: float,
    take_profit: float,
    *,
    min_distance_pct: float = 0.08,
) -> bool:
    if entry <= 0 or take_profit <= 0:
        return False
    is_buy = str(side).lower() in ("buy", "long")
    dist_pct = abs(take_profit - entry) / entry * 100.0
    if dist_pct < min_distance_pct:
        return False
    if is_buy:
        return take_profit > entry
    return take_profit < entry


def breakeven_stop_price(side: str, entry: float, fee_buffer_pct: float) -> float:
    buf = entry * max(0.0, fee_buffer_pct) / 100.0
    if str(side).lower() in ("buy", "long"):
        return entry + buf
    return entry - buf


def _nearest_support_sl_long(price: float, zc, atr: float, buffer_atr: float) -> Optional[float]:
    supports = sorted(
        set(zc.support_levels)
        | {z.low for z in zc.all_bullish_zones if not z.mitigated},
    )
    below = [s for s in supports if s < price]
    if below:
        sl = max(below) - buffer_atr * atr
    else:
        sl = zc.structural_sl_long(price, atr)
    if sl <= 0 or sl >= price:
        return None
    return sl


def _nearest_resistance_sl_short(price: float, zc, atr: float, buffer_atr: float) -> Optional[float]:
    resists = sorted(
        set(zc.resistance_levels)
        | {z.high for z in zc.all_bearish_zones if not z.mitigated},
    )
    above = [r for r in resists if r > price]
    if above:
        sl = min(above) + buffer_atr * atr
    else:
        sl = zc.structural_sl_short(price, atr)
    if sl <= 0 or sl <= price:
        return None
    return sl


def trailing_sl_behind_sr(
    side: str,
    price: float,
    klines: List[dict],
    *,
    atr: float = 0.0,
    buffer_atr: float = 0.15,
) -> Optional[float]:
    """SL за ближайшей поддержкой (LONG) или сопротивлением (SHORT) относительно текущей цены."""
    if len(klines) < 10 or price <= 0:
        return None
    if atr <= 0:
        atr = _simple_atr(klines)
    if atr <= 0:
        atr = price * 0.005
    zc = StructureZoneAnalyzer().analyze(klines, price)
    is_buy = str(side).lower() in ("buy", "long")
    if is_buy:
        return _nearest_support_sl_long(price, zc, atr, buffer_atr)
    return _nearest_resistance_sl_short(price, zc, atr, buffer_atr)


def tighten_stop(
    side: str,
    current_sl: float,
    candidate: float,
    price: float,
) -> Optional[float]:
    """Оставляет только ужесточение SL (выше для LONG, ниже для SHORT) и ниже/выше цены."""
    if candidate <= 0 or price <= 0:
        return None
    is_buy = str(side).lower() in ("buy", "long")
    if is_buy:
        if candidate >= price:
            return None
        if current_sl > 0 and candidate <= current_sl:
            return None
        return candidate
    if candidate <= price:
        return None
    if current_sl > 0 and candidate >= current_sl:
        return None
    return candidate


def evaluate_tp_progress_exit(
    *,
    cfg: TpProgressExitConfig,
    side: str,
    entry: float,
    price: float,
    take_profit: float,
    current_sl: float,
    klines: List[dict],
    atr: float,
) -> TpProgressResult:
    if not cfg.enabled:
        return TpProgressResult(None, None, "off", "tp_progress disabled")

    if not is_take_profit_valid(
        side,
        entry,
        take_profit,
        min_distance_pct=cfg.min_valid_tp_distance_pct,
    ):
        return TpProgressResult(None, None, "no_tp", "нет валидного TP")

    progress = progress_to_take_profit_pct(side, entry, price, take_profit)
    if progress is None:
        return TpProgressResult(None, None, "no_progress", "не считается прогресс")

    be_sl = breakeven_stop_price(side, entry, cfg.be_fee_buffer_pct)
    suggested: Optional[float] = None
    phase = "none"
    note = f"прогресс {progress:.0f}% к TP"

    if progress >= cfg.breakeven_at_progress_pct:
        suggested = tighten_stop(side, current_sl, be_sl, price)
        phase = "breakeven"
        note = f"BE @ {progress:.0f}% пути к TP"

    if (
        cfg.sr_trail_enabled
        and progress >= cfg.sr_trail_at_progress_pct
        and klines
    ):
        sr_sl = trailing_sl_behind_sr(
            side,
            price,
            klines,
            atr=atr,
            buffer_atr=cfg.sr_sl_buffer_atr,
        )
        if sr_sl is not None:
            sr_tight = tighten_stop(side, current_sl, sr_sl, price)
            if sr_tight is not None:
                if suggested is None:
                    suggested = sr_tight
                elif str(side).lower() in ("buy", "long"):
                    suggested = max(suggested, sr_tight)
                else:
                    suggested = min(suggested, sr_tight)
                phase = "sr_trail"
                note = f"S/R трейл @ {progress:.0f}% к TP"

    return TpProgressResult(progress, suggested, phase, note)


def cycle_breakeven_threshold(current: float) -> float:
    """Цикл 20 → 30 → 40 % для кнопки Telegram."""
    steps = (20.0, 30.0, 40.0)
    for i, v in enumerate(steps):
        if abs(current - v) < 0.5:
            return steps[(i + 1) % len(steps)]
    return 30.0


def format_tp_progress_status(cfg: TpProgressExitConfig) -> str:
    state = "ВКЛ" if cfg.enabled else "ВЫКЛ"
    return (
        f"Выход по TP: <b>{state}</b>\n"
        f"BE после <code>{cfg.breakeven_at_progress_pct:.0f}%</code> пути к цели\n"
        f"S/R трейл после <code>{cfg.sr_trail_at_progress_pct:.0f}%</code>"
    )
