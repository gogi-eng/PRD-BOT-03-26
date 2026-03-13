#!/usr/bin/env python3
"""
TRADING BOT v8.0 — Clean Architecture.
MAIN.PY — единственная точка входа.

Pipeline:
MARKET DATA → MARKET ANALYZER → ENTRY ENGINE → RISK MANAGER → EXECUTION ENGINE → POSITION MANAGER → EXIT ENGINE

Запуск: python main.py
"""
from __future__ import annotations
import asyncio
import os
import sys
import signal
import threading
import logging
import time
import json
from pathlib import Path
from datetime import datetime, timezone

from dotenv import load_dotenv

# Добавляем корень бота в sys.path
BOT_DIR = Path(__file__).parent.resolve()
if str(BOT_DIR) not in sys.path:
    sys.path.insert(0, str(BOT_DIR))

from core.config import BotConfig
from core.security import SecureStore
from core.live_controls import LiveControls
from exchange.bybit_client import BybitClient
from tg.controller import TelegramController

from analysis.market_analyzer import MarketAnalyzer, TrendDirection
from analysis.liquidity_sweep import LiquiditySweepDetector
from analysis.funding_filter import FundingFilter
from analysis.correlation_filter import CorrelationFilter
from analysis.liquidation_clusters import LiquidationClusterDetector
from analysis.ai_analyzer import AITradeAnalyzer

from engine.entry_engine import EntryEngine, EntrySignal
from engine.risk_manager import RiskGuard
from engine.execution_engine import ExecutionEngine
from engine.position_manager import PositionManager, Position
from engine.exit_engine import ExitEngine, ExitReason

from portfolio_profit_lock import PortfolioProfitLock
from utils import ATRCalculator

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("BOT")


