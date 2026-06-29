"""Цена SL в безубыток с учётом комиссии open+close (Bybit linear, taker)."""
from __future__ import annotations

# Taker ~0.055% × 2 стороны ≈ 0.11%; + проскальзывание на стопе
BYBIT_TAKER_FEE_RATE_PER_SIDE = 0.00055
DEFAULT_BE_FEE_BUFFER_PCT = 0.15
MIN_BE_FEE_BUFFER_PCT = 0.12


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
    fee_rate_per_side: float = BYBIT_TAKER_FEE_RATE_PER_SIDE,
    slippage_margin_pct: float = 0.04,
) -> float:
    """Оценка буфера из ставки комиссии (обе стороны) + запас на проскальзывание."""
    return fee_rate_per_side * 2.0 * 100.0 + max(0.0, slippage_margin_pct)


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
    if str(side).lower() in ("buy", "long"):
        return entry + buf
    return entry - buf
