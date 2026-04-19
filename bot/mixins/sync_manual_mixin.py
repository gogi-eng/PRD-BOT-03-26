"""Auto-split from main.TradingBot — see package bot.trading_bot."""
from __future__ import annotations

from bot.trading_bot_imports import *  # noqa: F401,F403

class TradingBotSyncManualMixin:
    async def _sync_exchange_position(self, exchange_position: dict):
        symbol = exchange_position.get("symbol", "")
        if not symbol:
            return
        self._missing_exchange_cycles.pop(symbol, None)
        self._exchange_closed_reentry_until.pop(symbol, None)
        size = float(exchange_position.get("size", 0) or 0)
        if size <= 0:
            return
        entry_price = float(exchange_position.get("avgPrice", 0) or exchange_position.get("entryPrice", 0) or 0)
        mark_price = float(exchange_position.get("markPrice", 0) or entry_price or 0)
        side = "BUY" if str(exchange_position.get("side", "")).lower() == "buy" else "SELL"
        stop_loss = float(exchange_position.get("stopLoss", 0) or 0)
        take_profit = float(exchange_position.get("takeProfit", 0) or 0)
        position_idx = int(exchange_position.get("positionIdx", 0) or 0)

        pos = self.position_manager.get(symbol)
        if pos:
            pos.qty = size
            if entry_price > 0:
                pos.entry_price = entry_price
            pos.position_idx = position_idx
            pos.unrealized_pnl = float(exchange_position.get("unrealisedPnl", 0) or 0)
            if self.preserve_existing_sl_tp:
                if stop_loss > 0:
                    pos.stop_loss = stop_loss
                    if pos.trailing_stop > 0 and pos.is_long and pos.trailing_stop < stop_loss:
                        pos.trailing_stop = stop_loss
                    if pos.trailing_stop > 0 and (not pos.is_long) and pos.trailing_stop > stop_loss > 0:
                        pos.trailing_stop = stop_loss
                if take_profit > 0:
                    pos.take_profit = take_profit
                    pos.total_tp_price = take_profit
                    pos.external_tp_locked = bool(self.manual_preserve_existing_tp and pos.origin == "manual")
                    if not pos.partial_tp_done and not pos.external_tp_locked:
                        pos.partial_tp_price = self._compute_partial_tp_price(pos.entry_price, take_profit, pos.side)
                    elif pos.external_tp_locked:
                        pos.partial_tp_price = 0.0
            return

        klines = await self.client.get_klines(symbol, self.candle_interval, max(60, self.feature_window))
        atr_val = self.atr.get_atr(symbol, klines)
        current_price = entry_price or mark_price
        market = self.market_analyzer.analyze(klines, klines[-max(60, self.feature_window // 2):] if klines else None)
        orderbook = await self.client.get_orderbook(symbol, limit=25)
        trades = await self.client.get_recent_trades(symbol, limit=80)
        orderflow = self.orderflow_analyzer.analyze(orderbook, trades)
        liq_analysis = self._resolve_liquidation_context(symbol, current_price, klines)
        if liq_analysis.target_level <= 0:
            liq_analysis = self._build_directional_liq_fallback(current_price, market, orderflow, atr_val)
        zone_context = self.structure_zone_analyzer.analyze(klines, current_price)
        derived_sl, derived_tp, partial_tp = self._derive_manual_position_levels(side, current_price, stop_loss, take_profit, atr_val, liq_analysis=liq_analysis, klines=klines, zone_context=zone_context)
        stop_loss = stop_loss if stop_loss > 0 and self.preserve_existing_sl_tp else derived_sl
        take_profit = take_profit if take_profit > 0 and self.preserve_existing_sl_tp else derived_tp

        external_tp_locked = bool(take_profit > 0 and self.manual_preserve_existing_tp)
        stop_loss_for_tracking = stop_loss if stop_loss > 0 and self.preserve_existing_sl_tp else 0.0
        adopted = Position(
            symbol=symbol,
            side=side,
            entry_price=entry_price or mark_price,
            qty=size,
            stop_loss=stop_loss_for_tracking,
            take_profit=take_profit,
            unrealized_pnl=float(exchange_position.get("unrealisedPnl", 0) or 0),
            origin="manual",
            partial_tp_price=0.0 if external_tp_locked else self._compute_partial_tp_price(entry_price or mark_price, take_profit, side),
            partial_close_fraction=self.partial_tp_close_fraction,
            total_tp_price=take_profit,
            position_idx=position_idx,
            external_tp_locked=external_tp_locked,
            last_notified_stop_loss=stop_loss_for_tracking,
        )
        self.exit_engine.initialize_position(adopted, atr_val, protective_liq_level=0.0)
        self._apply_manual_trailing_profile(adopted, atr_val)
        self._apply_profit_drawdown_profile(adopted)
        if not external_tp_locked and partial_tp > 0:
            adopted.partial_tp_price = partial_tp
        self.position_manager.add(adopted)

        if not self.controls.dry_run:
            if float(exchange_position.get("takeProfit", 0) or 0) <= 0 and take_profit > 0:
                await self.execution_engine.update_tp(symbol, take_profit, position_idx=position_idx)
        if self.tg and self.manual_notify_on_adopt:
            sl_info = f"${adopted.stop_loss:.4f}" if adopted.stop_loss > 0 else "НЕТ (ждём trailing)"
            await self.tg.send_message(
                f"<b>ПОДХВАЧЕНА ВНЕШНЯЯ ПОЗИЦИЯ</b>\n\n"
                f"Монета: <code>{symbol}</code>\n"
                f"Сторона: <b>{side}</b>\n"
                f"Вход: <code>${adopted.entry_price:.4f}</code>\n"
                f"Объём: <code>{size}</code>\n"
                f"SL: <code>{sl_info}</code>\n"
                f"TP: <code>${adopted.take_profit:.4f}</code>\n"
                f"Режим: <code>manual-safe-trailing (SL только после безубытка)</code>"
            )


    def _derive_manual_position_levels(self, side: str, entry_price: float, stop_loss: float, take_profit: float, atr_val: float, liq_analysis=None, klines: list[dict] | None = None, zone_context=None) -> tuple[float, float, float]:
        atr = atr_val if atr_val > 0 else entry_price * 0.01
        side_upper = side.upper()
        min_stop_distance = entry_price * (self.entry_engine.min_stop_distance_pct / 100)
        min_target_distance = entry_price * (self.entry_engine.min_target_profit_pct / 100)
        highs = [float(item.get("high", 0.0)) for item in (klines or [])[-30:]]
        lows = [float(item.get("low", 0.0)) for item in (klines or [])[-30:]]
        nearest_resistance = min((level for level in highs if level > entry_price), default=0.0)
        nearest_support = max((level for level in lows if level < entry_price), default=0.0)
        zone_support = max((level for level in (zone_context.support_levels if zone_context else []) if level < entry_price), default=0.0)
        zone_resistance = min((level for level in (zone_context.resistance_levels if zone_context else []) if level > entry_price), default=0.0)
        derived_sl = stop_loss
        if derived_sl <= 0:
            if side_upper in ["BUY", "LONG"]:
                cluster_support = liq_analysis.max_liq_cluster_below.level if liq_analysis and liq_analysis.max_liq_cluster_below else 0.0
                base_candidates = [level for level in [nearest_support, zone_support, cluster_support] if 0 < level < entry_price]
                base_support = max(base_candidates) if base_candidates else 0.0
                derived_sl = base_support - atr * self.exit_engine.sl_buffer_atr_mult if base_support > 0 else entry_price - atr * self.exit_engine.hard_sl_atr_mult
            else:
                cluster_resistance = liq_analysis.max_liq_cluster_above.level if liq_analysis and liq_analysis.max_liq_cluster_above else 0.0
                above_levels = [level for level in [nearest_resistance, zone_resistance, cluster_resistance] if level > entry_price]
                base_resistance = min(above_levels) if above_levels else 0.0
                derived_sl = base_resistance + atr * self.exit_engine.sl_buffer_atr_mult if base_resistance > 0 else entry_price + atr * self.exit_engine.hard_sl_atr_mult
        derived_tp = take_profit
        partial_tp = 0.0
        if derived_tp <= 0:
            if side_upper in ["BUY", "LONG"]:
                cluster_target = liq_analysis.max_liq_cluster_above.level if liq_analysis and liq_analysis.max_liq_cluster_above else 0.0
                tp_candidates = [level for level in [cluster_target, zone_resistance, nearest_resistance, max(highs) if highs else 0.0] if level > entry_price]
                if tp_candidates:
                    tp_candidates = sorted(set(tp_candidates))
                    partial_tp = tp_candidates[0] - atr * self.exit_engine.sl_buffer_atr_mult
                    derived_tp = (tp_candidates[1] - atr * self.exit_engine.sl_buffer_atr_mult) if len(tp_candidates) > 1 else partial_tp + max(min_target_distance, atr * 2)
                else:
                    risk = abs(entry_price - derived_sl) if derived_sl > 0 else atr * self.exit_engine.hard_sl_atr_mult
                    derived_tp = entry_price + risk * self.entry_engine.min_rr_ratio
            else:
                cluster_target = liq_analysis.max_liq_cluster_below.level if liq_analysis and liq_analysis.max_liq_cluster_below else 0.0
                tp_candidates = [level for level in [cluster_target, zone_support, nearest_support, min(lows) if lows else 0.0] if 0 < level < entry_price]
                if tp_candidates:
                    tp_candidates = sorted(set(tp_candidates), reverse=True)
                    partial_tp = tp_candidates[0] + atr * self.exit_engine.sl_buffer_atr_mult
                    derived_tp = (tp_candidates[1] + atr * self.exit_engine.sl_buffer_atr_mult) if len(tp_candidates) > 1 else partial_tp - max(min_target_distance, atr * 2)
                else:
                    risk = abs(entry_price - derived_sl) if derived_sl > 0 else atr * self.exit_engine.hard_sl_atr_mult
                    derived_tp = entry_price - risk * self.entry_engine.min_rr_ratio

        if abs(entry_price - derived_sl) < min_stop_distance:
            derived_sl = entry_price - min_stop_distance if side_upper in ["BUY", "LONG"] else entry_price + min_stop_distance
        if abs(derived_tp - entry_price) < min_target_distance:
            derived_tp = entry_price + min_target_distance if side_upper in ["BUY", "LONG"] else entry_price - min_target_distance
        if partial_tp > 0 and abs(partial_tp - entry_price) < min_target_distance * 0.5:
            partial_tp = 0.0
        return derived_sl, derived_tp, partial_tp


    def _apply_manual_trailing_profile(self, pos: Position, atr_val: float):
        atr = atr_val if atr_val > 0 else pos.entry_price * 0.01
        pos.trailing_distance = atr * self.manual_trailing_distance_atr
        min_dist_abs = pos.entry_price * max(0.0, self.manual_trailing_min_distance_pct) / 100.0
        if min_dist_abs > pos.trailing_distance:
            pos.trailing_distance = min_dist_abs
        if pos.is_long:
            pos.trailing_activation_price = pos.entry_price + atr * self.manual_trailing_activation_atr
        else:
            pos.trailing_activation_price = pos.entry_price - atr * self.manual_trailing_activation_atr


    def _apply_profit_drawdown_profile(self, pos: Position):
        pos.profit_guard_armed = False
        pos.profit_peak_price = pos.entry_price
        pos.profit_peak_pct = 0.0
        pos.profit_drawdown_below_trigger_since = 0.0
        act = float(self.profit_drawdown_activation_pct)
        if act <= 0 or pos.entry_price <= 0:
            return
        if pos.is_long:
            min_act = pos.entry_price * (1.0 + act / 100.0)
            if pos.trailing_activation_price <= 0:
                pos.trailing_activation_price = min_act
            else:
                pos.trailing_activation_price = max(pos.trailing_activation_price, min_act)
        else:
            max_act = pos.entry_price * (1.0 - act / 100.0)
            if pos.trailing_activation_price <= 0:
                pos.trailing_activation_price = max_act
            else:
                pos.trailing_activation_price = min(pos.trailing_activation_price, max_act)

    async def _check_profit_drawdown_guard(self, pos: Position, current_price: float, klines: Optional[list] = None) -> tuple[bool, str]:
        if not self.profit_drawdown_guard_enabled or current_price <= 0 or pos.entry_price <= 0:
            return False, ""

        current_profit_pct = self._calc_pnl_pct(pos, current_price)
        if not pos.profit_guard_armed:
            if current_profit_pct + 1e-9 < self.profit_drawdown_activation_pct:
                return False, ""
            pos.profit_guard_armed = True
            pos.profit_peak_price = current_price
            pos.profit_peak_pct = current_profit_pct
            if self.tg:
                await self.tg.send_message(
                    f"<b>PROFIT GUARD АКТИВЕН</b>\n\n"
                    f"Монета: <code>{pos.symbol}</code>\n"
                    f"Вход: <code>${pos.entry_price:.4f}</code>\n"
                    f"Активация: <code>{current_profit_pct:.2f}%</code>\n"
                    f"Правило: закрытие при откате {self.profit_drawdown_retrace_pct:.0f}% от пика прибыли"
                    + (
                        f", подтверждение {self.profit_drawdown_retrace_confirm_sec:.0f}s"
                        if self.profit_drawdown_retrace_confirm_sec > 0
                        else ""
                    )
                )
            return False, ""

        if current_profit_pct > pos.profit_peak_pct:
            pos.profit_peak_pct = current_profit_pct
            pos.profit_peak_price = current_price
            pos.profit_drawdown_below_trigger_since = 0.0
            return False, ""

        trigger_profit_pct = pos.profit_peak_pct * (1 - self.profit_drawdown_retrace_pct / 100)
        reason = (
            f"profit_drawdown_guard: peak={pos.profit_peak_pct:.2f}% current={current_profit_pct:.2f}% "
            f"retrace={self.profit_drawdown_retrace_pct:.0f}%"
        )
        if current_profit_pct > trigger_profit_pct or current_profit_pct <= 0:
            pos.profit_drawdown_below_trigger_since = 0.0
            return False, ""

        if (
            self.profit_drawdown_require_trend_break
            and klines
            and len(klines) >= max(self.profit_drawdown_trend_ema_slow + 2, 55)
        ):
            closes = np.array([float(k.get("close", 0.0) or 0.0) for k in klines], dtype=float)
            ema_fast = self.entry_engine._compute_ema(closes, max(2, self.profit_drawdown_trend_ema_fast))
            ema_slow = self.entry_engine._compute_ema(closes, max(3, self.profit_drawdown_trend_ema_slow))
            ef = float(ema_fast[-1]) if len(ema_fast) else current_price
            es = float(ema_slow[-1]) if len(ema_slow) else current_price
            if np.isfinite(ef) and np.isfinite(es):
                trend_intact = (
                    (pos.is_long and current_price >= ef and ef >= es)
                    or ((not pos.is_long) and current_price <= ef and ef <= es)
                )
                if trend_intact:
                    pos.profit_drawdown_below_trigger_since = 0.0
                    return False, ""

        # Full-symbol pullback analysis: if market shows a healthy pullback/recovery
        # (accumulation after adverse spike), cancel forced drawdown close.
        if (
            self.profit_drawdown_pullback_analysis_enabled
            and klines
            and len(klines) >= max(20, self.profit_drawdown_pullback_lookback_bars)
        ):
            lb = max(20, self.profit_drawdown_pullback_lookback_bars)
            closes = np.array(
                [float(k.get("close", 0.0) or 0.0) for k in klines[-lb:]],
                dtype=float,
            )
            if len(closes) >= 5:
                hi = float(np.max(closes))
                lo = float(np.min(closes))
                if hi > 0 and lo > 0 and hi > lo:
                    range_pct = (hi - lo) / hi * 100.0
                    recovery_ratio = (current_price - lo) / max(hi - lo, 1e-9)
                    adverse_pct = (
                        (hi - current_price) / hi * 100.0
                        if pos.is_long
                        else (current_price - lo) / lo * 100.0
                    )
                    accumulation_cancel = (
                        adverse_pct >= self.profit_drawdown_pullback_min_adverse_pct
                        and recovery_ratio >= self.profit_drawdown_pullback_cancel_recovery_ratio
                        and range_pct <= self.profit_drawdown_pullback_max_range_pct
                    )
                    if accumulation_cancel:
                        pos.profit_drawdown_below_trigger_since = 0.0
                        logger.info(
                            f"[PROFIT_GUARD] {pos.symbol} close cancelled by pullback analysis: "
                            f"adverse={adverse_pct:.2f}% recovery={recovery_ratio:.2f} range={range_pct:.2f}%"
                        )
                        return False, ""

        if self.profit_drawdown_retrace_confirm_sec <= 1e-9:
            return True, reason

        now = time.time()
        if pos.profit_drawdown_below_trigger_since <= 0:
            pos.profit_drawdown_below_trigger_since = now
            return False, ""
        if now - pos.profit_drawdown_below_trigger_since >= self.profit_drawdown_retrace_confirm_sec:
            return True, reason
        return False, ""


    async def _notify_manual_sl_move(self, pos: Position, source: str):
        if not self.tg or not self.manual_notify_on_sl_move:
            return
        if abs(pos.stop_loss - pos.last_notified_stop_loss) < 1e-9:
            return
        pos.last_notified_stop_loss = pos.stop_loss
        await self.tg.send_message(
            f"<b>РУЧНАЯ ПОЗИЦИЯ: ПЕРЕНОС SL</b>\n\n"
            f"Монета: <code>{pos.symbol}</code>\n"
            f"Сторона: <b>{pos.side}</b>\n"
            f"Новый SL: <code>${pos.stop_loss:.4f}</code>\n"
            f"Причина: <code>{source}</code>"
        )


    async def _maybe_execute_partial_tp(self, pos: Position, current_price: float) -> bool:
        if not self.partial_tp_enabled or pos.partial_tp_done or pos.partial_tp_price <= 0 or pos.qty <= 0:
            return False
        if pos.origin == "manual" and pos.external_tp_locked:
            return False
        hit = current_price >= pos.partial_tp_price if pos.is_long else current_price <= pos.partial_tp_price
        if not hit:
            return False
        close_qty = pos.qty * max(0.1, min(pos.partial_close_fraction, 0.9))
        if close_qty * current_price < self.min_position_usdt:
            pos.partial_tp_done = True
            return False
        close_result = await self.execution_engine.execute_close(
            pos.symbol,
            pos.side,
            qty=close_qty,
            reason=f"partial_tp@{pos.partial_tp_price:.4f}",
            position_idx=pos.position_idx,
        )
        if not close_result.get("success"):
            return False
        await self._finalize_partial_close(pos.symbol, pos, current_price, close_qty, f"partial_tp_{int(pos.partial_close_fraction*100)}pct")
        remaining = self.position_manager.get(pos.symbol)
        if remaining:
            remaining.partial_tp_done = True
            remaining.last_rl_action = "partial_tp"
            if self.partial_tp_move_stop_to_entry:
                if remaining.is_long:
                    remaining.stop_loss = max(remaining.stop_loss, remaining.entry_price)
                else:
                    remaining.stop_loss = min(remaining.stop_loss, remaining.entry_price) if remaining.stop_loss > 0 else remaining.entry_price
                updated = await self.execution_engine.update_sl(remaining.symbol, remaining.stop_loss, position_idx=remaining.position_idx)
                if updated and remaining.origin == "manual":
                    await self._notify_manual_sl_move(remaining, "partial_tp_breakeven")
        if self.tg and self.manual_notify_on_partial_tp:
            await self.tg.send_message(
                f"<b>ЧАСТИЧНЫЙ TP</b>\n\n"
                f"Монета: <code>{pos.symbol}</code>\n"
                f"Закрыто: <code>{close_qty:.6f}</code>\n"
                f"Цена: <code>${current_price:.4f}</code>\n"
                f"Уровень: <code>${pos.partial_tp_price:.4f}</code>"
            )
        return True
