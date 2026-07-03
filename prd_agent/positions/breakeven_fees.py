"""Цена SL в безубыток: комиссия вход+выход + финансирование (Bybit linear)."""
from __future__ import annotations

import math
from typing import Any, Dict, Mapping, Optional

from exchange.bybit_fees import DEFAULT_TAKER_RATE, BybitFeeConfig

# Taker ~0.055% × 2 стороны ≈ 0.11%; + проскальзывание на стопе
DEFAULT_BE_FEE_BUFFER_PCT = 0.15
MIN_BE_FEE_BUFFER_PCT = 0.12
DEFAULT_SLIPPAGE_MARGIN_PCT = 0.04
DEFAULT_FUNDING_INTERVAL_HOURS = 8.0
DEFAULT_FUNDING_RATE_ASSUMED = 0.0001  # 0.01% номинала за 8ч (консервативно)
DEFAULT_FUNDING_RESERVE_HOURS = 24.0
DEFAULT_FUNDING_RATE_CAP = 0.001


def _is_long(side: str) -> bool:
    return str(side or "").strip().lower() in ("buy", "long")


def effective_be_fee_buffer_pct(configured: float | None) -> float:
    """Итоговый буфер % от цены входа (не ниже минимума для round-trip)."""
    try:
        cfg = float(configured or 0.0)
    except (TypeError, ValueError):
        cfg = 0.0
    if cfg <= 0:
        return DEFAULT_BE_FEE_BUFFER_PCT
    return max(cfg, MIN_BE_FEE_BUFFER_PCT)


def round_trip_fee_buffer_pct(
    fee_rate_per_side: float = DEFAULT_TAKER_RATE,
    slippage_margin_pct: float = DEFAULT_SLIPPAGE_MARGIN_PCT,
) -> float:
    """Оценка буфера из ставки комиссии (обе стороны) + запас на проскальзывание."""
    return fee_rate_per_side * 2.0 * 100.0 + max(0.0, slippage_margin_pct)


def _fee_breakeven_raw(cfg: Mapping[str, Any]) -> Dict[str, Any]:
    positions = cfg.get("positions") if isinstance(cfg.get("positions"), dict) else {}
    raw = positions.get("fee_breakeven") if isinstance(positions.get("fee_breakeven"), dict) else {}
    return raw if isinstance(raw, dict) else {}


def fee_breakeven_config_from_positions(cfg: Mapping[str, Any]) -> BybitFeeConfig:
    """Ставки комиссий из positions.fee_breakeven или fallback taker×2."""
    raw = _fee_breakeven_raw(cfg)
    if not raw:
        return BybitFeeConfig()
    taker = float(raw.get("taker_rate", DEFAULT_TAKER_RATE) or DEFAULT_TAKER_RATE)
    maker = float(raw.get("maker_rate", BybitFeeConfig().maker_rate) or BybitFeeConfig().maker_rate)
    return BybitFeeConfig(
        enabled=True,
        taker_rate=taker,
        maker_rate=maker,
        entry_as_maker=bool(raw.get("entry_as_maker", False)),
        exit_as_maker=bool(raw.get("exit_as_maker", False)),
    )


def slippage_margin_pct_from_cfg(cfg: Mapping[str, Any]) -> float:
    raw = _fee_breakeven_raw(cfg)
    try:
        return float(raw.get("slippage_margin_pct", DEFAULT_SLIPPAGE_MARGIN_PCT) or DEFAULT_SLIPPAGE_MARGIN_PCT)
    except (TypeError, ValueError):
        return DEFAULT_SLIPPAGE_MARGIN_PCT


def fee_multiplier_from_cfg(cfg: Mapping[str, Any]) -> float:
    raw = _fee_breakeven_raw(cfg)
    try:
        mult = float(raw.get("fee_multiplier", 1.5) or 1.5)
    except (TypeError, ValueError):
        mult = 1.5
    return max(1.0, mult)


