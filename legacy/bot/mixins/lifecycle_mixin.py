"""Auto-split from main.TradingBot — see package bot.trading_bot."""
from __future__ import annotations

from bot.trading_bot_imports import *  # noqa: F401,F403

class TradingBotLifecycleMixin:
    async def run(self):
        logger.info("=" * 72)
        logger.info("TRADING BOT v9.0 — AI FUND ARCHITECTURE")
        logger.info("DATA → STRUCTURE → SWEEP/BOS → ENTRY → EXECUTION")
        logger.info("=" * 72)
        logger.info(
            f"Entry threshold={self.entry_engine.entry_threshold:.2f} | "
            f"same-side cooldown={self.signal_cooldown_sec}s"
        )
        _soft_th = getattr(self.entry_engine, "entry_threshold_soft", None)
        if _soft_th is not None:
            logger.info(
                f"Entry soft floor={float(_soft_th):.2f} (signals between soft and hard pass for ranking)"
            )
        _bpr = getattr(self, "bpr_ranker", None)
        if _bpr is not None and _bpr.enabled:
            logger.info(
                f"BPR ranker: ON blend={_bpr.blend_weight:.2f} top1_if_multi={_bpr.top1_when_multiple} "
                f"telegram_top={_bpr.telegram_top_n}"
            )
        else:
            logger.info("BPR ranker: OFF")
        if getattr(self.entry_engine, "_trained_model", None) is not None:
            logger.info(
                "Trained model gate: ON "
                f"(min_prob={self.entry_engine.trained_model_min_prob:.2f}, "
                f"blend={self.entry_engine.trained_model_blend:.2f})"
            )
        else:
            logger.info("Trained model gate: OFF (checkpoint missing or disabled)")
        logger.info(
            "Quality gate: "
            f"{'ON' if self.quality_gate_enabled else 'OFF'} "
            f"(min_conf={self.quality_gate_min_confidence:.2f}, "
            f"min_edge={self.quality_gate_min_expected_edge:.2f}, "
            f"reject_no_zone={self.quality_gate_reject_no_zone_entries})"
        )
        logger.info(
            "Quality chop policy: "
            f"allow_chop={self.quality_gate_allow_chop} | "
            f"bypass={'ON' if self.quality_gate_chop_bypass_enabled else 'OFF'} "
            f"(conf>={self.quality_gate_chop_bypass_min_confidence:.2f}, "
            f"|imb|>={self.quality_gate_chop_bypass_min_abs_imbalance:.2f}, "
            f"require_zone={self.quality_gate_chop_bypass_require_zone})"
        )
        logger.info(
            "Entry hard-gates: "
            f"smc>={self.entry_min_smc_score:.2f} | "
            f"|imb|>={self.entry_min_orderflow_imbalance_norm:.2f} "
            f"(cfg={self.entry_min_orderflow_imbalance:.2f}) | "
            f"atr%>={self.entry_min_volatility_pct:.2f} | "
            f"require_sweep={self.entry_require_sweep} | "
            f"require_4h_trend={self.entry_require_4h_trend}"
        )
        logger.info(
            "Entry peak guard: "
            f"{'ON' if self.entry_peak_reversal_guard else 'OFF'} "
            f"(lookback={self.entry_peak_lookback_bars}, "
            f"dist_atr={self.entry_peak_distance_atr:.2f}, "
            f"bypass_conf={self.entry_peak_confidence_bypass:.2f})"
        )
        logger.info(
            "Impulse-retest confirm: "
            f"{'ON' if self.entry_impulse_retest_confirm_enabled else 'OFF'} "
            f"(impulse_body_atr>={self.entry_impulse_min_body_atr:.2f}, "
            f"retest_max_atr={self.entry_retest_max_body_atr:.2f}, "
            f"bypass_conf={self.entry_impulse_confirm_conf_bypass:.2f})"
        )
        if self.signal_only:
            logger.info(
                "Signal feedback loop: "
                f"{'ON' if self.signal_feedback.enabled else 'OFF'} "
                f"(pending timeout={self.signal_feedback.max_pending_hours}h)"
            )
            logger.info(
                f"SIGNAL-ONLY output: Telegram + feedback only if confidence > "
                f"{self.signal_only_min_confidence:.0%} (bot.signal_only_min_confidence)"
            )
        logger.info(
            f"Correlation filter: {'ON' if self.correlation_filter_enabled else 'OFF'} "
            f"(thr={self.correlation_filter.threshold:.2f})"
        )
        logger.info(
            f"MTF zone confirmation: {'ON' if self.mtf_zone_enabled else 'OFF'} "
            f"(single_tf_min_conf={self.mtf_zone_min_confidence_if_single_tf:.2f})"
        )
        logger.info(
            f"Strict HTF mode: {'ON' if self.strict_htf_mode else 'OFF'} | "
            f"Volatility floor: {'ON' if self.volatility_floor_enabled else 'OFF'} "
            f"(ATR%>={self.volatility_floor_atr_pct:.2f})"
        )
        logger.info(
            f"Adaptive presets: {'ON' if self.adaptive_regime_presets_enabled else 'OFF'} "
            f"(interval={self.adaptive_regime_presets_interval_sec}s, benchmark={self.adaptive_regime_presets_benchmark_symbol})"
        )
        logger.info(
            f"SCALP session strategy: {'ON' if self.scalp_strategy.enabled else 'OFF'} "
            f"(UTC+{self.scalp_strategy.timezone_offset} "
            f"pump={sorted(self.scalp_strategy.pump_hours_local)} "
            f"dump={sorted(self.scalp_strategy.dump_hours_local)})"
        )
        logger.info(
            "SCALP unblock profile: "
            f"of_relax={self.scalp_orderflow_hardgate_mult:.2f} "
            f"of_floor={self.scalp_orderflow_hardgate_floor:.2f} "
            f"impulse_bypass_conf={self.scalp_impulse_retest_bypass_confidence:.2f} "
            f"quality_conf={self.scalp_quality_min_confidence:.2f} "
            f"quality_edge={self.scalp_quality_min_expected_edge:.2f}"
        )
        logger.info(
            f"Entry capital weight mode: {self.entry_capital_weight_mode.upper()}"
        )
        logger.info(
            f"Symbol quality filter: {'ON' if self.symbol_quality_filter.enabled else 'OFF'}"
        )
        logger.info(
            "Adaptive recommendations: "
            f"{'ON' if self.adaptive_recommendations.enabled else 'OFF'} "
            f"(window={self.adaptive_recommendations.lookback_hours:.0f}h, "
            f"interval={self.adaptive_recommendations.interval_sec}s, "
            f"auto_apply={'ON' if self.adaptive_recommendations.auto_apply_enabled else 'OFF'})"
        )
        logger.info(
            "Manual trade learning: "
            f"{'ON' if self.manual_trade_learner.enabled else 'OFF'} "
            f"(lookback={self.manual_trade_learner.lookback_days:.0f}d, "
            f"min_winners={self.manual_trade_learner.min_manual_winners}, "
            f"max_conf_boost={self.manual_trade_learner.max_confidence_boost:.2f})"
        )
        logger.info(
            f"Local advisor: {'ON' if self.advisor.enabled else 'OFF'} "
            f"(mode={self.advisor.mode})"
        )
        logger.info(
            "Position sync: "
            f"adopt_all_positions={'ON' if self.adopt_all_positions else 'OFF'} | "
            f"preserve_existing_sl_tp={'ON' if self.preserve_existing_sl_tp else 'OFF'} | "
            f"exchange_closed_confirm={self.exchange_closed_confirm_cycles} | "
            f"exchange_closed_force={self.exchange_closed_force_cycles}"
        )
        tg_started = False
        try:
            logger.info("[STARTUP] Validating API credentials...")
            ok, err = self.security.validate_bybit_keys()
            if not ok:
                logger.error(f"Bybit keys: {err}")
                return

            if self.tg:
                logger.info("[STARTUP] Starting Telegram polling...")
                try:
                    await asyncio.wait_for(self.tg.start_async(), timeout=45)
                except asyncio.TimeoutError:
                    logger.error("[STARTUP] Telegram polling start timeout after 45s. Continuing without Telegram.")
                    self.tg = None
                except Exception as exc:
                    logger.error(f"[STARTUP] Telegram start failed: {exc}")
                    self.tg = None
                if self.tg:
                    tg_started = True

            logger.info("[STARTUP] Fetching account balance...")
            startup_balance_ok = True
            startup_balance_error = ""
            balance = 0.0
            for attempt in range(1, 4):
                try:
                    balance = await asyncio.wait_for(self.client.get_balance(), timeout=30)
                    if balance > 0:
                        break
                    startup_balance_error = (
                        "Failed to read positive balance. "
                        "Check Bybit API key/secret permissions and expiration."
                    )
                except asyncio.TimeoutError:
                    startup_balance_error = "get_balance timeout after 30s. Check VPS network/API reachability."
                    logger.error(f"[STARTUP] {startup_balance_error} (attempt {attempt}/3)")
                if attempt < 3:
                    await asyncio.sleep(2.0)
            bybit_perm_code = int(getattr(self.client, "last_auth_error_code", 0) or 0)
            if bybit_perm_code in {10005, 33004}:
                logger.error(
                    "Bybit auth/permission failure detected at startup "
                    f"(code={bybit_perm_code}). "
                    "Stop bot and fix API key permissions/expiration."
                )
                return
            if balance <= 0:
                startup_balance_ok = False
                if not startup_balance_error:
                    startup_balance_error = (
                        "Failed to read positive balance. "
                        "Check Bybit API key/secret permissions and expiration."
                    )
                logger.error(startup_balance_error)

            if startup_balance_ok:
                self.controls.set_balance(balance)
                self.risk_guard.initial_balance = balance
                self.profit_lock.set_initial_balance(balance)
                logger.info(f"Balance: ${balance:.2f}")
            else:
                # Keep bot alive for monitoring/Telegram even if balance API is unhealthy.
                # Force signal-only to avoid live orders with unknown balance state.
                self.signal_only = True
                self.controls.signal_only = True
                logger.warning("[STARTUP] Balance unavailable -> forcing SIGNAL-ONLY mode.")

            if self.tg and tg_started:
                logger.info("[STARTUP] Sending startup message to Telegram...")
                try:
                    balance_display = f"${balance:.2f}" if startup_balance_ok else "N/A"
                    startup_text = (
                        f"<b>Бот v9.0 запущен</b>\n"
                        f"Баланс: <code>{balance_display}</code>\n"
                        f"Режим: {'СИГНАЛЫ' if self.signal_only else ('ТЕСТ' if self.controls.dry_run else 'LIVE')}\n"
                        f"Стратегия: SMC v3 (Sweep→BOS→Retest OB/FVG) + AI + Pyramid"
                    )
                    if not startup_balance_ok and startup_balance_error:
                        startup_text += f"\n⚠️ Balance warning: <code>{startup_balance_error}</code>"
                    await asyncio.wait_for(self.tg.send_message(startup_text), timeout=20)
                except Exception as exc:
                    logger.warning(f"[STARTUP] Telegram startup message failed: {exc}")

            self._running = True
            cycle = 0
            while self._running and not self._stop_event.is_set():
                try:
                    stage_timeout = max(10.0, float(getattr(self, "runtime_stage_timeout_sec", 60.0)))
                    scan_timeout = max(stage_timeout, float(getattr(self, "runtime_scan_timeout_sec", 180.0)))
                    cycle += 1
                    logger.info(f"\n{'=' * 36} CYCLE {cycle} {'=' * 36}")
                    logger.info(f"[CYCLE {cycle}] stage=balance")
                    balance = await asyncio.wait_for(self.client.get_balance(), timeout=stage_timeout)
                    if balance > 0:
                        self.controls.set_balance(balance)
                        if self.risk_guard.initial_balance <= 0:
                            self.risk_guard.initial_balance = balance
                        self.profit_lock.set_initial_balance(balance)

                    logger.info(f"[CYCLE {cycle}] stage=positions")
                    exchange_positions = await asyncio.wait_for(
                        self.client.get_positions(), timeout=stage_timeout
                    )
                    exchange_symbols = [item["symbol"] for item in exchange_positions]
                    logger.info(f"[CYCLE {cycle}] stage=symbols")
                    symbols = await asyncio.wait_for(self.get_trade_symbols(), timeout=stage_timeout)
                    subscribed = self._unique_symbols(exchange_symbols + self.position_manager.symbols() + symbols)[: self.max_stream_symbols]
                    logger.info(f"[CYCLE {cycle}] stage=liq_stream symbols={len(subscribed)}")
                    await asyncio.wait_for(
                        self.client.set_liquidation_symbols(subscribed), timeout=stage_timeout
                    )

                    await self._maybe_apply_regime_preset()
                    await self._apply_strategy_presets()
                    st_to = max(10.0, float(getattr(self, "runtime_stage_timeout_sec", 60.0) or 60.0))
                    await self._meta_stack_cycle_update(cycle, st_to)

                    if self.signal_only and self.signal_feedback.enabled:
                        await self._process_signal_feedback_loop()

                    await self.adaptive_recommendations.maybe_emit(self.tg)
                    await self.adaptive_recommendations.maybe_apply_runtime_tuning(self, self.tg)

                    if not self.signal_only:
                        await self._maybe_session_flatten()
                        logger.info(f"[CYCLE {cycle}] stage=manage_positions")
                        total_unrealized = await asyncio.wait_for(
                            self._manage_positions(exchange_positions), timeout=scan_timeout
                        )

                        if self.basket_profit_guard_enabled and self.position_manager.count() >= self.basket_profit_min_positions:
                            await self._check_basket_profit_guard(total_unrealized)

                        if self.portfolio_tp_enabled:
                            bot_tp_positions = self._positions_for_portfolio_tp()
                            min_bot = int(getattr(self, "portfolio_tp_min_bot_positions", 2))
                            if len(bot_tp_positions) >= min_bot:
                                await self._check_portfolio_take_profit(total_unrealized)

                        profit_lock_positions = self.position_manager.all_positions()
                        if getattr(self, "profit_lock_skip_manual", True):
                            profit_lock_positions = {
                                symbol: pos
                                for symbol, pos in profit_lock_positions.items()
                                if getattr(pos, "origin", "") != "manual"
                            }
                        if profit_lock_positions:
                            closed_symbols = await self.profit_lock.check(profit_lock_positions) or []
                            for symbol in closed_symbols:
                                pos = self.position_manager.get(symbol)
                                if pos:
                                    current_price = await self.client.get_price(symbol)
                                    await self._finalize_full_close(symbol, pos, current_price, 0.0, "profit_lock")

                    if self.controls.enabled and not self.controls.emergency:
                        can_trade, reason = self.risk_guard.can_trade()
                        if (
                            can_trade
                            and (self.signal_only or self.position_manager.count() < self.controls.max_positions)
                            and self._meta_stack_allows_entry_scan()
                        ):
                            if self._should_scan_entries_now():
                                logger.info(f"[CYCLE {cycle}] stage=scan_entries symbols={len(symbols)}")
                                await asyncio.wait_for(
                                    self._scan_entries(symbols), timeout=scan_timeout
                                )
                        elif not can_trade:
                            logger.info(f"Trading blocked: {reason}")
                    else:
                        guard_allows, guard_reason = self.risk_guard.can_trade()
                        logger.info(
                            "Bot paused: controls_enabled=%s emergency=%s guard_allows_trade=%s guard_reason='%s'",
                            self.controls.enabled,
                            self.controls.emergency,
                            guard_allows,
                            guard_reason or "",
                        )

                    self.controls.set_positions(self.position_manager.to_controls_dict())
                    sleep_sec = self._get_cycle_sleep_sec()
                    logger.info(f"Cycle {cycle} done. Sleeping {sleep_sec}s...")
                    await asyncio.sleep(sleep_sec)
                except asyncio.CancelledError:
                    break
                except asyncio.TimeoutError as exc:
                    logger.error(f"Cycle {cycle} timeout: {exc}")
                    await asyncio.sleep(5)
                except Exception as exc:
                    logger.error(f"Cycle error: {exc}", exc_info=True)
                    await asyncio.sleep(20)
        finally:
            self._running = False
            try:
                await self.client.close()
            except Exception as exc:
                logger.warning(f"Client close warning: {exc}")
            if self.tg:
                if tg_started:
                    try:
                        await self.tg.send_message("<b>Бот остановлен</b>")
                    except Exception:
                        pass
                try:
                    await self.tg.stop_async()
                except Exception:
                    pass


    def stop(self):
        self._running = False
        self._stop_event.set()



    def _should_scan_entries_now(self) -> bool:
        interval = max(5, int(self.scan_interval_sec))
        active_interval = max(5, int(self.scan_interval_active_hours_sec))
        # During configured scalp hot hours, use tighter scan cadence.
        if getattr(self, "scalp_strategy", None) and self.scalp_strategy.enabled:
            now_utc = datetime.now(timezone.utc)
            local_hour = (now_utc.hour + int(self.scalp_strategy.timezone_offset)) % 24
            active_hours = set(self.scalp_strategy.pump_hours_local) | set(
                self.scalp_strategy.dump_hours_local
            )
            if local_hour in active_hours:
                interval = min(interval, active_interval)
        now = time.time()
        if self._last_scan_ts <= 0 or (now - self._last_scan_ts) >= interval:
            self._last_scan_ts = now
            return True
        return False


    def _get_cycle_sleep_sec(self) -> int:
        base_sleep = max(5, int(self.cycle_sleep))
        if self.signal_only:
            return base_sleep
        if self.position_manager.count() > 0:
            active_sleep = max(5, int(self.position_active_sleep_sec))
            return min(active_sleep, base_sleep)
        return base_sleep
