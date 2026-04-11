"""Auto-split from main.TradingBot — see package bot.trading_bot."""
from __future__ import annotations

from bot.trading_bot_imports import *  # noqa: F401,F403

class TradingBotEntryExecMixin:
    async def _execute_entry(self, symbol: str, signal: EntrySignal, capital_weight: float):
        # Pre-execution momentum guard: check last 3 candles for strong opposite momentum
        recent_klines = await self.client.get_klines(symbol, self.candle_interval, 5)
        if recent_klines and len(recent_klines) >= 3:
            last_3 = recent_klines[-3:]
            atr_check = self.atr.get_atr(symbol, recent_klines)
            if atr_check > 0:
                total_body = 0.0
                for k in last_3:
                    o, c = float(k.get("open", 0)), float(k.get("close", 0))
                    total_body += (c - o)  # positive = bullish, negative = bearish
                # If entering BUY but last 3 candles are strongly bearish (> 1.5 ATR down)
                if signal.side.upper() == "BUY" and total_body < -1.5 * atr_check:
                    logger.warning(
                        f"[MOMENTUM GUARD] {symbol} BUY blocked: last 3 candles bearish "
                        f"(body={total_body:.4f} vs ATR={atr_check:.4f})"
                    )
                    return
                # If entering SELL but last 3 candles are strongly bullish
                if signal.side.upper() == "SELL" and total_body > 1.5 * atr_check:
                    logger.warning(
                        f"[MOMENTUM GUARD] {symbol} SELL blocked: last 3 candles bullish "
                        f"(body={total_body:.4f} vs ATR={atr_check:.4f})"
                    )
                    return

        balance = self.controls.get_balance()
        leverage = self.controls.leverage
        qty = self.risk_guard.calculate_position_size(
            balance=balance,
            risk_pct=self.controls.risk_per_trade_pct,
            entry=signal.entry_price,
            stop_loss=signal.stop_loss,
            leverage=leverage,
            capital_weight=capital_weight,
            margin_cap_pct=self.controls.margin_total_pct,
            size_mode=self.position_size_mode,
        )
        notional = qty * signal.entry_price
        margin_used = notional / max(1.0, float(leverage))
        margin_cap = balance * (float(self.controls.margin_total_pct) / 100.0) * max(0.2, float(capital_weight))
        logger.info(
            f"[ENTRY SIZE] {symbol} mode={self.position_size_mode} "
            f"qty={qty:.6f} notional=${notional:.2f} margin_used=${margin_used:.2f} "
            f"margin_cap=${margin_cap:.2f} bal=${balance:.2f} lev={leverage}x weight={capital_weight:.3f}"
        )
        if qty * signal.entry_price < self.min_position_usdt:
            logger.info(f"Position too small for {symbol}: ${qty * signal.entry_price:.2f}")
            return

        result = await self.execution_engine.execute_entry(
            symbol=symbol,
            side=signal.side,
            qty=qty,
            stop_loss=signal.stop_loss,
            take_profit=signal.take_profit,
            leverage=leverage,
            reason=" | ".join(signal.reasons[:3]),
        )
        if not result.get("success"):
            err = str(result.get("error") or result.get("retMsg") or "unknown")
            logger.warning(f"[ENTRY FAILED] {symbol} {signal.side}: {err}")
            return

        executed_price = result.get("avg_price", 0.0) or signal.entry_price
        pos = Position(
            symbol=symbol,
            side=signal.side,
            entry_price=executed_price,
            qty=result.get("executed_qty", qty),
            stop_loss=signal.stop_loss,
            take_profit=signal.take_profit,
            capital_weight=capital_weight,
            heatmap_target=signal.metadata.get("target_level", 0.0),
            protective_liq_level=signal.metadata.get("protective_liq_level", 0.0),
            model_confidence=signal.confidence,
            origin="bot",
            partial_tp_price=signal.metadata.get("tp1_level", 0.0) or self._compute_partial_tp_price(executed_price, signal.take_profit, signal.side),
            partial_close_fraction=self.partial_tp_close_fraction,
            total_tp_price=signal.take_profit,
        )
        klines = await self.client.get_klines(symbol, self.candle_interval, 50)
        atr_val = self.atr.get_atr(symbol, klines)
        self.exit_engine.initialize_position(pos, atr_val, protective_liq_level=pos.protective_liq_level)
        self._apply_profit_drawdown_profile(pos)
        self.position_manager.add(pos)
        self._register_signal_timestamp(symbol, signal.side)
        logger.info(f"ENTERED {symbol}: {signal.side} [{signal.grade}] qty={pos.qty:.6f} entry=${executed_price:.4f} weight={capital_weight:.2f}")


    async def _maybe_pyramid_add(self, pos: Position, current_price: float, atr_val: float, structure):
        """Pyramid strategy: add to winning positions.

        Rules:
        - add1: R >= add1_min_r (0.5R) — pullback entry
        - add2: R >= add2_min_r (1.2R) — continuation entry
        - Total risk across all adds <= max_total_risk_pct
        """
        risk = abs(pos.entry_price - pos.stop_loss)
        if risk <= 0:
            return

        if pos.is_long:
            profit = current_price - pos.entry_price
        else:
            profit = pos.entry_price - current_price

        r_multiple = profit / risk

        # Determine which add level we're at
        if pos.add_count == 0:
            min_r = self.pyramid_add1_min_r
        elif pos.add_count == 1:
            min_r = self.pyramid_add2_min_r
        else:
            return

        if r_multiple < min_r:
            return

        # Check total risk budget
        balance = self.controls.get_balance()
        current_risk_pct = (risk * pos.qty / balance * 100) if balance > 0 else 100
        remaining_risk_pct = self.pyramid_max_total_risk_pct - current_risk_pct
        if remaining_risk_pct <= 0.1:
            return

        # Pyramid condition: pullback or continuation
        is_pullback = False
        is_continuation = False

        if structure and structure.last_bos:
            if pos.is_long and structure.last_bos.direction == "up":
                is_continuation = True
            elif not pos.is_long and structure.last_bos.direction == "down":
                is_continuation = True

        if pos.is_long and structure and structure.swing_lows:
            last_sl = structure.swing_lows[-1].price
            if current_price <= last_sl * 1.005 and current_price > last_sl:
                is_pullback = True
        elif not pos.is_long and structure and structure.swing_highs:
            last_sh = structure.swing_highs[-1].price
            if current_price >= last_sh * 0.995 and current_price < last_sh:
                is_pullback = True

        if not is_pullback and not is_continuation:
            return

        allowed, _ = self.risk_guard.can_trade(pos.symbol)
        if not allowed:
            return

        add_risk_pct = min(remaining_risk_pct, self.controls.risk_per_trade_pct * 0.5)
        add_qty = self.risk_guard.calculate_position_size(
            balance=balance,
            risk_pct=add_risk_pct,
            entry=current_price,
            stop_loss=pos.stop_loss,
            leverage=self.controls.leverage,
            capital_weight=0.5,
            margin_cap_pct=self.controls.margin_total_pct,
        )
        if add_qty * current_price < self.min_position_usdt:
            return

        add_type = "pullback" if is_pullback else "continuation"
        reason = f"pyramid_{add_type}_add{pos.add_count + 1}"
        add_result = await self.execution_engine.execute_add(
            pos.symbol, pos.side, add_qty, self.controls.leverage, reason=reason
        )
        if add_result.get("success"):
            executed_qty = add_result.get("executed_qty", 0.0)
            avg_price = add_result.get("avg_price", current_price) or current_price
            self.position_manager.increase(pos.symbol, executed_qty, avg_price)
            logger.info(f"PYRAMID {pos.symbol}: {reason} qty={executed_qty:.6f} price=${avg_price:.4f} R={r_multiple:.1f}")
            if self.tg:
                await self.tg.send_message(
                    f"<b>PYRAMID ADD {pos.add_count}</b>\n"
                    f"Монета: <code>{pos.symbol}</code>\n"
                    f"Тип: {add_type} (R={r_multiple:.1f})\n"
                    f"Добавлено: <code>{executed_qty:.6f}</code> @ ${avg_price:.4f}"
                )



    def _compute_partial_tp_price(self, entry: float, total_tp: float, side: str) -> float:
        if entry <= 0 or total_tp <= 0:
            return 0.0
        progress = max(0.05, min(self.partial_tp_trigger_progress, 0.95))
        if side.upper() in ["BUY", "LONG"]:
            return entry + (total_tp - entry) * progress if total_tp > entry else 0.0
        return entry - (entry - total_tp) * progress if total_tp < entry else 0.0


    def _same_side_cooldown_remaining(self, symbol: str, side: str) -> int:
        if self.signal_cooldown_sec <= 0:
            return 0
        key = (symbol, side.upper())
        last_ts = self._last_signal_ts.get(key)
        if not last_ts:
            return 0
        elapsed = time.time() - last_ts
        remaining = int(self.signal_cooldown_sec - elapsed)
        return remaining if remaining > 0 else 0


    def _register_signal_timestamp(self, symbol: str, side: str):
        if self.signal_cooldown_sec <= 0:
            return
        self._last_signal_ts[(symbol, side.upper())] = time.time()
