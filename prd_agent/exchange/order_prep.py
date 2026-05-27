"""Подготовка qty/цен перед отправкой ордера на Bybit."""
from __future__ import annotations

import math
from typing import Any, Dict, Optional, Tuple


def round_qty(qty: float, min_qty: float, step: float) -> float:
    if qty <= 0 or step <= 0:
        return 0.0
    decimals = max(0, int(round(-math.log10(step)))) if step < 1 else 0
    rounded = math.floor(qty / step) * step
    return round(max(rounded, min_qty), decimals)


def round_price(price: float, step: float) -> float:
    if step <= 0:
        return price
    decimals = max(0, int(round(-math.log10(step)))) if step < 1 else 0
    return round(math.floor(price / step) * step, decimals)


async def prepare_order(
    client: Any,
    *,
    symbol: str,
    leverage: int,
    qty: float,
    stop_loss: Optional[float],
    take_profit: Optional[float],
    limit_price: Optional[float] = None,
) -> Tuple[float, Optional[float], Optional[float], str]:
    """
    Устанавливает плечо, округляет qty/SL/TP по шагам инструмента.
    Возвращает (qty, sl, tp, error). error пустой при успехе.
    """
    if qty <= 0:
        return 0.0, stop_loss, take_profit, "qty=0"

    # Плечо выставляется в orchestrator через apply_trade_leverage (с проверкой биржи).

    inst: Optional[Dict] = None
    if hasattr(client, "get_instrument_info"):
        inst = await client.get_instrument_info(symbol)

    if inst:
        min_qty = float(inst.get("min_qty", 0))
        step = float(inst.get("qty_step", 0))
        price_step = float(inst.get("price_step", 0))
        qty = round_qty(qty, min_qty, step)
        if qty < min_qty:
            return 0.0, stop_loss, take_profit, f"Qty {qty} ниже минимума {min_qty}"
        if stop_loss is not None and price_step > 0:
            stop_loss = round_price(stop_loss, price_step)
        if take_profit is not None and price_step > 0:
            take_profit = round_price(take_profit, price_step)
        if limit_price is not None and price_step > 0:
            limit_price = round_price(limit_price, price_step)

    return qty, stop_loss, take_profit, ""


async def prepare_market_order(
    client: Any,
    *,
    symbol: str,
    leverage: int,
    qty: float,
    stop_loss: Optional[float],
    take_profit: Optional[float],
) -> Tuple[float, Optional[float], Optional[float], str]:
    return await prepare_order(
        client,
        symbol=symbol,
        leverage=leverage,
        qty=qty,
        stop_loss=stop_loss,
        take_profit=take_profit,
    )
