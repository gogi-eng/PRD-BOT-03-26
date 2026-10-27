"""Подготовка qty/цен перед отправкой ордера на Bybit."""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Dict, Iterator, Optional, Tuple


@dataclass
class PreparedOrder:
    qty: float = 0.0
    stop_loss: Optional[float] = None
    take_profit: Optional[float] = None
    error: str = ""
    ok: Optional[bool] = None
    reason: str = ""

    def __post_init__(self) -> None:
        if self.reason and not self.error:
            self.error = self.reason
        elif self.error and not self.reason:
            self.reason = self.error
        if self.ok is None:
            self.ok = not bool(self.error)

    def __iter__(self) -> Iterator[Any]:
        yield self.qty
        yield self.stop_loss
        yield self.take_profit
        yield self.error

    def __getitem__(self, idx: int) -> Any:
        return (self.qty, self.stop_loss, self.take_profit, self.error)[idx]

    def __len__(self) -> int:
        return 4


def round_qty(qty: float, min_qty: float, step: float) -> float:
    if qty <= 0 or step <= 0:
        return 0.0
    decimals = max(0, int(round(-math.log10(step)))) if step < 1 else 0
    rounded = math.floor((qty + 1e-12) / step) * step
    return round(max(rounded, min_qty), decimals)


def round_price(price: float, step: float, *, side: str = "") -> float:
    if step <= 0 or price <= 0:
        return price
    decimals = max(0, int(round(-math.log10(step)))) if step < 1 else 0
    side_u = str(side or "").upper()
    if side_u in ("SELL", "SHORT"):
        rounded = math.ceil((price - 1e-12) / step) * step
    else:
        rounded = math.floor((price + 1e-12) / step) * step
    return round(rounded, decimals)


async def prepare_order(
    client: Any,
    *,
    symbol: str,
    leverage: int,
    qty: float,
    stop_loss: Optional[float],
    take_profit: Optional[float],
    limit_price: Optional[float] = None,
    entry_price: Optional[float] = None,
    min_notional_usdt: float = 5.0,
) -> PreparedOrder:
    """
    Округляет qty/SL/TP по шагам инструмента и проверяет минимальный номинал Bybit (5 USDT).
    Возвращает PreparedOrder (распаковывается как (qty, sl, tp, error) и имеет .ok / .reason).
    """
    if qty <= 0:
        return PreparedOrder(
            qty=0.0,
            stop_loss=stop_loss,
            take_profit=take_profit,
            ok=False,
            reason="qty=0",
        )

    inst: Optional[Dict] = None
    if hasattr(client, "get_instrument_info"):
        inst = await client.get_instrument_info(symbol)

    min_notional = float(min_notional_usdt or 5.0)
    if inst:
        min_qty = float(inst.get("min_qty", 0))
        step = float(inst.get("qty_step", 0))
        price_step = float(inst.get("price_step", 0))
        min_notional = float(inst.get("min_notional", min_notional) or min_notional)
        qty_rounded = round_qty(qty, 0.0, step) if step > 0 else qty
        if qty_rounded < min_qty:
            return PreparedOrder(
                qty=0.0,
                stop_loss=stop_loss,
                take_profit=take_profit,
                ok=False,
                reason=f"Qty {qty_rounded} ниже минимума {min_qty}",
            )
        qty = round_qty(qty, min_qty, step)
        if stop_loss is not None and price_step > 0:
            stop_loss = round_price(stop_loss, price_step)
        if take_profit is not None and price_step > 0:
            take_profit = round_price(take_profit, price_step)
        if limit_price is not None and price_step > 0:
            limit_price = round_price(limit_price, price_step)

    ref_price = 0.0
    if limit_price is not None and float(limit_price or 0) > 0:
        ref_price = float(limit_price)
    elif entry_price is not None and float(entry_price or 0) > 0:
        ref_price = float(entry_price)
    elif client is not None and hasattr(client, "get_price"):
        try:
            ref_price = float(await client.get_price(symbol) or 0.0)
        except Exception:
            ref_price = 0.0
    if ref_price <= 0:
        if stop_loss and take_profit and float(stop_loss) > 0 and float(take_profit) > 0:
            ref_price = (float(stop_loss) + float(take_profit)) / 2.0
        elif stop_loss and float(stop_loss) > 0:
            ref_price = float(stop_loss)

    if ref_price > 0 and min_notional > 0:
        notional = qty * ref_price
        if notional + 1e-9 < min_notional:
            return PreparedOrder(
                qty=0.0,
                stop_loss=stop_loss,
                take_profit=take_profit,
                ok=False,
                reason=(
                    f"Номинал ордера {notional:.2f} USDT ниже минимума Bybit "
                    f"{min_notional:.2f} USDT (retCode=110094)"
                ),
            )

    return PreparedOrder(
        qty=qty,
        stop_loss=stop_loss,
        take_profit=take_profit,
        ok=True,
        reason="",
    )


async def prepare_market_order(
    client: Any,
    *,
    symbol: str,
    leverage: int,
    qty: float,
    stop_loss: Optional[float],
    take_profit: Optional[float],
    entry_price: Optional[float] = None,
) -> PreparedOrder:
    return await prepare_order(
        client,
        symbol=symbol,
        leverage=leverage,
        qty=qty,
        stop_loss=stop_loss,
        take_profit=take_profit,
        entry_price=entry_price,
    )
