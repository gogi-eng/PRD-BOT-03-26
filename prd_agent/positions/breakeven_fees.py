"""Цена SL в безубыток с учётом комиссии open+close (Bybit linear)."""
from __future__ import annotations

from typing import Any, Dict, Mapping, Optional

from exchange.bybit_fees import DEFAULT_TAKER_RATE, BybitFeeConfig

# Taker ~0.055% × 2 стороны ≈ 0.11%; + проскальзывание на стопе
DEFAULT_BE_FEE_BUFFER_PCT = 0.15
MIN_BE_FEE_BUFFER_PCT = 0.12
DEFAULT_SLIPPAGE_MARGIN_PCT = 0.04


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


def fee_breakeven_config_from_positions(cfg: Mapping[str, Any]) -> BybitFeeConfig:
    """Ставки комиссий из positions.fee_breakeven или fallback taker×2."""
    positions = cfg.get("positions") if isinstance(cfg.get("positions"), dict) else {}
    raw = positions.get("fee_breakeven") if isinstance(positions.get("fee_breakeven"), dict) else {}
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
    positions = cfg.get("positions") if isinstance(cfg.get("positions"), dict) else {}
    raw = positions.get("fee_breakeven") if isinstance(positions.get("fee_breakeven"), dict) else {}
    try:
        return float(raw.get("slippage_margin_pct", DEFAULT_SLIPPAGE_MARGIN_PCT) or DEFAULT_SLIPPAGE_MARGIN_PCT)
    except (TypeError, ValueError):
        return DEFAULT_SLIPPAGE_MARGIN_PCT


def fee_buffer_pct_from_bot_cfg(
    cfg: Mapping[str, Any],
    *,
    yaml_override: float | None = None,
) -> float:
    """
    Буфер % от entry: комиссия open+close (taker/maker из config) + slippage.
    yaml_override — be_fee_buffer_pct из tp_progress_exit (не ниже расчётного минимума).
    """
    fee_cfg = fee_breakeven_config_from_positions(cfg)
    slip = slippage_margin_pct_from_cfg(cfg)
    computed = round_trip_fee_buffer_pct(
        fee_rate_per_side=fee_cfg.entry_rate(),
        slippage_margin_pct=slip,
    )
    computed_exit = fee_cfg.entry_rate() * 100.0 + fee_cfg.exit_rate() * 100.0 + slip
    computed = max(computed, computed_exit, MIN_BE_FEE_BUFFER_PCT)
    if yaml_override is not None:
        return effective_be_fee_buffer_pct(max(yaml_override, computed))
    return max(computed, MIN_BE_FEE_BUFFER_PCT)


def breakeven_stop_price(
    side: str,
    entry: float,
    fee_buffer_pct: float | None = None,
) -> float:
    """
    Уровень SL: при срабатывании цена покрывает комиссии (не «голый» entry).

    fee_buffer_pct — суммарный % от entry (open+close+slippage), не ставка одной ноги.
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
) -> float:
    """PnL % при выходе по stop с учётом комиссий open+close."""
    gross = gross_pnl_pct_at_stop(side, entry, stop)
    cfg = fee_cfg or BybitFeeConfig()
    if not cfg.enabled:
        return gross
    return gross - cfg.round_trip_fee_pct()


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
