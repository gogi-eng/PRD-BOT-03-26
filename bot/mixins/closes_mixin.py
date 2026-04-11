"""Auto-split from main.TradingBot — see package bot.trading_bot."""
from __future__ import annotations

from bot.trading_bot_imports import *  # noqa: F401,F403

class TradingBotClosesMixin:
    async def _finalize_full_close(self, symbol: str, pos: Position, exit_price: float, pnl: float, reason: str, already_removed: bool = False):
        self._missing_exchange_cycles.pop(symbol, None)
        if reason == "exchange_closed" or reason.startswith("exchange_closed_"):
            self._set_exchange_closed_reentry_block(symbol)
        if not already_removed:
            self.position_manager.remove(symbol)
        close_meta = self._pop_exchange_close_meta(symbol)
        self.risk_guard.record_trade(pnl, symbol, reason=reason)
        self.controls.add_trade(pnl, symbol, pos.side, reason)
        self._save_trade(
            symbol,
            pos.side,
            pos.qty,
            pos.entry_price,
            exit_price,
            pnl,
            reason,
            origin=pos.origin,
            exchange_close_meta=close_meta,
        )
        logger.info(f"CLOSED {symbol}: pnl=${pnl:.2f} reason={reason}")
        if self.tg:
            pnl_pct = self._calc_pnl_pct(pos, exit_price)
            direction = "ЛОНГ" if pos.is_long else "ШОРТ"
            sign = "+" if pnl >= 0 else ""
            await self.tg.send_message(
                f"<b>СДЕЛКА ЗАКРЫТА</b>\n\n"
                f"Монета: <code>{symbol}</code>\n"
                f"Направление: <b>{direction}</b>\n"
                f"Вход: <code>${pos.entry_price:.4f}</code>\n"
                f"Выход: <code>${exit_price:.4f}</code>\n"
                f"Объём: <code>{pos.qty}</code>\n\n"
                f"Результат: <b>{sign}${pnl:.2f}</b> ({sign}{pnl_pct:.2f}%)\n"
                f"Причина: {reason}"
            )


    async def _finalize_partial_close(self, symbol: str, pos: Position, exit_price: float, qty: float, reason: str):
        pnl = self._calc_pnl(pos, exit_price, qty)
        self.risk_guard.record_trade(pnl, symbol, reason=reason)
        self.controls.add_trade(pnl, symbol, pos.side, reason)
        self._save_trade(symbol, pos.side, qty, pos.entry_price, exit_price, pnl, reason, origin=pos.origin)
        self.position_manager.reduce(symbol, qty)
        logger.info(f"REDUCED {symbol}: qty={qty:.6f} pnl=${pnl:.2f} reason={reason}")


    def _calc_pnl(self, pos: Position, exit_price: float, qty: float) -> float:
        if pos.is_long:
            raw_pnl = (exit_price - pos.entry_price) * qty
        else:
            raw_pnl = (pos.entry_price - exit_price) * qty
        # Deduct trading fees (entry + exit)
        entry_fee = pos.entry_price * qty * self.fee_rate
        exit_fee = exit_price * qty * self.fee_rate
        return raw_pnl - entry_fee - exit_fee


    def _calc_pnl_pct(self, pos: Position, price: float) -> float:
        if pos.entry_price <= 0:
            return 0.0
        if pos.is_long:
            raw_pct = (price - pos.entry_price) / pos.entry_price * 100
        else:
            raw_pct = (pos.entry_price - price) / pos.entry_price * 100
        # Deduct fee percentage (entry + exit ≈ 2x fee_rate)
        return raw_pct - (self.fee_rate * 2 * 100)


    def _save_trade(
        self,
        symbol: str,
        side: str,
        qty: float,
        entry: float,
        exit_price: float,
        pnl: float,
        reason: str,
        origin: str = "bot",
        exchange_close_meta: dict | None = None,
    ):
        trade = {
            "time": datetime.now(timezone.utc).isoformat(),
            "symbol": symbol,
            "side": side,
            "qty": qty,
            "entry": entry,
            "exit": exit_price,
            "pnl": round(pnl, 2),
            "pnl_pct": round((pnl / (entry * qty)) * 100, 2) if entry * qty > 0 else 0,
            "strategy": "ai_fund_entry_engine",
            "reason": reason,
            "origin": origin,
        }
        if exchange_close_meta:
            trade["exchange_close_meta"] = exchange_close_meta
        history_path = resolve_bot_dir() / "trade_history.json"
        try:
            if history_path.exists():
                with open(history_path, "r", encoding="utf-8") as handle:
                    history = json.load(handle)
            else:
                history = []
            history.append(trade)
            with open(history_path, "w", encoding="utf-8") as handle:
                json.dump(history, handle, indent=2, ensure_ascii=False)
        except Exception as exc:
            logger.error(f"Error saving trade: {exc}")
