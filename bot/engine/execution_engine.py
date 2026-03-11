#!/usr/bin/env python3
"""
EXECUTION ENGINE — ЕДИНСТВЕННЫЙ модуль исполнения ордеров.

Отвечает за:
- Установку leverage
- Расчёт qty с учётом instrument info
- Отправку ордеров на биржу
- Уведомления через Telegram
"""
from __future__ import annotations
from typing import Dict, Optional
import math


class ExecutionEngine:
    """Исполнение ордеров на бирже."""

    def __init__(self, client, controls, tg=None):
        self.client = client
        self.controls = controls
        self.tg = tg

    async def execute_entry(
        self,
        symbol: str,
        side: str,
        qty: float,
        stop_loss: float,
        take_profit: float,
        leverage: int,
        reason: str = "",
    ) -> Dict:
        """
        Открыть позицию.

        Returns:
            {"success": bool, "orderId": str, "error": str, "executed_qty": float}
        """
        result = {"success": False, "orderId": "", "error": "", "executed_qty": 0.0}

        if self.controls.dry_run:
            print(f"[EXEC] DRY RUN: {side} {symbol} qty={qty} SL={stop_loss} TP={take_profit}")
            result["success"] = True
            result["executed_qty"] = qty
            if self.tg:
                await self.tg.send_trade_notification(symbol, side, qty, 0, is_open=True, reason=f"[DRY] {reason}")
            return result

        try:
            # Set leverage
            await self.client.set_leverage(symbol, leverage)

            # Get instrument info for qty rounding
            inst = await self.client.get_instrument_info(symbol)
            if inst:
                qty = self._round_qty(qty, inst["min_qty"], inst["qty_step"])
                if qty < inst["min_qty"]:
                    result["error"] = f"Qty {qty} below min {inst['min_qty']}"
                    return result
                stop_loss = self._round_price(stop_loss, inst["price_step"])
                take_profit = self._round_price(take_profit, inst["price_step"])

            # Place order
            bybit_side = "Buy" if side.upper() in ["BUY", "LONG"] else "Sell"
            order = await self.client.place_order(
                symbol=symbol,
                side=bybit_side,
                qty=qty,
                order_type="Market",
                stop_loss=stop_loss,
                take_profit=take_profit,
            )

            if order.get("success"):
                result["success"] = True
                result["orderId"] = order.get("orderId", "")
                result["executed_qty"] = qty

                # Get actual entry price
                price = await self.client.get_price(symbol)
                print(f"[EXEC] OPENED: {side} {symbol} qty={qty} price=${price:.4f} SL=${stop_loss:.4f} TP=${take_profit:.4f}")

                if self.tg:
                    await self.tg.send_trade_notification(symbol, side, qty, price, is_open=True, reason=reason)
            else:
                result["error"] = order.get("error", "Unknown")
                print(f"[EXEC] FAILED: {side} {symbol} - {result['error']}")

        except Exception as e:
            result["error"] = str(e)
            print(f"[EXEC] Exception: {e}")

        return result

    async def execute_close(self, symbol: str, side: str, qty: float = None, reason: str = "") -> Dict:
        """Закрыть позицию."""
        if self.controls.dry_run:
            print(f"[EXEC] DRY RUN CLOSE: {symbol} {side} reason={reason}")
            return {"success": True, "orderId": "", "error": ""}

        try:
            order = await self.client.close_position(symbol, side, qty)
            if order.get("success"):
                price = await self.client.get_price(symbol)
                print(f"[EXEC] CLOSED: {symbol} {side} price=${price:.4f} reason={reason}")
            return order
        except Exception as e:
            print(f"[EXEC] Close error: {e}")
            return {"success": False, "orderId": "", "error": str(e)}

    async def update_sl(self, symbol: str, new_sl: float) -> bool:
        """Обновить стоп-лосс."""
        if self.controls.dry_run:
            return True
        inst = await self.client.get_instrument_info(symbol)
        if inst:
            new_sl = self._round_price(new_sl, inst["price_step"])
        result = await self.client.update_stop_loss(symbol, new_sl)
        return result.get("success", False)

    async def update_tp(self, symbol: str, new_tp: float) -> bool:
        """Обновить тейк-профит."""
        if self.controls.dry_run:
            return True
        inst = await self.client.get_instrument_info(symbol)
        if inst:
            new_tp = self._round_price(new_tp, inst["price_step"])
        result = await self.client.update_take_profit(symbol, new_tp)
        return result.get("success", False)

    @staticmethod
    def _round_qty(qty: float, min_qty: float, step: float) -> float:
        if step <= 0:
            return qty
        decimals = max(0, -int(math.floor(math.log10(step)))) if step < 1 else 0
        rounded = math.floor(qty / step) * step
        return round(max(rounded, min_qty), decimals)

    @staticmethod
    def _round_price(price: float, step: float) -> float:
        if step <= 0:
            return price
        decimals = max(0, -int(math.floor(math.log10(step)))) if step < 1 else 0
        return round(round(price / step) * step, decimals)