class TradingBot:
    """Основной класс бота."""

    def __init__(self):
        # Config
        load_dotenv(override=True)
        self.cfg = BotConfig.load(str(BOT_DIR / "config.yaml"))
        self.security = SecureStore()

        # Controls
        self.controls = LiveControls(
            enabled=True,
            dry_run=False,
            leverage=self.cfg.get("trading", "leverage", default=20),
            margin_total_pct=self.cfg.get("trading", "margin_total_pct", default=10.0),
            risk_per_trade_pct=self.cfg.get("trading", "risk_per_trade_pct", default=2.0),
            tp_pct=self.cfg.get("trading", "tp_pct", default=3.0),
            sl_pct=self.cfg.get("trading", "sl_pct", default=1.5),
            max_positions=self.cfg.get("trading", "max_positions", default=3),
            trailing_stop_pct=self.cfg.get("trading", "trailing_stop_pct", default=1.5),
        )

        # Exchange
        api_key = self.security.get_key("BYBIT_API_KEY")
        api_secret = self.security.get_key("BYBIT_API_SECRET")
        testnet = self.cfg.get("bybit", "testnet", default=False)
        self.client = BybitClient(api_key, api_secret, testnet=testnet)

        # Risk Manager (ONE)
        self.risk_guard = RiskGuard(
            max_consecutive_losses=self.cfg.get("risk", "max_consecutive_losses", default=4),
            max_daily_loss_pct=self.cfg.get("risk", "max_daily_loss_pct", default=5.0),
            max_daily_loss_usdt=self.cfg.get("risk", "max_daily_loss_usdt", default=100),
            max_trades_per_day=self.cfg.get("risk", "max_trades_per_day", default=20),
            max_positions=self.cfg.get("trading", "max_positions", default=3),
            max_trades_per_symbol_24h=self.cfg.get("risk", "max_trades_per_symbol_24h", default=8),
            cooldown_after_loss_sec=self.cfg.get("risk", "cooldown_after_loss_sec", default=300),
            cooldown_after_stop_hours=self.cfg.get("risk", "cooldown_after_stop_hours", default=2),
            reduce_after_losses=self.cfg.get("risk", "reduce_after_losses", default=2),
            reduction_factor=self.cfg.get("risk", "reduction_factor", default=0.5),
        )
        self.controls.set_guard(self.risk_guard)

        # Analysis
        self.market_analyzer = MarketAnalyzer()
        self.sweep_detector = LiquiditySweepDetector(
            lookback=self.cfg.get("liquidity", "lookback_bars", default=20),
            wick_ratio=self.cfg.get("liquidity", "wick_ratio", default=2.0),
            min_break_pct=self.cfg.get("liquidity", "min_break_pct", default=0.1),
        )
        self.funding_filter = FundingFilter(
            high_threshold=self.cfg.get("funding", "high_threshold", default=0.0005),
            extreme_threshold=self.cfg.get("funding", "extreme_threshold", default=0.001),
        )
        self.correlation_filter = CorrelationFilter(
            threshold=self.cfg.get("correlation", "threshold", default=0.70),
            max_correlated=self.cfg.get("correlation", "max_correlated", default=1),
        )
        self.liq_detector = LiquidationClusterDetector()
        self.ai_analyzer = AITradeAnalyzer()
        self.atr = ATRCalculator(period=self.cfg.get("atr", "period", default=14))

        # Engines
        self.entry_engine = EntryEngine(self.cfg)
        self.position_manager = PositionManager()
        self.exit_engine = ExitEngine(
            hard_sl_atr_mult=self.cfg.get("exit", "hard_sl_atr_mult", default=2.0),
            early_exit_bars=self.cfg.get("exit", "early_exit_bars", default=10),
            early_exit_min_profit_atr=self.cfg.get("exit", "early_exit_min_profit_atr", default=0.5),
            trailing_activation_atr=self.cfg.get("exit", "trailing_activation_atr", default=1.0),
            trailing_distance_atr=self.cfg.get("exit", "trailing_distance_atr", default=1.5),
            tp_cap_atr_mult=self.cfg.get("exit", "tp_cap_atr_mult", default=10.0),
        )

        # Telegram (optional)
        self.tg = None
        tg_token = self.security.get_key("TELEGRAM_TOKEN")
        tg_chat_id = self.security.get_key("TELEGRAM_CHAT_ID")
        if tg_token:
            self.tg = TelegramController(
                token=tg_token,
                controls=self.controls,
                allowed_chat_id=int(tg_chat_id) if tg_chat_id else None,
            )
            self.risk_guard.set_notify_callback(self._notify_tg)

        # Execution Engine
        self.execution_engine = ExecutionEngine(self.client, self.controls, self.tg)

        # Portfolio Profit Lock
        self.profit_lock = PortfolioProfitLock(
            client=self.client,
            tg=self.tg,
            min_profit_pct=self.cfg.get("profit_lock", "min_profit_pct", default=5.0),
            decline_threshold_pct=self.cfg.get("profit_lock", "decline_threshold_pct", default=20.0),
            decline_duration_sec=self.cfg.get("profit_lock", "decline_duration_sec", default=300.0),
            cooldown_sec=self.cfg.get("profit_lock", "cooldown_sec", default=3600.0),
            dry_run=self.controls.dry_run,
        )

        # Connect profit_lock to TG
        if self.tg:
            self.tg.set_profit_lock(self.profit_lock)

        # State
        self._running = False
        self._stop_event = threading.Event()

        # Trading params
        self.candle_interval = self.cfg.get("bot", "candle_interval", default="5")
        self.htf_interval = self.cfg.get("bot", "htf_interval", default="240")
        self.cycle_sleep = self.cfg.get("bot", "cycle_sleep_sec", default=60)
        self.klines_limit = self.cfg.get("bot", "klines_limit", default=200)
        self.min_volume = self.cfg.get("market", "min_24h_volume_usdt", default=50_000_000)
        self.max_symbols = self.cfg.get("market", "max_symbols", default=40)
        self.trade_symbols = self.cfg.get("market", "trade_symbols", default=20)
        self.whitelist_enabled = self.cfg.get("market", "whitelist_enabled", default=True)
        self.whitelist = self.cfg.get("market", "whitelist_symbols", default=[])
        self.blacklist = self.cfg.get("trading", "blacklist_symbols", default=[])
        self.blacklist_substrings = self.cfg.get("market", "blacklist_substrings", default=[])

    async def _notify_tg(self, message: str):
        if self.tg:
            await self.tg.send_alert(message)

    # === Symbol selection ===

    async def get_trade_symbols(self) -> list:
        """Отбор символов для торговли."""
        try:
            tickers = await self.client.get_tickers()
        except Exception as e:
            logger.error(f"Failed to get tickers: {e}")
            return self.whitelist[:self.trade_symbols] if self.whitelist else []

        filtered = []
        for t in tickers:
            symbol = t.get("symbol", "")
            if not symbol.endswith("USDT"):
                continue
            if symbol in self.blacklist:
                continue
            if any(sub in symbol for sub in self.blacklist_substrings):
                continue
            volume = float(t.get("turnover24h", 0))
            if volume < self.min_volume:
                continue
            filtered.append((symbol, volume))

        filtered.sort(key=lambda x: x[1], reverse=True)
        symbols = [s[0] for s in filtered[:self.max_symbols]]

        # Whitelist always included
        if self.whitelist_enabled:
            for ws in self.whitelist:
                if ws not in symbols:
                    symbols.insert(0, ws)

        return symbols[:self.trade_symbols]

    # === Main loop ===

    async def run(self):
        """Запуск бота."""
        logger.info("=" * 60)
        logger.info("TRADING BOT v8.0 — Clean Architecture")
        logger.info("Pipeline: DATA → ANALYZER → ENTRY → RISK → EXEC → POS → EXIT")
        logger.info("=" * 60)

        # Validate keys
        ok, err = self.security.validate_bybit_keys()
        if not ok:
            logger.error(f"Bybit keys: {err}")
            return

        # Get balance
        balance = await self.client.get_balance()
        if balance <= 0:
            logger.error("Zero balance!")
            return
        self.controls.set_balance(balance)
        self.risk_guard.initial_balance = balance
        self.profit_lock.set_initial_balance(balance)
        logger.info(f"Balance: ${balance:.2f}")

        # Start Telegram
        if self.tg:
            asyncio.create_task(self.tg.start_async())
            await asyncio.sleep(2)
            await self.tg.send_message(
                f"<b>Бот v8.0 запущен</b>\n"
                f"Баланс: <code>${balance:.2f}</code>\n"
                f"Режим: {'ТЕСТ' if self.controls.dry_run else 'LIVE'}\n"
                f"Стратегия: Тренд + Откат + Ликвидити Свип"
            )

        self._running = True
        cycle = 0

        while self._running and not self._stop_event.is_set():
            try:
                cycle += 1
                logger.info(f"\n{'='*40} CYCLE {cycle} {'='*40}")

                # Update balance
                balance = await self.client.get_balance()
                if balance > 0:
                    self.controls.set_balance(balance)
                    self.profit_lock.set_initial_balance(balance)

                # Phase 1: Manage existing positions
                await self._manage_positions()

                # Phase 1.5: Portfolio Profit Lock check
                if self.position_manager.count() > 0:
                    closed_symbols = await self.profit_lock.check(
                        self.position_manager.all_positions()
                    )
                    if closed_symbols:
                        for sym in closed_symbols:
                            pos = self.position_manager.remove(sym)
                            if pos:
                                self.controls.add_trade(0, sym, pos.side, "profit_lock")
                        logger.info(f"Profit Lock closed {len(closed_symbols)} positions")

                # Phase 2: Scan for new entries (if allowed)
                if self.controls.enabled and not self.controls.emergency:
                    can, reason = self.risk_guard.can_trade()
                    if can:
                        if self.position_manager.count() < self.controls.max_positions:
                            await self._scan_entries()
                        else:
                            logger.info(f"Max positions reached ({self.position_manager.count()})")
                    else:
                        logger.info(f"Trading blocked: {reason}")
                else:
                    logger.info("Bot paused or emergency")

                # Update controls
                self.controls.set_positions(self.position_manager.to_controls_dict())

                logger.info(f"Cycle {cycle} done. Sleeping {self.cycle_sleep}s...\n")
                await asyncio.sleep(self.cycle_sleep)

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Cycle error: {e}", exc_info=True)
                await asyncio.sleep(30)

        # Cleanup
        await self.client.close()
        if self.tg:
            await self.tg.send_message("<b>Бот остановлен</b>")
            try:
                await self.tg.stop_async()
            except Exception:
                pass

    # === Phase 1: Exit management ===

    async def _manage_positions(self):
        """Управление открытыми позициями."""
        if self.position_manager.count() == 0:
            return

        # Sync with exchange
        exchange_positions = await self.client.get_positions()
        exchange_symbols = {p["symbol"] for p in exchange_positions}
        tracked = self.position_manager.symbols()

        # Remove closed positions
        for symbol in tracked:
            if symbol not in exchange_symbols and not self.controls.dry_run:
                pos = self.position_manager.remove(symbol)
                if pos:
                    logger.info(f"Position {symbol} closed on exchange")
                    pnl = 0.0
                    # Try to get PnL from closed
                    try:
                        closed = await self.client.get_closed_pnl(symbol, limit=5)
                        if closed:
                            pnl = float(closed[0].get("closedPnl", 0))
                            self.risk_guard.record_trade(pnl, symbol)
                            self.controls.add_trade(pnl, symbol, pos.side, "exchange_closed")
                    except Exception:
                        pass

                    current_price = await self.client.get_price(symbol)
                    self._save_trade(symbol, pos.side, pos.qty, pos.entry_price, current_price, pnl, "exchange_sl_tp")

                    # Уведомление в Telegram
                    if self.tg:
                        direction = "ЛОНГ" if pos.is_long else "ШОРТ"
                        pnl_sign = "+" if pnl >= 0 else ""
                        result_emoji = "ПРОФИТ" if pnl >= 0 else "УБЫТОК"
                        current_price = await self.client.get_price(symbol)
                        text = (
                            f"<b>СДЕЛКА ЗАКРЫТА (БИРЖА) — {result_emoji}</b>\n\n"
                            f"Монета: <code>{symbol}</code>\n"
                            f"Направление: <b>{direction}</b>\n"
                            f"Вход: <code>${pos.entry_price:.4f}</code>\n"
                            f"Выход: <code>${current_price:.4f}</code>\n\n"
                            f"Результат: <b>{pnl_sign}${pnl:.2f}</b>\n"
                            f"Причина: SL/TP на бирже"
                        )
                        await self.tg.send_message(text)

        # Update unrealized PnL
        total_unrealized = 0.0
        for ep in exchange_positions:
            sym = ep["symbol"]
            unrealized = float(ep.get("unrealisedPnl", 0))
            total_unrealized += unrealized
            pos = self.position_manager.get(sym)
            if pos:
                pos.unrealized_pnl = unrealized
        self.controls.set_unrealized_pnl(total_unrealized)

        # Check exit conditions for each position
        for symbol in self.position_manager.symbols():
            pos = self.position_manager.get(symbol)
            if not pos:
                continue

            current_price = await self.client.get_price(symbol)
            if current_price <= 0:
                continue

            # Get ATR for exit
            klines = await self.client.get_klines(symbol, self.candle_interval, 50)
            atr_val = self.atr.get_atr(symbol, klines)

            # Update trailing stop
            self.exit_engine.update_trailing(pos, current_price)

            # Check exit
            should_exit, reason, details = self.exit_engine.check_exit(pos, current_price, atr_val)

            if should_exit:
                logger.info(f"EXIT {symbol}: {reason.value} - {details}")
                close_result = await self.execution_engine.execute_close(
                    symbol, pos.side, reason=f"{reason.value}: {details}"
                )
                if close_result.get("success"):
                    # Calculate PnL
                    if pos.is_long:
                        pnl = (current_price - pos.entry_price) * pos.qty
                    else:
                        pnl = (pos.entry_price - current_price) * pos.qty
                    self.risk_guard.record_trade(pnl, symbol)
                    self.controls.add_trade(pnl, symbol, pos.side, reason.value)
                    self.position_manager.remove(symbol)
                    self._save_trade(symbol, pos.side, pos.qty, pos.entry_price, current_price, pnl, reason.value)
                    logger.info(f"Closed {symbol}: PnL=${pnl:.2f}")

                    # Уведомление в Telegram с PnL
                    if self.tg:
                        pnl_pct = (pnl / (pos.entry_price * pos.qty)) * 100 if pos.entry_price * pos.qty > 0 else 0
                        direction = "ЛОНГ" if pos.is_long else "ШОРТ"
                        pnl_sign = "+" if pnl >= 0 else ""
                        result_emoji = "ПРОФИТ" if pnl >= 0 else "УБЫТОК"
                        text = (
                            f"<b>СДЕЛКА ЗАКРЫТА — {result_emoji}</b>\n\n"
                            f"Монета: <code>{symbol}</code>\n"
                            f"Направление: <b>{direction}</b>\n"
                            f"Вход: <code>${pos.entry_price:.4f}</code>\n"
                            f"Выход: <code>${current_price:.4f}</code>\n"
                            f"Объём: <code>{pos.qty}</code>\n\n"
                            f"Результат: <b>{pnl_sign}${pnl:.2f}</b> ({pnl_sign}{pnl_pct:.1f}%)\n"
                            f"Причина: {reason.value}"
                        )
                        await self.tg.send_message(text)
            else:
                # Increment bars counter
                pos.bars_since_entry += 1

                # Update SL on exchange if trailing moved it
                if pos.trailing_active and pos.trailing_stop > 0:
                    await self.execution_engine.update_sl(symbol, pos.trailing_stop)

    # === Phase 2: Entry scanning ===

    async def _scan_entries(self):
        """Сканирование символов для новых входов."""
        symbols = await self.get_trade_symbols()
        # Убираем дубликаты, сохраняя порядок
        seen = set()
        unique_symbols = []
        for s in symbols:
            if s not in seen:
                seen.add(s)
                unique_symbols.append(s)
        symbols = unique_symbols

        logger.info(f"Scanning {len(symbols)} symbols...")
        analyzed_count = 0

        for symbol in symbols:
            # Skip if already in position
            if self.position_manager.has(symbol):
                continue

            # Check risk
            can, reason = self.risk_guard.can_trade(symbol)
            if not can:
                continue

            # Max positions check
            if self.position_manager.count() >= self.controls.max_positions:
                break

            try:
                signal = await self._analyze_symbol(symbol)
                analyzed_count += 1
                if signal and signal.should_enter:
                    await self._execute_entry(symbol, signal)
            except Exception as e:
                logger.error(f"Error analyzing {symbol}: {e}")

            # Rate limit: 1.5с между символами
            await asyncio.sleep(1.5)

        logger.info(f"Analyzed {analyzed_count} symbols")

    async def _analyze_symbol(self, symbol: str) -> EntrySignal:
        """
        Полный анализ символа по пайплайну.

        MARKET DATA → MARKET ANALYZER → filters → ENTRY ENGINE → AI filter
        """
        # 1. Get market data
        klines = await self.client.get_klines(symbol, self.candle_interval, self.klines_limit)
        if len(klines) < 60:
            return EntrySignal()

        htf_klines = await self.client.get_klines(symbol, self.htf_interval, self.klines_limit)

        # 2. Market analysis
        market = self.market_analyzer.analyze(klines, htf_klines)
        if not market.can_trade:
            return EntrySignal()

        # 3. ATR check
        atr_val = self.atr.get_atr(symbol, klines)
        atr_pct = self.atr.get_atr_pct(symbol, klines)
        min_atr = self.cfg.get("atr", "min_atr_pct", default=0.50)
        if atr_pct < min_atr:
            return EntrySignal()

        # 4. Liquidity sweep
        sweep = self.sweep_detector.detect_multi_bar(klines)

        # 5. Funding filter
        funding = await self.funding_filter.analyze(self.client, symbol)

        # 6. Liquidation clusters
        current_price = float(klines[-1]["close"])
        highs = [float(k["high"]) for k in klines[-20:]]
        lows = [float(k["low"]) for k in klines[-20:]]
        liq = self.liq_detector.analyze(current_price, highs, lows)

        # 7. Correlation filter
        closes = [float(k["close"]) for k in klines]
        self.correlation_filter.update_prices(symbol, closes)
        open_positions = self.position_manager.symbols()
        corr_blocked, corr_reason = self.correlation_filter.should_filter(symbol, open_positions)
        if corr_blocked:
            logger.info(f"[CORR] {symbol} filtered: {corr_reason}")
            return EntrySignal()

        # 8. Entry Engine
        signal = self.entry_engine.generate_signal(
            symbol=symbol,
            klines=klines,
            market_analysis=market,
            sweep_signal=sweep,
            funding_signal=funding,
            liq_analysis=liq,
            atr_value=atr_val,
        )

        if not signal.should_enter:
            return signal

        # Детальный лог ПОЧЕМУ вход
        current_price = float(klines[-1]["close"])
        logger.info(f"[ENTRY] {symbol}: {signal.side} price=${current_price:.4f} "
                    f"htf={market.htf_trend.name} ltf={market.trend.name} "
                    f"ADX={market.adx:.0f} RSI={market.rsi:.0f} "
                    f"conf={signal.confidence:.0%} reasons={signal.reasons}")

        # 9. Funding filter check
        blocked, fr_reason = self.funding_filter.should_filter_entry(funding, signal.side)
        if blocked:
            logger.info(f"[FUND] {symbol} blocked: {fr_reason}")
            return EntrySignal()

        # 10. AI filter (optional)
        if self.ai_analyzer.enabled:
            ai_data = {
                "price": current_price,
                "regime": market.regime.value,
                "trend": market.trend.name.lower(),
                "htf_trend": market.htf_trend.name.lower(),
                "adx": market.adx,
                "rsi": market.rsi,
                "atr_pct": market.atr_pct,
                "volatility": market.volatility.value,
                "sweep_detected": sweep.detected,
                "sweep_direction": sweep.direction,
                "sweep_strength": sweep.strength,
                "sweep_description": sweep.description,
                "funding_rate": funding.funding_rate,
                "funding_sentiment": funding.sentiment,
                "oi_change": funding.oi_change_pct,
                "liq_magnet": liq.magnet_direction,
                "liq_signal": liq.signal,
                "proposed_signal": signal.side,
                "confluence_score": signal.confidence,
            }
            ai_result = await self.ai_analyzer.analyze(symbol, ai_data)

            if not ai_result.get("should_trade", False):
                logger.info(f"[AI] {symbol} rejected: {ai_result.get('reason', '')}")
                return EntrySignal()

            # AI confidence boost/reduction
            ai_conf = ai_result.get("confidence", 0) / 100
            signal.confidence = (signal.confidence + ai_conf) / 2

        logger.info(f"SIGNAL: {signal.side} {symbol} conf={signal.confidence:.0%} "
                    f"SL=${signal.stop_loss:.4f} TP=${signal.take_profit:.4f} "
                    f"RR={signal.rr_ratio:.1f}")
        return signal

    async def _execute_entry(self, symbol: str, signal: EntrySignal):
        """Исполнение входа."""
        balance = self.controls.get_balance()
        leverage = self.controls.leverage

        # Calculate position size
        qty = self.risk_guard.calculate_position_size(
            balance=balance,
            risk_pct=self.controls.risk_per_trade_pct,
            entry=signal.entry_price,
            stop_loss=signal.stop_loss,
            leverage=leverage,
        )

        # Min check
        min_usdt = self.cfg.get("trading", "min_position_usdt", default=5.0)
        if qty * signal.entry_price < min_usdt:
            logger.info(f"Position too small for {symbol}: ${qty * signal.entry_price:.2f} < ${min_usdt}")
            return

        # ЖЁСТКАЯ проверка маржи
        notional = qty * signal.entry_price
        margin = notional / leverage if leverage > 0 else notional
        max_margin = balance * 0.15  # 15% баланса макс
        if margin > max_margin:
            logger.warning(f"Margin too high for {symbol}: ${margin:.2f} > ${max_margin:.2f} (15% of ${balance:.2f})")
            return

        reason = " | ".join(signal.reasons[:3])
        result = await self.execution_engine.execute_entry(
            symbol=symbol,
            side=signal.side,
            qty=qty,
            stop_loss=signal.stop_loss,
            take_profit=signal.take_profit,
            leverage=leverage,
            reason=reason,
        )

        if result.get("success"):
            # Create position
            pos = Position(
                symbol=symbol,
                side=signal.side,
                entry_price=signal.entry_price,
                qty=result["executed_qty"],
                stop_loss=signal.stop_loss,
                take_profit=signal.take_profit,
            )

            # Initialize exit levels
            klines = await self.client.get_klines(symbol, self.candle_interval, 50)
            atr_val = self.atr.get_atr(symbol, klines)
            self.exit_engine.initialize_position(pos, atr_val)

            self.position_manager.add(pos)
            logger.info(f"ENTERED: {signal.side} {symbol} qty={qty:.6f} price=${signal.entry_price:.4f} "
                        f"margin=${margin:.2f} notional=${notional:.2f}")

    def _save_trade(self, symbol: str, side: str, qty: float, entry: float,
                    exit_price: float, pnl: float, reason: str):
        """Сохранить сделку в trade_history.json."""
        trade = {
            "time": datetime.now(timezone.utc).isoformat(),
            "symbol": symbol,
            "side": side,
            "qty": qty,
            "entry": entry,
            "exit": exit_price,
            "pnl": round(pnl, 2),
            "pnl_pct": round((pnl / (entry * qty)) * 100, 2) if entry * qty > 0 else 0,
            "strategy": "trend_pullback_sweep",
            "reason": reason,
        }
        history_path = BOT_DIR / "trade_history.json"
        try:
            if history_path.exists():
                with open(history_path, "r") as f:
                    history = json.load(f)
            else:
                history = []
            history.append(trade)
            with open(history_path, "w") as f:
                json.dump(history, f, indent=2, ensure_ascii=False)
        except Exception as e:
            logger.error(f"Error saving trade: {e}")

    def stop(self):
        self._running = False
        self._stop_event.set()


async def main():
    # PID lock — защита от двойного запуска
    pid_file = BOT_DIR / "bot.pid"
    if pid_file.exists():
        old_pid = pid_file.read_text().strip()
        try:
            os.kill(int(old_pid), 0)  # Проверяем жив ли процесс
            logger.error(f"Bot already running (PID {old_pid})! Kill it first: kill {old_pid}")
            return
        except (OSError, ValueError):
            pass  # Старый процесс мёртв, продолжаем
    pid_file.write_text(str(os.getpid()))

    bot = TradingBot()

    def handle_signal(sig, frame):
        logger.info("Shutting down...")
        bot.stop()
        pid_file.unlink(missing_ok=True)

    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)

    try:
        await bot.run()
    finally:
        pid_file.unlink(missing_ok=True)


if __name__ == "__main__":
    asyncio.run(main())
