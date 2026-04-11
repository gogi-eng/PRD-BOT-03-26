"""Auto-split from main.TradingBot — see package bot.trading_bot."""
from __future__ import annotations

from bot.trading_bot_imports import *  # noqa: F401,F403

class TradingBotGuardsMixin:
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
                f"Закрываю все позиции аккаунта."
            )
        for symbol in list(self.position_manager.symbols()):
            pos = self.position_manager.get(symbol)
            if not pos:
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
            return

        falling_symbol, symbol_drop_pct = self._find_falling_symbol(now)
        if not falling_symbol:
            self.basket_profit_state.drawdown_detected_at = 0.0
            return

        # --- 15-minute confirmation timer ---
        if self.basket_profit_state.drawdown_detected_at <= 0:
            self.basket_profit_state.drawdown_detected_at = now
            logger.info(f"BASKET GUARD: drawdown detected on {falling_symbol} ({symbol_drop_pct:.1f}%), starting {self.basket_drawdown_confirm_sec}s confirmation timer")
            if self.tg:
                await self.tg.send_message(
                    f"<b>BASKET GUARD: ТАЙМЕР ЗАПУЩЕН</b>\n\n"
                    f"Символ: <code>{falling_symbol}</code>\n"
                    f"Падение PnL: <code>{symbol_drop_pct:.1f}%</code>\n"
                    f"Ждём {int(self.basket_drawdown_confirm_sec / 60)} мин. для подтверждения."
                )
            return

        elapsed = now - self.basket_profit_state.drawdown_detected_at
        if elapsed < self.basket_drawdown_confirm_sec:
            remaining = self.basket_drawdown_confirm_sec - elapsed
            logger.info(f"BASKET GUARD: waiting for confirmation, {remaining:.0f}s remaining")
            return

        # Timer expired and drawdown persists — close falling symbol
        logger.info(f"BASKET GUARD: {self.basket_drawdown_confirm_sec}s confirmed, closing {falling_symbol}")
        pos = self.position_manager.get(falling_symbol)
        if pos:
            current_price = await self.client.get_price(falling_symbol)
            close_result = await self.execution_engine.execute_close(falling_symbol, pos.side, reason="basket_symbol_fall", position_idx=pos.position_idx)
            if close_result.get("success"):
                pnl = self._calc_pnl(pos, current_price, pos.qty)
                await self._finalize_full_close(falling_symbol, pos, current_price, pnl, "basket_symbol_fall")
                if self.tg:
                    await self.tg.send_message(
                        f"<b>BASKET GUARD: ПОДТВЕРЖДЕНО</b>\n\n"
                        f"Символ: <code>{falling_symbol}</code>\n"
                        f"Падение PnL за {int(self.basket_drawdown_confirm_sec / 60)}м: <code>{symbol_drop_pct:.1f}%</code>\n"
                        f"Закрыт падающий символ."
                    )
                self.basket_profit_state.symbol_pnl_history.pop(falling_symbol, None)

        self.basket_profit_state.drawdown_detected_at = 0.0

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
