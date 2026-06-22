"""SL/TP и BOS для MARKET SCANNER: TP дальше от входа, чем SL (минимальный RR)."""
from __future__ import annotations


def market_scanner_bos_confirmed(
    *,
    scenario: str,
    price: float,
    range_low: float,
    range_high: float,
    bos_buffer_pct: float,
) -> bool:
    """True если цена пробила BOS уровень диапазона с буфером (как в telegram_signal_agent)."""
    buffer = max(0.0, float(bos_buffer_pct or 0.0)) / 100.0
    px = float(price or 0.0)
    if px <= 0:
        return False
    scen = str(scenario or "").upper()
    if scen == "PUMP":
        return px > float(range_high) * (1.0 + buffer)
    if scen == "DUMP":
        return px < float(range_low) * (1.0 - buffer)
    return False


def market_scanner_invalidation_and_target(
    *,
    scenario: str,
    price: float,
    range_low: float,
    range_high: float,
    bos_level: float,
    bos_buffer_pct: float,
    min_rr: float,
) -> tuple[float, float]:
    """
    PUMP (long): SL у BOS/поддержки, не на дне всего диапазона; TP расширяется под min_rr.
    DUMP (short): симметрично.
    """
    span = max(float(range_high) - float(range_low), 1e-12)
    buffer = max(0.0, float(bos_buffer_pct or 0.0)) / 100.0
    px = float(price or 0.0)
    min_rr_f = max(1.0, float(min_rr or 2.0))
    scen = str(scenario or "").upper()

    if scen == "PUMP":
        sl_bos = float(bos_level) * (1.0 - buffer * 2.0)
        invalidation = max(float(range_low), sl_bos)
        target = max(float(range_high) + span * 0.7, px * 1.012)
        risk = px - invalidation
        if risk > 0:
            floor_tp = px + risk * min_rr_f
            if target < floor_tp:
                target = floor_tp
        return invalidation, target

    if scen == "DUMP":
        sl_bos = float(bos_level) * (1.0 + buffer * 2.0)
        invalidation = min(float(range_high), sl_bos)
        target = min(float(range_low) - span * 0.7, px * 0.988)
        risk = invalidation - px
        if risk > 0:
            floor_tp = px - risk * min_rr_f
            if target > floor_tp:
                target = floor_tp
        return invalidation, target

    return float(range_low), float(range_high)
