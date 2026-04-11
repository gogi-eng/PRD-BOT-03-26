#!/usr/bin/env python3
"""Execution engine with post-only limit orders and market fallback."""
from __future__ import annotations

import asyncio
import math
from typing import Dict, Optional


class ExecutionEngine:
    """Executes entries and adjustments with conservative order handling."""

    def __init__(self, client, controls, tg=None, limit_retries: int = 3):
        self.client = client
        self.controls = controls
        self.tg = tg
        self.limit_retries = limit_retries

    async def execute_entry(self, symbol: str, side: str, qty: float, stop_loss: float, take_profit: float, leverage: int, reason: str = "", preferred_price: float = 0.0) -> Dict:
        result = {"success": False, "orderId": "", "error": "", "executed_qty": 0.0, "avg_price": 0.0}
        if self.controls.dry_run:
            print(f"[EXEC] DRY RUN: {side} {symbol} qty={qty} SL={stop_loss} TP={take_profit}")
            result.update({"success": True, "executed_qty": qty, "avg_price": preferred_price})
            if self.tg:
                await self.tg.send_trade_notification(symbol, side, qty, preferred_price, is_open=True, reason=f"[DRY] {reason}")
            return result

        try:
            await self.client.set_leverage(symbol, leverage)
            inst = await self.client.get_instrument_info(symbol)
            if inst:
                qty = self._round_qty(qty, inst["min_qty"], inst["qty_step"])
                if qty < inst["min_qty"]:
                    result["error"] = f"Qty {qty} below min {inst['min_qty']}"
                    return result
                stop_loss = self._round_price(stop_loss, inst["price_step"])
                take_profit = self._round_price(take_profit, inst["price_step"])

            opened = await self._open_with_limit_fallback(symbol, side, qty, stop_loss, take_profit, preferred_price)
            result.update(opened)
            if result.get("success") and self.tg:
                await self.tg.send_trade_notification(symbol, side, result["executed_qty"], result.get("avg_price", 0.0), is_open=True, reason=reason)
        except Exception as exc:
            result["error"] = str(exc)
            print(f"[EXEC] Exception: {exc}")
        return result

    async def execute_add(self, symbol: str, side: str, qty: float, leverage: int, reason: str = "") -> Dict:
        result = {"success": False, "orderId": "", "error": "", "executed_qty": 0.0, "avg_price": 0.0}
        if self.controls.dry_run:
            result.update({"success": True, "executed_qty": qty})
            return result
        try:
            await self.client.set_leverage(symbol, leverage)
            inst = await self.client.get_instrument_info(symbol)
            if inst:
                qty = self._round_qty(qty, inst["min_qty"], inst["qty_step"])
                if qty < inst["min_qty"]:
                    result["error"] = f"Qty {qty} below min {inst['min_qty']}"
                    return result
            result.update(await self._open_with_limit_fallback(symbol, side, qty, None, None, 0.0))
            if result.get("success"):
                print(f"[EXEC] ADDED: {side} {symbol} qty={result['executed_qty']}")
                if self.tg:
                    await self.tg.send_message(f"<b>RL ADD</b>\n<code>{symbol}</code> {side} qty=<code>{result['executed_qty']}</code>\n{reason}")
        except Exception as exc:
            result["error"] = str(exc)
        return result

    async def _open_with_limit_fallback(self, symbol: str, side: str, qty: float, stop_loss: Optional[float], take_profit: Optional[float], preferred_price: float) -> Dict:
        bybit_side = "Buy" if side.upper() in ["BUY", "LONG"] else "Sell"
        last_limit_error = ""
        for attempt in range(self.limit_retries):
            limit_price = preferred_price or await self._derive_passive_price(symbol, side)
            order = await self.client.place_order(
                symbol=symbol,
                side=bybit_side,
                qty=qty,
                order_type="Limit",
                price=limit_price,
                stop_loss=stop_loss,
                take_profit=take_profit,
                time_in_force="PostOnly",
            )
            if not order.get("success"):
                last_limit_error = str(order.get("error") or "")
                continue
            order_id = order.get("orderId", "")
            await asyncio.sleep(2 + attempt)
            status = await self.client.get_order_status(symbol, order_id)
            filled_qty = float(status.get("filled_qty", 0.0))
            avg_price = float(status.get("avg_price", 0.0) or limit_price)
            if filled_qty >= qty * 0.999:
                print(f"[EXEC] LIMIT FILLED: {side} {symbol} qty={filled_qty} price=${avg_price:.4f}")
                return {"success": True, "orderId": order_id, "error": "", "executed_qty": filled_qty, "avg_price": avg_price}
            if filled_qty > 0:
                remaining = max(0.0, qty - filled_qty)
                if remaining > 0:
                    await self.client.cancel_order(symbol, order_id)
                    market = await self.client.place_order(symbol=symbol, side=bybit_side, qty=remaining, order_type="Market", stop_loss=stop_loss, take_profit=take_profit)
                    if market.get("success"):
                        price = await self.client.get_price(symbol)
                        return {"success": True, "orderId": market.get("orderId", order_id), "error": "", "executed_qty": qty, "avg_price": price or avg_price}
                return {"success": True, "orderId": order_id, "error": "", "executed_qty": filled_qty, "avg_price": avg_price}
            await self.client.cancel_order(symbol, order_id)

        market = await self.client.place_order(symbol=symbol, side=bybit_side, qty=qty, order_type="Market", stop_loss=stop_loss, take_profit=take_profit)
        if market.get("success"):
            price = await self.client.get_price(symbol)
            print(f"[EXEC] MARKET FALLBACK: {side} {symbol} qty={qty} price=${price:.4f}")
            return {"success": True, "orderId": market.get("orderId", ""), "error": "", "executed_qty": qty, "avg_price": price}
        m_err = str(market.get("error") or "Order failed")
        if last_limit_error:
            m_err = f"{m_err} | last PostOnly: {last_limit_error}"

        # Bybit v5 often rejects create-order when stopLoss/takeProfit are set on the same request.
        # Open with market only, then attach SL/TP via position/trading-stop.
        if stop_loss is not None or take_profit is not None:
            market_plain = await self.client.place_order(
                symbol=symbol,
                side=bybit_side,
                qty=qty,
                order_type="Market",
                stop_loss=None,
                take_profit=None,
            )
            if market_plain.get("success"):
                price = await self.client.get_price(symbol)
                await asyncio.sleep(0.4)
                warn_parts = []
                if stop_loss is not None:
                    slr = await self.client.update_stop_loss(symbol, stop_loss)
                    if not slr.get("success"):
                        warn_parts.append(f"SL:{slr.get('error', '?')}")
                if take_profit is not None:
                    tpr = await self.client.update_take_profit(symbol, take_profit)
                    if not tpr.get("success"):
                        warn_parts.append(f"TP:{tpr.get('error', '?')}")
                extra = (" WARN " + " ".join(warn_parts)) if warn_parts else ""
                print(
                    f"[EXEC] MARKET plain + trading-stop: {side} {symbol} qty={qty} price=${price:.4f}{extra}"
                )
                return {
                    "success": True,
                    "orderId": market_plain.get("orderId", ""),
                    "error": "",
                    "executed_qty": qty,
                    "avg_price": price,
                }
            m_err = f"{m_err} | plain market: {market_plain.get('error', 'unknown')}"

        return {"success": False, "orderId": "", "error": m_err, "executed_qty": 0.0, "avg_price": 0.0}

    async def _derive_passive_price(self, symbol: str, side: str) -> float:
        orderbook = await self.client.get_orderbook(symbol, limit=5)
        bids = orderbook.get("bids", [])
        asks = orderbook.get("asks", [])
        if side.upper() in ["BUY", "LONG"] and bids:
            return float(bids[0][0])
        if side.upper() in ["SELL", "SHORT"] and asks:
            return float(asks[0][0])
        return await self.client.get_price(symbol)

    async def execute_close(self, symbol: str, side: str, qty: float = None, reason: str = "", position_idx: int = 0) -> Dict:
        """Закрыть позицию."""
        if self.controls.dry_run:
            print(f"[EXEC] DRY RUN CLOSE: {symbol} {side} reason={reason}")
            return {"success": True, "orderId": "", "error": ""}

        try:
            order = await self.client.close_position(symbol, side, qty, position_idx=position_idx)
            if order.get("success"):
                price = await self.client.get_price(symbol)
                print(f"[EXEC] CLOSED: {symbol} {side} price=${price:.4f} reason={reason}")
            return order
        except Exception as e:
            print(f"[EXEC] Close error: {e}")
            return {"success": False, "orderId": "", "error": str(e)}

    async def update_sl(self, symbol: str, new_sl: float, position_idx: int = 0) -> bool:
        """Обновить стоп-лосс."""
        if self.controls.dry_run:
            return True
        inst = await self.client.get_instrument_info(symbol)
        if inst:
            new_sl = self._round_price(new_sl, inst["price_step"])
        result = await self.client.update_stop_loss(symbol, new_sl, position_idx=position_idx)
        return result.get("success", False)

    async def update_tp(self, symbol: str, new_tp: float, position_idx: int = 0) -> bool:
        """Обновить тейк-профит."""
        if self.controls.dry_run:
            return True
        inst = await self.client.get_instrument_info(symbol)
        if inst:
            new_tp = self._round_price(new_tp, inst["price_step"])
        result = await self.client.update_take_profit(symbol, new_tp, position_idx=position_idx)
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
