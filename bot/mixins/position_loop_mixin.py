"""Auto-split from main.TradingBot — see package bot.trading_bot."""
from __future__ import annotations

from bot.trading_bot_imports import *  # noqa: F401,F403

class TradingBotPositionLoopMixin:
    async def _manage_positions(self, exchange_positions: list | None = None) -> float:
        exchange_positions = exchange_positions if exchange_positions is not None else await self.client.get_positions()

        sync_pause_remaining = self._exchange_closed_sync_pause_remaining()
        if sync_pause_remaining > 0:
            now = time.time()
            if now - self._last_exchange_sync_pause_log_ts >= 30:
                logger.info(
                    f"[POSITION_SYNC] exchange_closed reconciliation paused due rate-limit: {sync_pause_remaining}s"
                )
                self._last_exchange_sync_pause_log_ts = now
            self._missing_exchange_cycles.clear()

        if exchange_positions:
            exchange_symbols = {item["symbol"] for item in exchange_positions}
            for symbol in self.position_manager.symbols():
                if sync_pause_remaining > 0:
                    continue
                if symbol not in exchange_symbols and not self.controls.dry_run:
                    if not self._should_finalize_exchange_closed(symbol):
                        continue
                    pos = self.position_manager.get(symbol)
                    if pos:
                        closed = await self.client.get_closed_pnl(symbol, limit=3)
                        # Only count closedPnl records from the last 5 minutes
                        recent_closed = self._filter_recent_closed_pnl(closed, max_age_sec=300)
                        seen_cycles = int(self._missing_exchange_cycles.get(symbol, 0))

                        # Manual positions: require closedPnl evidence, but widen window progressively
                        if pos.origin == "manual" and len(recent_closed) == 0:
                            # Try wider time window for manual positions (up to 2 hours)
                            wider_closed = self._filter_recent_closed_pnl(closed, max_age_sec=7200)
                            if len(wider_closed) > 0:
                                # Found closedPnl in wider window — use it
                                recent_closed = wider_closed
                                logger.info(
                                    f"[POSITION_SYNC] {symbol} MANUAL — found closedPnl in wider window "
                                    f"(missing={seen_cycles}, records={len(wider_closed)})"
                                )
                            elif seen_cycles < max(1, int(self.exchange_closed_force_cycles)):
                                # Still waiting — no evidence yet
                                if seen_cycles % 10 == 0:
                                    logger.info(
                                        f"[POSITION_SYNC] {symbol} MANUAL position — waiting for closedPnl "
                                        f"(missing={seen_cycles}/{self.exchange_closed_force_cycles})"
                                    )
                                continue
                            else:
                                # Force-finalize after exchange_closed_force_cycles even for manual
                                logger.warning(
                                    f"[POSITION_SYNC] {symbol} MANUAL position — force-finalizing after "
                                    f"{seen_cycles} missing cycles with no closedPnl"
                                )
                                # Use any closedPnl if available, or estimate
                                if closed:
                                    recent_closed = closed[:1]

                        if not self._can_finalize_exchange_closed(seen_cycles, len(recent_closed)):
                            logger.info(
                                f"[POSITION_SYNC] {symbol} waiting close evidence "
                                f"(missing={seen_cycles}, recent_closed={len(recent_closed)}, total_closed={len(closed or [])})"
                            )
                            continue

                        pos = self.position_manager.remove(symbol)
                        if pos:
                            current_price = await self.client.get_price(symbol)
                            pnl = 0.0
                            if recent_closed:
                                pnl = float(recent_closed[0].get("closedPnl", 0) or 0)
                            elif closed:
                                pnl = float(closed[0].get("closedPnl", 0) or 0)
                            exchange_close_records = recent_closed or closed
                            exchange_close_reason = self._classify_exchange_closed_reason(exchange_close_records)
                            self._set_exchange_close_meta(symbol, exchange_close_records)
                            await self._finalize_full_close(
                                symbol,
                                pos,
                                current_price,
                                pnl,
                                exchange_close_reason,
                                already_removed=True,
                            )
                else:
                    self._missing_exchange_cycles.pop(symbol, None)

        if self.adopt_all_positions:
            for exchange_position in exchange_positions:
                await self._sync_exchange_position(exchange_position)

        total_unrealized = 0.0
        for exchange_position in exchange_positions:
            symbol = exchange_position["symbol"]
            unrealized = float(exchange_position.get("unrealisedPnl", 0) or 0)
            total_unrealized += unrealized
            pos = self.position_manager.get(symbol)
            if pos:
                pos.unrealized_pnl = unrealized
        self.controls.set_unrealized_pnl(total_unrealized)

        if self.position_manager.count() == 0:
            self._reset_basket_profit_state()
            return total_unrealized

        for symbol in list(self.position_manager.symbols()):
            pos = self.position_manager.get(symbol)
            if not pos:
                continue
            current_price = await self.client.get_price(symbol)
            if current_price <= 0:
                continue

            klines = await self.client.get_klines(symbol, self.candle_interval, self.klines_limit)
            htf_klines = await self.client.get_klines(symbol, self.htf_interval, max(80, self.feature_window))
            if len(klines) < 40:
                continue
            # Count bars by CLOSED candle timestamps, not by loop cycles.
            # This prevents early_exit from triggering too early when loop runs faster than candle interval.
            interval_sec = self._interval_to_seconds(self.candle_interval)
            latest_closed_ts = self._last_closed_kline_ts(klines)
            if latest_closed_ts > 0:
                prev_ts = int(getattr(pos, "last_counted_kline_ts", 0) or 0)
                if latest_closed_ts > prev_ts:
                    if prev_ts > 0:
                        delta_ms = max(0, latest_closed_ts - prev_ts)
                        bars_delta = max(1, int(round(delta_ms / max(1, interval_sec * 1000))))
                        pos.bars_since_entry += bars_delta
                    else:
                        # First observation after startup/adoption: seed without increment.
                        pos.bars_since_entry = max(0, int(getattr(pos, "bars_since_entry", 0) or 0))
                    pos.last_counted_kline_ts = latest_closed_ts
            atr_val = self.atr.get_atr(symbol, klines)
            # HTF ATR floor for trailing distance (same as entry)
            htf_atr_val = self.atr.get_atr(f"{symbol}_htf", htf_klines)
            if htf_atr_val > 0:
                atr_val = max(atr_val, htf_atr_val)
            market = self.market_analyzer.analyze(klines, htf_klines)
            regime = self.regime_ai.classify(market)
            orderbook = await self.client.get_orderbook(symbol, limit=25)
            trades = await self.client.get_recent_trades(symbol, limit=80)
            orderflow = self.orderflow_analyzer.analyze(orderbook, trades)
            liq = self._resolve_liquidation_context(symbol, current_price, klines)
            if liq.target_level <= 0:
                liq = self._build_directional_liq_fallback(current_price, market, orderflow, atr_val)
            self.controls.set_heatmap(symbol, liq)
            features = self.feature_engineer.build(klines, orderflow, liq, atr_val)
            transformer = self.transformer_model.predict(features, regime, orderflow, liq)

            pnl_pct = self._calc_pnl_pct(pos, current_price)
            if self.controls.rl_enabled and (pos.origin == "bot" or (pos.origin == "manual" and self.manual_rl_enabled)):
                # Calculate drawdown from peak
                if pos.best_price > 0 and pos.entry_price > 0:
                    if pos.is_long:
                        peak_profit = pos.best_price - pos.entry_price
                        curr_profit = current_price - pos.entry_price
                    else:
                        peak_profit = pos.entry_price - pos.best_price
                        curr_profit = pos.entry_price - current_price
                    dd_from_peak = ((peak_profit - curr_profit) / max(peak_profit, pos.entry_price * 0.001)) * 100 if peak_profit > 0 else 0.0
                else:
                    dd_from_peak = 0.0

                state = {
                    "trend_bias": market.htf_trend.value if market.htf_trend.value != 0 else market.trend.value,
                    "volatility": market.atr_pct / 100,
                    "pnl_pct": pnl_pct,
                    "liq_signal": liq.signal,
                    "orderflow_edge": orderflow.imbalance_score,
                    "transformer_edge": transformer.prob_up - transformer.prob_down,
                    "regime": regime.regime.value if hasattr(regime, 'regime') else "chop",
                    "bars_held": pos.bars_since_entry,
                    "drawdown_from_peak_pct": dd_from_peak,
                }
                decision = self.rl_agent.decide(pos, state)
                pos.last_rl_action = decision.action.value
                if decision.action == RLAction.CLOSE:
                    close_result = await self.execution_engine.execute_close(symbol, pos.side, reason=decision.reason, position_idx=pos.position_idx)
                    if close_result.get("success"):
                        pnl = self._calc_pnl(pos, current_price, pos.qty)
                        await self._finalize_full_close(symbol, pos, current_price, pnl, f"rl_close:{decision.reason}")
                        continue
                elif decision.action == RLAction.REDUCE and pos.qty > 0:
                    reduce_qty = pos.qty * decision.fraction
                    close_result = await self.execution_engine.execute_close(symbol, pos.side, qty=reduce_qty, reason=decision.reason, position_idx=pos.position_idx)
                    if close_result.get("success"):
                        await self._finalize_partial_close(symbol, pos, current_price, reduce_qty, f"rl_reduce:{decision.reason}")
                elif decision.action == RLAction.TIGHTEN and pos.trailing_active:
                    # Move trailing stop closer by fraction of current distance
                    if pos.is_long and pos.trailing_stop > 0:
                        gap = current_price - pos.trailing_stop
                        new_stop = pos.trailing_stop + gap * decision.fraction
                        if new_stop > pos.trailing_stop:
                            pos.trailing_stop = new_stop
                            await self.execution_engine.update_sl(symbol, new_stop, position_idx=pos.position_idx)
                            logger.info(f"[RL TIGHTEN] {symbol} LONG trail_stop → {new_stop:.4f}")
                    elif not pos.is_long and pos.trailing_stop > 0:
                        gap = pos.trailing_stop - current_price
                        new_stop = pos.trailing_stop - gap * decision.fraction
                        if new_stop < pos.trailing_stop:
                            pos.trailing_stop = new_stop
                            await self.execution_engine.update_sl(symbol, new_stop, position_idx=pos.position_idx)
                            logger.info(f"[RL TIGHTEN] {symbol} SHORT trail_stop → {new_stop:.4f}")
                elif decision.action == RLAction.ADD and pos.add_count < self.max_rl_adds:
                    allowed, _ = self.risk_guard.can_trade(symbol)
                    if allowed:
                        add_qty = self.risk_guard.calculate_position_size(
                            balance=self.controls.get_balance(),
                            risk_pct=self.controls.risk_per_trade_pct,
                            entry=current_price,
                            stop_loss=pos.stop_loss,
                            leverage=self.controls.leverage,
                            capital_weight=max(pos.capital_weight * decision.fraction, 0.25),
                            margin_cap_pct=self.controls.margin_total_pct,
                        )
                        if add_qty * current_price >= self.min_position_usdt:
                            add_result = await self.execution_engine.execute_add(symbol, pos.side, add_qty, self.controls.leverage, reason=decision.reason)
                            if add_result.get("success"):
                                self.position_manager.increase(symbol, add_result.get("executed_qty", 0.0), add_result.get("avg_price", current_price) or current_price)

            partial_closed = await self._maybe_execute_partial_tp(pos, current_price)
            if partial_closed:
                pos = self.position_manager.get(symbol)
                if not pos:
                    continue

            guard_exit, guard_reason = await self._check_profit_drawdown_guard(pos, current_price)
            if guard_exit:
                close_result = await self.execution_engine.execute_close(symbol, pos.side, reason=guard_reason, position_idx=pos.position_idx)
                if close_result.get("success"):
                    pnl = self._calc_pnl(pos, current_price, pos.qty)
                    await self._finalize_full_close(symbol, pos, current_price, pnl, "profit_drawdown_guard")
                    continue

            # Get swing levels for R-based trailing
            structure = self.market_structure_engine.analyze(klines, atr_val)
            last_swing_low = structure.swing_lows[-1].price if structure.swing_lows else 0.0
            last_swing_high = structure.swing_highs[-1].price if structure.swing_highs else 0.0
            self.exit_engine.update_trailing(
                pos, current_price, last_swing_low, last_swing_high, atr_val
            )
            # Keep exchange stop-loss in sync with trailing stop for ALL positions.
            # Without this, local trailing can move while exchange SL remains stale.
            if pos.trailing_active and pos.trailing_stop > 0:
                updated = await self.execution_engine.update_sl(
                    symbol, pos.trailing_stop, position_idx=pos.position_idx
                )
                if updated and pos.stop_loss != pos.trailing_stop:
                    logger.info(
                        f"[TRAIL SL SYNC] {symbol} SL {pos.stop_loss:.4f} -> {pos.trailing_stop:.4f}"
                    )
                    pos.stop_loss = pos.trailing_stop
                    if pos.origin == "manual":
                        await self._notify_manual_sl_move(pos, "trailing")

            # --- Trailing stop diagnostic logging ---
            risk = abs(pos.entry_price - pos.stop_loss) if pos.stop_loss > 0 else pos.entry_price * 0.01
            if risk < pos.entry_price * 0.0001:
                risk = pos.entry_price * 0.01  # Prevent division by near-zero
            pnl_from_entry = (current_price - pos.entry_price) if pos.is_long else (pos.entry_price - current_price)
            r_mult = pnl_from_entry / risk if risk > 0 else 0
            logger.info(
                f"[TRAIL] {symbol} price={current_price:.4f} entry={pos.entry_price:.4f} "
                f"best={pos.best_price:.4f} R={r_mult:.2f} "
                f"trail_active={pos.trailing_active} trail_stop={pos.trailing_stop:.4f} "
                f"activation={pos.trailing_activation_price:.4f} SL={pos.stop_loss:.4f} "
                f"bars={pos.bars_since_entry}"
            )

            # --- Pyramid: add to winning positions ---
            if self.pyramid_enabled and pos.origin == "bot" and pos.add_count < self.pyramid_max_adds:
                await self._maybe_pyramid_add(pos, current_price, atr_val, structure)

            should_exit, reason, details = self.exit_engine.check_exit(
                pos,
                current_price,
                atr_val,
                protective_level=pos.protective_liq_level if pos.origin == "bot" else 0.0,
                allow_early_exit=(pos.origin == "bot"),
            )
            if should_exit and reason == ExitReason.EARLY_EXIT:
                # Parse numeric thresholds from exit details for actionable diagnostics.
                required_profit = effective_profit = raw_profit = best_profit = fee_floor = "n/a"
                parsed = re.search(
                    r"Profit\s+([+-]?\d+(?:\.\d+)?)\s*/\s*best\s+([+-]?\d+(?:\.\d+)?)\s*<\s*required\s+([+-]?\d+(?:\.\d+)?)\s*\(incl fees\s+([+-]?\d+(?:\.\d+)?)\)",
                    details or "",
                )
                if parsed:
                    raw_profit = parsed.group(1)
                    best_profit = parsed.group(2)
                    required_profit = parsed.group(3)
                    fee_floor = parsed.group(4)
                    try:
                        effective_profit = f"{max(float(raw_profit), float(best_profit)):.4f}"
                    except Exception:
                        effective_profit = "n/a"
                logger.info(
                    f"[EARLY_EXIT] {symbol} {pos.side} "
                    f"price={current_price:.4f} entry={pos.entry_price:.4f} "
                    f"best={pos.best_price:.4f} bars={pos.bars_since_entry} "
                    f"raw_profit={raw_profit} best_profit={best_profit} "
                    f"effective_profit={effective_profit} required_profit={required_profit} "
                    f"fee_floor={fee_floor} "
                    f"detail={details}"
                )
            # MANUAL SAFETY: only trailing_exit and tp_cap allowed for manual positions
            if should_exit and pos.origin == "manual" and reason not in (
                ExitReason.TRAILING_EXIT, ExitReason.TP_CAP
            ):
                logger.info(
                    f"[MANUAL SAFE] {symbol} exit blocked: {reason.value} — "
                    f"only trailing_exit/tp_cap allowed for manual positions. {details}"
                )
                should_exit = False

            # EMA TREND EXIT — close if price reverses against EMA(20)
            if not should_exit and self.ema_trend_exit_enabled:
                should_exit, reason, details = self.exit_engine.check_ema_trend_exit(
                    pos, klines, ema_period=self.ema_exit_period
                )

            if should_exit:
                close_result = await self.execution_engine.execute_close(symbol, pos.side, reason=f"{reason.value}: {details}", position_idx=pos.position_idx)
                if close_result.get("success"):
                    self._failed_close_attempts.pop(symbol, None)
                    pnl = self._calc_pnl(pos, current_price, pos.qty)
                    await self._finalize_full_close(symbol, pos, current_price, pnl, reason.value)
                else:
                    fails = self._failed_close_attempts.get(symbol, 0) + 1
                    self._failed_close_attempts[symbol] = fails
                    logger.warning(
                        f"[EXIT FAILED] {symbol} execute_close failed ({fails}/3): "
                        f"reason={reason.value} error={close_result.get('error', '?')}"
                    )
                    if fails >= 3:
                        error_msg = str(close_result.get("error", "")).lower()
                        position_gone = "not found" in error_msg or "position" in error_msg

                        if position_gone:
                            # Position no longer exists on exchange (user closed it, or exchange SL/TP hit)
                            # Finalize regardless of origin (manual or bot)
                            logger.warning(
                                f"[POSITION GONE] {symbol} — position not found on exchange after {fails} attempts. "
                                f"Finalizing as exchange_closed (origin={pos.origin})"
                            )
                            self._failed_close_attempts.pop(symbol, None)
                            removed = self.position_manager.remove(symbol)
                            if removed:
                                closed = await self.client.get_closed_pnl(symbol, limit=5)
                                recent_closed = self._filter_recent_closed_pnl(closed, max_age_sec=3600)
                                if recent_closed:
                                    pnl = float(recent_closed[0].get("closedPnl", 0) or 0)
                                    logger.info(f"[POSITION GONE] {symbol} exchange closedPnl: ${pnl:.4f}")
                                elif closed:
                                    pnl = float(closed[0].get("closedPnl", 0) or 0)
                                    logger.info(f"[POSITION GONE] {symbol} older closedPnl: ${pnl:.4f}")
                                else:
                                    pnl = self._calc_pnl(removed, current_price, removed.qty)
                                    logger.info(f"[POSITION GONE] {symbol} estimated pnl: ${pnl:.4f}")
                                exchange_close_records = recent_closed or closed
                                exchange_close_reason = self._classify_exchange_closed_reason(exchange_close_records)
                                self._set_exchange_close_meta(symbol, exchange_close_records)
                                await self._finalize_full_close(
                                    symbol,
                                    removed,
                                    current_price,
                                    pnl,
                                    exchange_close_reason,
                                    already_removed=True,
                                )
                                if self.tg:
                                    try:
                                        await self.tg.send_alert(
                                            f"[POSITION GONE] {symbol}\n"
                                            f"Position not found on exchange — removed.\n"
                                            f"Entry: {removed.entry_price:.4f} | PnL: ${pnl:.2f}\n"
                                            f"Origin: {removed.origin}"
                                        )
                                    except Exception:
                                        pass
                        # MANUAL POSITIONS: NEVER force-remove for non-"not found" errors
                        elif pos.origin == "manual":
                            logger.warning(
                                f"[MANUAL SAFE] {symbol} — {fails} close failures but origin=manual. "
                                f"NOT removing. Resetting counter. User must close manually."
                            )
                            self._failed_close_attempts.pop(symbol, None)
                            if self.tg:
                                try:
                                    await self.tg.send_alert(
                                        f"[MANUAL SAFE] {symbol}\n"
                                        f"Close failed {fails}x — position kept.\n"
                                        f"Entry: {pos.entry_price:.4f} | Current: {current_price:.4f}\n"
                                        f"Please close manually if needed."
                                    )
                                except Exception:
                                    pass
                        else:
                            logger.error(
                                f"[FORCE REMOVE] {symbol} — {fails} consecutive close failures. "
                                f"Removing zombie position (entry={pos.entry_price:.4f} current={current_price:.4f})"
                            )
                            self._failed_close_attempts.pop(symbol, None)
                            pos = self.position_manager.remove(symbol)
                            if pos:
                                # Get real PnL from exchange closedPnl
                                closed = await self.client.get_closed_pnl(symbol, limit=3)
                                recent_closed = self._filter_recent_closed_pnl(closed, max_age_sec=600)
                                if recent_closed:
                                    pnl = float(recent_closed[0].get("closedPnl", 0) or 0)
                                    logger.info(f"[FORCE REMOVE] {symbol} using exchange closedPnl: ${pnl:.4f}")
                                elif closed:
                                    pnl = float(closed[0].get("closedPnl", 0) or 0)
                                    logger.info(f"[FORCE REMOVE] {symbol} using older closedPnl: ${pnl:.4f}")
                                else:
                                    pnl = self._calc_pnl(pos, current_price, pos.qty)
                                    logger.info(f"[FORCE REMOVE] {symbol} no closedPnl, estimated: ${pnl:.4f}")
                                await self._finalize_full_close(symbol, pos, current_price, pnl, "force_closed_stale", already_removed=True)
            else:
                pass

        return total_unrealized
