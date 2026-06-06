"""Auto-split from main.TradingBot — see package bot.trading_bot."""
from __future__ import annotations

from bot.trading_bot_imports import *  # noqa: F401,F403

class TradingBotGuardsMixin:
    def _basket_effective_drawdown_confirm_sec(self) -> float:
        """Секунды подтверждения basket guard: вне fast-часов base, в окне fast (локальные часы, UTC+offset)."""
        base = float(getattr(self, "basket_drawdown_confirm_sec", 900.0) or 900.0)
        fast = float(
            getattr(self, "basket_fast_drawdown_confirm_sec", base) or base
        )
        hours = frozenset(getattr(self, "basket_fast_drawdown_confirm_local_hours", frozenset()) or frozenset())
        if not hours:
            return base
        off = int(getattr(self, "basket_fast_drawdown_confirm_tz_offset", 0) or 0)
        utc_h = datetime.now(timezone.utc).hour
        local_h = (utc_h + off) % 24
        if int(local_h) in hours:
            return fast
        return base

    async def _maybe_session_flatten(self):
        """Close all open positions shortly before configured UTC session hours."""
        if not getattr(self, "session_flatten_enabled", False):
            return
        target_hours = list(getattr(self, "session_flatten_utc_hours", []) or [])
        if not target_hours:
            return
        if self.position_manager.count() <= 0:
            return

        now = datetime.now(timezone.utc)
        lead_min = max(1, int(getattr(self, "session_flatten_lead_minutes", 10)))
        trigger_key = ""
        trigger_hour = None
        for hour in target_hours:
            target_dt = now.replace(hour=int(hour), minute=0, second=0, microsecond=0)
            if target_dt <= now:
                target_dt = target_dt + timedelta(days=1)
            minutes_to_target = (target_dt - now).total_seconds() / 60.0
            if 0 < minutes_to_target <= lead_min:
                trigger_key = target_dt.strftime("%Y-%m-%d-%H")
                trigger_hour = int(hour)
                break

        if not trigger_key:
            return
        if trigger_key == getattr(self, "_session_flatten_last_key", ""):
            return

        self._session_flatten_last_key = trigger_key
        msg = (
            f"SESSION FLATTEN: closing all positions {lead_min}m before UTC hour={trigger_hour:02d}. "
            f"open_positions={self.position_manager.count()}"
        )
        logger.warning(msg)
        if self.tg:
            try:
                await self.tg.send_message(
                    f"<b>SESSION FLATTEN</b>\n\n"
                    f"Закрываю все позиции перед риск-окном UTC <code>{trigger_hour:02d}:00</code>.\n"
                    f"Lead: <code>{lead_min} мин</code>\n"
                    f"Открытых позиций: <code>{self.position_manager.count()}</code>"
                )
            except Exception:
                pass

        for symbol in list(self.position_manager.symbols()):
            pos = self.position_manager.get(symbol)
            if not pos:
                continue
            if getattr(pos, "origin", "") == "manual":
                logger.info(f"SESSION FLATTEN: skip {symbol} (origin=manual)")
                continue
            try:
                current_price = await self.client.get_price(symbol)
                close_result = await self.execution_engine.execute_close(
                    symbol,
                    pos.side,
                    reason="session_flatten_preopen",
                    position_idx=pos.position_idx,
                )
                if close_result.get("success"):
                    pnl = self._calc_pnl(pos, current_price, pos.qty)
                    await self._finalize_full_close(
                        symbol, pos, current_price, pnl, "session_flatten_preopen"
                    )
            except Exception as exc:
                logger.warning(f"SESSION FLATTEN close failed for {symbol}: {exc}")

    async def _check_portfolio_take_profit(self, total_unrealized: float):
        if not self.portfolio_tp_enabled or total_unrealized <= 0 or self.position_manager.count() < 2:
            return
        balance = self.controls.get_balance()
        if balance <= 0:
            return
        target = balance * (self.portfolio_tp_target_pct / 100)
        if total_unrealized + 1e-9 < target:
            return
        logger.info(f"PORTFOLIO TP HIT: unrealized=${total_unrealized:.2f} target=${target:.2f}")
        if self.tg:
            await self.tg.send_message(
                f"<b>СУММАРНЫЙ TP ДОСТИГНУТ</b>\n\n"
                f"Нереализованный PnL: <code>${total_unrealized:.2f}</code>\n"
                f"Цель: <code>${target:.2f}</code>\n"
                f"Закрываю только позиции бота (ручные не трогаю)."
            )
        for symbol in list(self.position_manager.symbols()):
            pos = self.position_manager.get(symbol)
            if not pos:
                continue
            if getattr(pos, "origin", "") == "manual":
                logger.info(f"PORTFOLIO TP: skip {symbol} (origin=manual)")
                continue
            current_price = await self.client.get_price(symbol)
            close_result = await self.execution_engine.execute_close(symbol, pos.side, reason="portfolio_total_tp", position_idx=pos.position_idx)
            if close_result.get("success"):
                pnl = self._calc_pnl(pos, current_price, pos.qty)
                await self._finalize_full_close(symbol, pos, current_price, pnl, "portfolio_total_tp")
        self._reset_basket_profit_state()


    def _reset_basket_profit_state(self):
        self.basket_profit_state = BasketProfitState()


    async def _check_basket_profit_guard(self, total_unrealized: float):
        positions = self.position_manager.all_positions()
        if len(positions) < self.basket_profit_min_positions:
            self._reset_basket_profit_state()
            return

        now = time.time()
        self._update_basket_histories(positions, total_unrealized, now)
        peak = max((value for _, value in self.basket_profit_state.total_history), default=total_unrealized)
        self.basket_profit_state.peak_profit_usdt = max(self.basket_profit_state.peak_profit_usdt, peak)

        if total_unrealized < self.basket_profit_min_total_usdt:
            self.basket_profit_state.drawdown_detected_at = 0.0
            self.basket_profit_state.drawdown_confirm_lock_sec = 0.0
            return

        falling_symbol, symbol_drop_pct = self._find_falling_symbol(now)
        if not falling_symbol:
            self.basket_profit_state.drawdown_detected_at = 0.0
            self.basket_profit_state.drawdown_confirm_lock_sec = 0.0
            return

        if self.basket_profit_state.drawdown_detected_at > 0 and self.basket_profit_state.drawdown_confirm_lock_sec <= 0:
            self.basket_profit_state.drawdown_confirm_lock_sec = self._basket_effective_drawdown_confirm_sec()

        if self.basket_profit_state.drawdown_detected_at <= 0:
            confirm_for_wait = self._basket_effective_drawdown_confirm_sec()
            self.basket_profit_state.drawdown_detected_at = now
            self.basket_profit_state.drawdown_confirm_lock_sec = confirm_for_wait
            logger.info(
                f"BASKET GUARD: drawdown detected on {falling_symbol} ({symbol_drop_pct:.1f}%), starting {confirm_for_wait}s confirmation timer"
            )
            if self.tg:
                await self.tg.send_message(
                    f"<b>BASKET GUARD: ТАЙМЕР ЗАПУЩЕН</b>\n\n"
                    f"Символ: <code>{falling_symbol}</code>\n"
                    f"Падение PnL: <code>{symbol_drop_pct:.1f}%</code>\n"
                    f"Ждём {max(1, int(confirm_for_wait // 60))} мин. для подтверждения."
                )
            return

        lock_sec = self.basket_profit_state.drawdown_confirm_lock_sec or self._basket_effective_drawdown_confirm_sec()
        elapsed = now - self.basket_profit_state.drawdown_detected_at
        if elapsed < lock_sec:
            remaining = lock_sec - elapsed
            logger.info(f"BASKET GUARD: waiting for confirmation, {remaining:.0f}s remaining")
            return

        # Timer expired and drawdown persists — close falling symbol
        logger.info(f"BASKET GUARD: {lock_sec:.0f}s confirmed, closing {falling_symbol}")
        pos = self.position_manager.get(falling_symbol)
        if pos:
            if getattr(pos, "origin", "") == "manual":
                logger.warning(
                    f"BASKET GUARD: skip closing {falling_symbol} because origin=manual"
                )
                self.basket_profit_state.symbol_pnl_history.pop(falling_symbol, None)
                self.basket_profit_state.drawdown_detected_at = 0.0
                self.basket_profit_state.drawdown_confirm_lock_sec = 0.0
                return
            current_price = await self.client.get_price(falling_symbol)
            close_result = await self.execution_engine.execute_close(falling_symbol, pos.side, reason="basket_symbol_fall", position_idx=pos.position_idx)
            if close_result.get("success"):
                pnl = self._calc_pnl(pos, current_price, pos.qty)
                await self._finalize_full_close(falling_symbol, pos, current_price, pnl, "basket_symbol_fall")
                if self.tg:
                    await self.tg.send_message(
                        f"<b>BASKET GUARD: ПОДТВЕРЖДЕНО</b>\n\n"
                        f"Символ: <code>{falling_symbol}</code>\n"
                        f"Падение PnL за {max(1, int(lock_sec // 60))}м: <code>{symbol_drop_pct:.1f}%</code>\n"
                        f"Закрыт падающий символ."
                    )
                self.basket_profit_state.symbol_pnl_history.pop(falling_symbol, None)

        self.basket_profit_state.drawdown_detected_at = 0.0
        self.basket_profit_state.drawdown_confirm_lock_sec = 0.0

        remaining_positions = self.position_manager.all_positions()
        if len(remaining_positions) < 2:
            self._reset_basket_profit_state()
            return

        total_drawdown_pct = ((peak - total_unrealized) / peak) * 100 if peak > 0 else 0.0
        if total_drawdown_pct + 1e-9 < self.basket_profit_total_drawdown_pct:
            return

        logger.info(
            f"BASKET PROFIT GUARD HIT: total=${total_unrealized:.2f}, peak=${peak:.2f}, drawdown={total_drawdown_pct:.1f}%"
        )
        for symbol in list(self.position_manager.symbols()):
            pos = self.position_manager.get(symbol)
            if not pos:
                continue
            current_price = await self.client.get_price(symbol)
            close_result = await self.execution_engine.execute_close(symbol, pos.side, reason="basket_total_drawdown", position_idx=pos.position_idx)
            if close_result.get("success"):
                pnl = self._calc_pnl(pos, current_price, pos.qty)
                await self._finalize_full_close(symbol, pos, current_price, pnl, "basket_total_drawdown")
        self._reset_basket_profit_state()


    def _update_basket_histories(self, positions: dict, total_unrealized: float, now: float):
        self.basket_profit_state.total_history.append((now, total_unrealized))
        self.basket_profit_state.total_history = [item for item in self.basket_profit_state.total_history if now - item[0] <= self.basket_profit_window_sec]
        active_symbols = set(positions.keys())
        for symbol in list(self.basket_profit_state.symbol_pnl_history.keys()):
            if symbol not in active_symbols:
                self.basket_profit_state.symbol_pnl_history.pop(symbol, None)
        for symbol, pos in positions.items():
            history = self.basket_profit_state.symbol_pnl_history.setdefault(symbol, [])
            history.append((now, pos.unrealized_pnl))
            self.basket_profit_state.symbol_pnl_history[symbol] = [item for item in history if now - item[0] <= self.basket_profit_window_sec]


    def _find_falling_symbol(self, now: float) -> tuple[str | None, float]:
        worst_symbol = None
        worst_drop = 0.0
        for symbol, history in self.basket_profit_state.symbol_pnl_history.items():
            if len(history) < 2:
                continue
            peak = max(value for _, value in history)
            current = history[-1][1]
            if peak < self.basket_profit_min_symbol_peak:
                continue
            drop_pct = ((peak - current) / peak) * 100 if peak > 0 else 0.0
            if drop_pct >= self.basket_profit_symbol_drop_pct and drop_pct > worst_drop:
                worst_symbol = symbol
                worst_drop = drop_pct
        return worst_symbol, worst_drop