def funding_buffer_pct(
    cfg: Mapping[str, Any],
    *,
    hold_hours: float | None = None,
    funding_rate_per_interval: float | None = None,
) -> float:
    """
    Резерв % от entry на финансирование (каждые funding_interval_hours).

    hold_hours — сколько позиция уже открыта; если неизвестно — funding_reserve_hours из config.
    """
    raw = _fee_breakeven_raw(cfg)
    if not bool(raw.get("include_funding", True)):
        return 0.0
    try:
        interval_h = float(raw.get("funding_interval_hours", DEFAULT_FUNDING_INTERVAL_HOURS) or DEFAULT_FUNDING_INTERVAL_HOURS)
    except (TypeError, ValueError):
        interval_h = DEFAULT_FUNDING_INTERVAL_HOURS
    interval_h = max(1.0, interval_h)
    try:
        reserve_h = float(raw.get("funding_reserve_hours", DEFAULT_FUNDING_RESERVE_HOURS) or DEFAULT_FUNDING_RESERVE_HOURS)
    except (TypeError, ValueError):
        reserve_h = DEFAULT_FUNDING_RESERVE_HOURS
    try:
        assumed = float(raw.get("funding_rate_assumed", DEFAULT_FUNDING_RATE_ASSUMED) or DEFAULT_FUNDING_RATE_ASSUMED)
    except (TypeError, ValueError):
        assumed = DEFAULT_FUNDING_RATE_ASSUMED
    try:
        rate_cap = float(raw.get("funding_rate_cap", DEFAULT_FUNDING_RATE_CAP) or DEFAULT_FUNDING_RATE_CAP)
    except (TypeError, ValueError):
        rate_cap = DEFAULT_FUNDING_RATE_CAP
    if funding_rate_per_interval is not None:
        rate = abs(float(funding_rate_per_interval))
    else:
        rate = assumed
    rate = min(max(rate, assumed), rate_cap)
    if hold_hours is not None and hold_hours > 0:
        hours = max(hold_hours, reserve_h)
    else:
        hours = reserve_h
    intervals = max(1.0, math.ceil(hours / interval_h))
    return intervals * rate * 100.0


def fee_buffer_pct_from_bot_cfg(
    cfg: Mapping[str, Any],
    *,
    yaml_override: float | None = None,
    hold_hours: float | None = None,
    funding_rate: float | None = None,
) -> float:
    """
    Буфер % от entry для безубытка:
    fee_multiplier × (комиссия вход+выход) + финансирование за время удержания.
    """
    fee_cfg = fee_breakeven_config_from_positions(cfg)
    mult = fee_multiplier_from_cfg(cfg)
    round_trip_pct = (fee_cfg.entry_rate() + fee_cfg.exit_rate()) * 100.0
    trade_buf = mult * round_trip_pct
    funding_buf = funding_buffer_pct(cfg, hold_hours=hold_hours, funding_rate_per_interval=funding_rate)
    computed = trade_buf + funding_buf
    if yaml_override is not None:
        try:
            y = float(yaml_override)
        except (TypeError, ValueError):
            y = 0.0
        if y > 0:
            return max(computed, y, MIN_BE_FEE_BUFFER_PCT)
    return max(computed, MIN_BE_FEE_BUFFER_PCT)


def breakeven_stop_price(
    side: str,
    entry: float,
    fee_buffer_pct: float | None = None,
) -> float:
    """
    Уровень SL: при срабатывании цена покрывает комиссии и финансирование.

    Long: entry + buffer (выше входа).
    Short: entry - buffer (ниже входа).
    """
    if entry <= 0:
        return 0.0
    pct = effective_be_fee_buffer_pct(fee_buffer_pct)
    buf = entry * pct / 100.0
    if _is_long(side):
        return entry + buf
    return entry - buf


def gross_pnl_pct_at_stop(side: str, entry: float, stop: float) -> float:
    if entry <= 0 or stop <= 0:
        return 0.0
    if _is_long(side):
        return (stop - entry) / entry * 100.0
    return (entry - stop) / entry * 100.0


def net_pnl_pct_at_stop(
    side: str,
    entry: float,
    stop: float,
    fee_cfg: Optional[BybitFeeConfig] = None,
    *,
    funding_buffer_pct_val: float = 0.0,
) -> float:
    """PnL % при выходе по stop с учётом комиссий open+close и финансирования."""
    gross = gross_pnl_pct_at_stop(side, entry, stop)
    cfg = fee_cfg or BybitFeeConfig()
    if not cfg.enabled:
        return gross - max(0.0, funding_buffer_pct_val)
    return gross - cfg.round_trip_fee_pct() - max(0.0, funding_buffer_pct_val)


def is_stop_in_loss_after_fees(
    side: str,
    entry: float,
    stop: float,
    fee_buffer_pct: float | None = None,
) -> bool:
    """True если SL хуже безубытка с комиссиями (типичная ошибка S/R-трейла на SHORT)."""
    if entry <= 0 or stop <= 0:
        return False
    be = breakeven_stop_price(side, entry, fee_buffer_pct)
    if _is_long(side):
        return stop < be
    return stop > be


def clamp_sl_for_profit_lock(
    side: str,
    entry: float,
    stop: float,
    fee_buffer_pct: float | None,
    *,
    in_profit: bool,
) -> float:
    """
    Не допускает SL в зону убытка после комиссий, когда позиция в плюсе.

    Long: SL не ниже entry + fee buffer.
    Short: SL не выше entry - fee buffer.
    """
    if not in_profit or entry <= 0 or stop <= 0:
        return stop
    be = breakeven_stop_price(side, entry, fee_buffer_pct)
    if _is_long(side):
        return max(stop, be)
    return min(stop, be)
