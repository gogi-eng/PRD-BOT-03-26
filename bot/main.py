#!/usr/bin/env python3
"""TRADING BOT v9.0 — AI-fund architecture from the latest specification."""
from __future__ import annotations

import asyncio
import json
import logging
import os
import signal
import sys
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

BOT_DIR = Path(__file__).parent.resolve()
if str(BOT_DIR) not in sys.path:
    sys.path.insert(0, str(BOT_DIR))

from analysis.ai_analyzer import AITradeAnalyzer
from analysis.feature_engineering import FeatureEngineer
from analysis.liquidation_clusters import LiquidationCluster, LiquidationClusterDetector, LiquidationAnalysis
from analysis.market_analyzer import MarketAnalyzer
from analysis.market_regime_ai import MarketRegimeAI
from analysis.orderflow_analyzer import OrderflowAnalyzer
from analysis.transformer_model import TransformerPriceModel
from core.config import BotConfig
from core.live_controls import LiveControls
from core.security import SecureStore
from engine.capital_allocator import MultiSymbolCapitalAllocator
from engine.entry_engine import EntryEngine, EntrySignal
from engine.execution_engine import ExecutionEngine
from engine.exit_engine import ExitEngine
from engine.position_manager import Position, PositionManager
from engine.risk_manager import RiskGuard
from engine.rl_position_agent import RLAction, RLPositionAgent
from exchange.bybit_client import BybitClient
from portfolio_profit_lock import PortfolioProfitLock
from tg.controller import TelegramController
from utils import ATRCalculator

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(message)s", datefmt="%H:%M:%S")
logger = logging.getLogger("BOT")


@dataclass
class BasketProfitState:
    peak_profit_usdt: float = 0.0
    armed: bool = False
    last_reason: str = ""


class TradingBot:
    """Main trading bot orchestrator."""

    def __init__(self):
        load_dotenv(override=True)
        self.cfg = BotConfig.load(str(BOT_DIR / "config.yaml"))
        self.security = SecureStore()

        self.controls = LiveControls(
            enabled=True,
            dry_run=False,
            leverage=self.cfg.get("trading", "leverage", default=5),
            margin_total_pct=self.cfg.get("trading", "margin_total_pct", default=8.0),
            risk_per_trade_pct=self.cfg.get("trading", "risk_per_trade_pct", default=0.5),
            tp_pct=self.cfg.get("trading", "tp_pct", default=1.8),
            sl_pct=self.cfg.get("trading", "sl_pct", default=1.0),
            max_positions=self.cfg.get("trading", "max_positions", default=3),
            trailing_stop_pct=self.cfg.get("trading", "trailing_stop_pct", default=1.2),
            ai_enabled=self.cfg.get("ai", "enabled", default=True),
            rl_enabled=self.cfg.get("rl", "enabled", default=True),
        )

        api_key = self.security.get_key("BYBIT_API_KEY")
        api_secret = self.security.get_key("BYBIT_API_SECRET")
        testnet = self.cfg.get("bybit", "testnet", default=False)
        category = self.cfg.get("bybit", "category", default="linear")
        self.client = BybitClient(api_key, api_secret, testnet=testnet, category=category)

        self.risk_guard = RiskGuard(
            max_consecutive_losses=self.cfg.get("risk", "max_consecutive_losses", default=2),
            max_daily_loss_pct=self.cfg.get("risk", "max_daily_loss_pct", default=2.5),
            max_daily_loss_usdt=self.cfg.get("risk", "max_daily_loss_usdt", default=10),
            max_trades_per_day=self.cfg.get("risk", "max_trades_per_day", default=10),
            max_positions=self.cfg.get("trading", "max_positions", default=3),
            max_trades_per_symbol_24h=self.cfg.get("risk", "max_trades_per_symbol_24h", default=2),
            cooldown_after_loss_sec=self.cfg.get("risk", "cooldown_after_loss_sec", default=900),
            cooldown_after_stop_hours=self.cfg.get("risk", "cooldown_after_stop_hours", default=6),
            reduce_after_losses=self.cfg.get("risk", "reduce_after_losses", default=1),
            reduction_factor=self.cfg.get("risk", "reduction_factor", default=0.5),
        )
        self.controls.set_guard(self.risk_guard)

        self.market_analyzer = MarketAnalyzer(atr_period=self.cfg.get("atr", "period", default=14))
        self.regime_ai = MarketRegimeAI()
        self.orderflow_analyzer = OrderflowAnalyzer()
        self.liq_detector = LiquidationClusterDetector(
            cluster_step=self.cfg.get("heatmap", "cluster_step", default=20),
            max_levels=self.cfg.get("heatmap", "max_levels", default=10),
        )
        self.feature_engineer = FeatureEngineer(sequence_length=self.cfg.get("bot", "feature_window", default=128))
        self.transformer_model = TransformerPriceModel(sequence_length=self.cfg.get("bot", "feature_window", default=128))
        self.ai_analyzer = AITradeAnalyzer()
        self.ai_analyzer.min_confidence = self.cfg.get("ai", "min_confidence", default=60)
        self.ai_analyzer.fail_open = self.cfg.get("ai", "fail_open", default=True)
        self.atr = ATRCalculator(period=self.cfg.get("atr", "period", default=14))

        self.entry_engine = EntryEngine(self.cfg)
        self.allocator = MultiSymbolCapitalAllocator()
        self.position_manager = PositionManager()
        self.rl_agent = RLPositionAgent(
            add_threshold=self.cfg.get("rl", "add_threshold", default=0.78),
            reduce_threshold=self.cfg.get("rl", "reduce_threshold", default=0.7),
            close_threshold=self.cfg.get("rl", "close_threshold", default=0.8),
        )
        self.exit_engine = ExitEngine(
            hard_sl_atr_mult=self.cfg.get("exit", "hard_sl_atr_mult", default=1.8),
            early_exit_bars=self.cfg.get("exit", "early_exit_bars", default=8),
            early_exit_min_profit_atr=self.cfg.get("exit", "early_exit_min_profit_atr", default=0.35),
            trailing_activation_atr=self.cfg.get("exit", "trailing_activation_atr", default=0.8),
            trailing_distance_atr=self.cfg.get("exit", "trailing_distance_atr", default=1.2),
            tp_cap_atr_mult=self.cfg.get("exit", "tp_cap_atr_mult", default=8.0),
        )

        self.tg = None
        tg_token = self.security.get_key("TELEGRAM_TOKEN")
        tg_chat_id = self.security.get_key("TELEGRAM_CHAT_ID")
        if tg_token:
            self.tg = TelegramController(token=tg_token, controls=self.controls, allowed_chat_id=int(tg_chat_id) if tg_chat_id else None)
            self.risk_guard.set_notify_callback(self._notify_tg)

        self.execution_engine = ExecutionEngine(self.client, self.controls, self.tg)
        self.profit_lock = PortfolioProfitLock(
            client=self.client,
            tg=self.tg,
            min_profit_pct=self.cfg.get("profit_lock", "min_profit_pct", default=5.0),
            decline_threshold_pct=self.cfg.get("profit_lock", "decline_threshold_pct", default=20.0),
            decline_duration_sec=self.cfg.get("profit_lock", "decline_duration_sec", default=300.0),
            cooldown_sec=self.cfg.get("profit_lock", "cooldown_sec", default=3600.0),
            dry_run=self.controls.dry_run,
        )
        if self.tg:
            self.tg.set_profit_lock(self.profit_lock)

        self._running = False
        self._stop_event = threading.Event()
        self.candle_interval = self.cfg.get("bot", "candle_interval", default="1")
        self.htf_interval = self.cfg.get("bot", "htf_interval", default="15")
        self.cycle_sleep = self.cfg.get("bot", "cycle_sleep_sec", default=45)
        self.feature_window = self.cfg.get("bot", "feature_window", default=128)
        self.klines_limit = max(self.cfg.get("bot", "klines_limit", default=180), self.feature_window)
        self.min_volume = self.cfg.get("market", "min_24h_volume_usdt", default=15_000_000)
        self.max_symbols = self.cfg.get("market", "max_symbols", default=15)
        self.trade_symbols = self.cfg.get("market", "trade_symbols", default=5)
        self.whitelist_enabled = self.cfg.get("market", "whitelist_enabled", default=True)
        self.whitelist = self.cfg.get("market", "whitelist_symbols", default=[])
        self.blacklist = self.cfg.get("trading", "blacklist_symbols", default=[])
        self.blacklist_substrings = self.cfg.get("market", "blacklist_substrings", default=[])
        self.min_position_usdt = self.cfg.get("trading", "min_position_usdt", default=5.0)
        self.min_atr_pct = self.cfg.get("atr", "min_atr_pct", default=0.25)
        self.max_stream_symbols = self.cfg.get("bot", "liquidation_stream_symbols", default=12)
        self.max_rl_adds = 1
        self.adopt_all_positions = self.cfg.get("position_sync", "adopt_all_positions", default=True)
        self.preserve_existing_sl_tp = self.cfg.get("position_sync", "preserve_existing_sl_tp", default=True)
        self.partial_tp_enabled = self.cfg.get("partial_tp", "enabled", default=True)
        self.partial_tp_trigger_progress = self.cfg.get("partial_tp", "trigger_progress", default=0.5)
        self.partial_tp_close_fraction = self.cfg.get("partial_tp", "close_fraction", default=0.5)
        self.partial_tp_move_stop_to_entry = self.cfg.get("partial_tp", "move_stop_to_entry", default=True)
        self.portfolio_tp_enabled = self.cfg.get("portfolio_tp", "enabled", default=True)
        self.portfolio_tp_target_pct = self.cfg.get("portfolio_tp", "target_profit_pct", default=2.0)
        self.basket_profit_guard_enabled = self.cfg.get("basket_profit_guard", "enabled", default=True)
        self.basket_profit_min_positions = self.cfg.get("basket_profit_guard", "min_positions", default=2)
        self.basket_profit_require_negative = self.cfg.get("basket_profit_guard", "require_negative_position", default=True)
        self.basket_profit_drawdown_pct = self.cfg.get("basket_profit_guard", "drawdown_pct_from_peak", default=12.0)
        self.basket_profit_min_total_usdt = self.cfg.get("basket_profit_guard", "min_total_profit_usdt", default=0.5)
        self.profit_drawdown_guard_enabled = self.cfg.get("profit_drawdown_guard", "enabled", default=True)
        self.profit_drawdown_activation_pct = self.cfg.get("profit_drawdown_guard", "activation_profit_pct", default=3.0)
        self.profit_drawdown_retrace_pct = self.cfg.get("profit_drawdown_guard", "retrace_from_peak_pct", default=25.0)
        self.manual_rl_enabled = self.cfg.get("manual_management", "rl_enabled", default=False)
        self.manual_preserve_existing_tp = self.cfg.get("manual_management", "preserve_existing_tp", default=True)
        self.manual_trailing_activation_atr = self.cfg.get("manual_management", "trailing_activation_atr", default=1.6)
        self.manual_trailing_distance_atr = self.cfg.get("manual_management", "trailing_distance_atr", default=2.4)
        self.manual_notify_on_adopt = self.cfg.get("manual_management", "notify_on_adopt", default=True)
        self.manual_notify_on_partial_tp = self.cfg.get("manual_management", "notify_on_partial_tp", default=True)
        self.manual_notify_on_sl_move = self.cfg.get("manual_management", "notify_on_sl_move", default=True)
        self.basket_profit_state = BasketProfitState()

    async def _notify_tg(self, message: str):
        if self.tg:
            await self.tg.send_alert(message)

    async def get_trade_symbols(self) -> list:
        try:
            tickers = await self.client.get_tickers()
        except Exception as exc:
            logger.error(f"Failed to get tickers: {exc}")
            return self.whitelist[: self.trade_symbols] if self.whitelist else []

        ranked = []
        for ticker in tickers:
            symbol = ticker.get("symbol", "")
            if not symbol.endswith("USDT"):
                continue
            if symbol in self.blacklist:
                continue
            if any(part in symbol for part in self.blacklist_substrings):
                continue
            turnover = float(ticker.get("turnover24h", 0) or 0)
            if turnover < self.min_volume:
                continue
            ranked.append((symbol, turnover))
        ranked.sort(key=lambda item: item[1], reverse=True)
        symbols = [item[0] for item in ranked[: self.max_symbols]]
        if self.whitelist_enabled:
            ordered = [symbol for symbol in self.whitelist if symbol not in self.blacklist]
            for symbol in reversed(ordered):
                if symbol in symbols:
                    symbols.remove(symbol)
                symbols.insert(0, symbol)
        unique = []
        seen = set()
        for symbol in symbols:
            if symbol not in seen:
                unique.append(symbol)
                seen.add(symbol)
        return unique[: self.trade_symbols]

    async def run(self):
        logger.info("=" * 72)
        logger.info("TRADING BOT v9.0 — AI FUND ARCHITECTURE")
        logger.info("DATA → FEATURES → TRANSFORMER → ENTRY → RL → EXECUTION")
        logger.info("=" * 72)

        ok, err = self.security.validate_bybit_keys()
        if not ok:
            logger.error(f"Bybit keys: {err}")
            return

        balance = await self.client.get_balance()
        if balance <= 0:
            logger.error("Zero balance!")
            return
        self.controls.set_balance(balance)
        self.risk_guard.initial_balance = balance
        self.profit_lock.set_initial_balance(balance)
        logger.info(f"Balance: ${balance:.2f}")

        if self.tg:
            asyncio.create_task(self.tg.start_async())
            await asyncio.sleep(2)
            await self.tg.send_message(
                f"<b>Бот v9.0 запущен</b>\n"
                f"Баланс: <code>${balance:.2f}</code>\n"
                f"Режим: {'ТЕСТ' if self.controls.dry_run else 'LIVE'}\n"
                f"Стратегия: Transformer + Heatmap + Orderflow"
            )

        self._running = True
        cycle = 0
        while self._running and not self._stop_event.is_set():
            try:
                cycle += 1
                logger.info(f"\n{'=' * 36} CYCLE {cycle} {'=' * 36}")
                balance = await self.client.get_balance()
                if balance > 0:
                    self.controls.set_balance(balance)
                    if self.risk_guard.initial_balance <= 0:
                        self.risk_guard.initial_balance = balance
                    self.profit_lock.set_initial_balance(balance)

                exchange_positions = await self.client.get_positions()
                exchange_symbols = [item["symbol"] for item in exchange_positions]
                symbols = await self.get_trade_symbols()
                subscribed = self._unique_symbols(exchange_symbols + self.position_manager.symbols() + symbols)[: self.max_stream_symbols]
                await self.client.set_liquidation_symbols(subscribed)

                total_unrealized = await self._manage_positions(exchange_positions)

                if self.basket_profit_guard_enabled and self.position_manager.count() >= self.basket_profit_min_positions:
                    await self._check_basket_profit_guard(total_unrealized)

                if self.portfolio_tp_enabled and self.position_manager.count() >= 2:
                    await self._check_portfolio_take_profit(total_unrealized)

                if self.position_manager.count() > 0:
                    closed_symbols = await self.profit_lock.check(self.position_manager.all_positions()) or []
                    for symbol in closed_symbols:
                        pos = self.position_manager.get(symbol)
                        if pos:
                            current_price = await self.client.get_price(symbol)
                            await self._finalize_full_close(symbol, pos, current_price, 0.0, "profit_lock")

                if self.controls.enabled and not self.controls.emergency:
                    can_trade, reason = self.risk_guard.can_trade()
                    if can_trade and self.position_manager.count() < self.controls.max_positions:
                        await self._scan_entries(symbols)
                    elif not can_trade:
                        logger.info(f"Trading blocked: {reason}")
                else:
                    logger.info("Bot paused or emergency")

                self.controls.set_positions(self.position_manager.to_controls_dict())
                logger.info(f"Cycle {cycle} done. Sleeping {self.cycle_sleep}s...")
                await asyncio.sleep(self.cycle_sleep)
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.error(f"Cycle error: {exc}", exc_info=True)
                await asyncio.sleep(20)

        await self.client.close()
        if self.tg:
            await self.tg.send_message("<b>Бот остановлен</b>")
            try:
                await self.tg.stop_async()
            except Exception:
                pass

    async def _manage_positions(self, exchange_positions: list | None = None) -> float:
        exchange_positions = exchange_positions if exchange_positions is not None else await self.client.get_positions()
        if exchange_positions:
            exchange_symbols = {item["symbol"] for item in exchange_positions}
            for symbol in self.position_manager.symbols():
                if symbol not in exchange_symbols and not self.controls.dry_run:
                    pos = self.position_manager.remove(symbol)
                    if pos:
                        current_price = await self.client.get_price(symbol)
                        pnl = 0.0
                        closed = await self.client.get_closed_pnl(symbol, limit=3)
                        if closed:
                            pnl = float(closed[0].get("closedPnl", 0) or 0)
                        await self._finalize_full_close(symbol, pos, current_price, pnl, "exchange_closed", already_removed=True)

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
            atr_val = self.atr.get_atr(symbol, klines)
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
                state = {
                    "trend_bias": market.htf_trend.value if market.htf_trend.value != 0 else market.trend.value,
                    "volatility": market.atr_pct / 100,
                    "pnl_pct": pnl_pct,
                    "liq_signal": liq.signal,
                    "orderflow_edge": orderflow.imbalance_score,
                    "transformer_edge": transformer.prob_up - transformer.prob_down,
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

            self.exit_engine.update_trailing(pos, current_price)
            should_exit, reason, details = self.exit_engine.check_exit(
                pos,
                current_price,
                atr_val,
                protective_level=pos.protective_liq_level,
                allow_early_exit=(pos.origin == "bot"),
            )
            if should_exit:
                close_result = await self.execution_engine.execute_close(symbol, pos.side, reason=f"{reason.value}: {details}", position_idx=pos.position_idx)
                if close_result.get("success"):
                    pnl = self._calc_pnl(pos, current_price, pos.qty)
                    await self._finalize_full_close(symbol, pos, current_price, pnl, reason.value)
            else:
                pos.bars_since_entry += 1
                if pos.trailing_active and pos.trailing_stop > 0:
                    updated = await self.execution_engine.update_sl(symbol, pos.trailing_stop, position_idx=pos.position_idx)
                    if updated and pos.origin == "manual":
                        pos.stop_loss = pos.trailing_stop
                        await self._notify_manual_sl_move(pos, "trailing")

        return total_unrealized

    async def _scan_entries(self, symbols: list):
        candidates = []
        for symbol in symbols:
            if self.position_manager.has(symbol):
                continue
            allowed, _ = self.risk_guard.can_trade(symbol)
            if not allowed:
                continue
            try:
                signal = await self._analyze_symbol(symbol)
                if signal.should_enter:
                    candidates.append(
                        {
                            "symbol": symbol,
                            "signal": signal,
                            "signal_strength": signal.capital_score or signal.confidence,
                            "liquidity": signal.metadata.get("liquidity", 0.0),
                            "volatility": signal.metadata.get("volatility", 0.0),
                            "spread": signal.metadata.get("spread_pct", 0.0),
                        }
                    )
            except Exception as exc:
                logger.error(f"Error analyzing {symbol}: {exc}")
            await asyncio.sleep(0.8)

        ranked = self.allocator.allocate(candidates)
        self.controls.set_candidates(ranked)
        available_slots = max(0, self.controls.max_positions - self.position_manager.count())
        for item in ranked[:available_slots]:
            await self._execute_entry(item["symbol"], item["signal"], item.get("capital_weight", 1.0))

    async def _analyze_symbol(self, symbol: str) -> EntrySignal:
        klines = await self.client.get_klines(symbol, self.candle_interval, self.klines_limit)
        if len(klines) < 80:
            return EntrySignal()
        htf_klines = await self.client.get_klines(symbol, self.htf_interval, max(80, self.feature_window))
        market = self.market_analyzer.analyze(klines, htf_klines)
        if not market.can_trade:
            return EntrySignal()

        atr_val = self.atr.get_atr(symbol, klines)
        atr_pct = self.atr.get_atr_pct(symbol, klines)
        if atr_pct < self.min_atr_pct:
            return EntrySignal()

        current_price = float(klines[-1]["close"])
        orderbook = await self.client.get_orderbook(symbol, limit=25)
        trades = await self.client.get_recent_trades(symbol, limit=120)
        orderflow = self.orderflow_analyzer.analyze(orderbook, trades)
        liq = self._resolve_liquidation_context(symbol, current_price, klines)
        if liq.target_level <= 0:
            liq = self._build_directional_liq_fallback(current_price, market, orderflow, atr_val)
        self.controls.set_heatmap(symbol, liq)
        if liq.target_level <= 0:
            return EntrySignal()

        regime = self.regime_ai.classify(market)
        features = self.feature_engineer.build(klines, orderflow, liq, atr_val)
        transformer = self.transformer_model.predict(features, regime, orderflow, liq)
        signal = self.entry_engine.generate_signal(symbol, current_price, market, regime, transformer, orderflow, liq, atr_val)
        if not signal.should_enter:
            return signal

        liquidity = sum(float(item.get("volume", 0.0)) for item in klines[-30:]) * current_price
        signal.metadata.update({
            "liquidity": liquidity,
            "volatility": market.atr_pct / 100,
            "adx": market.adx,
            "trend": market.trend.name.lower(),
            "htf_trend": market.htf_trend.name.lower(),
            "atr_pct": market.atr_pct,
        })

        if self.ai_analyzer.enabled and self.controls.ai_enabled:
            ai_result = await self.ai_analyzer.analyze(symbol, self._build_ai_payload(current_price, market, signal))
            if not ai_result.get("should_trade", False):
                logger.info(f"[AI] {symbol} rejected: {ai_result.get('reason', '')}")
                return EntrySignal()
            signal.confidence = round((signal.confidence + ai_result.get("confidence", 0) / 100) / 2, 4)
            signal.capital_score = round(signal.confidence * signal.rr_ratio, 4)

        logger.info(
            f"SIGNAL {symbol}: {signal.side} conf={signal.confidence:.0%} "
            f"p_up={signal.metadata.get('transformer_prob_up', 0):.2f} p_down={signal.metadata.get('transformer_prob_down', 0):.2f} "
            f"liq={signal.metadata.get('target_level', 0):.4f} dist={signal.metadata.get('liq_distance_pct', 0):.3f}%"
        )
        return signal

    def _build_ai_payload(self, current_price: float, market, signal: EntrySignal) -> dict:
        return {
            "price": current_price,
            "regime": signal.metadata.get("regime", market.regime.value),
            "trend": signal.metadata.get("trend", market.trend.name.lower()),
            "htf_trend": signal.metadata.get("htf_trend", market.htf_trend.name.lower()),
            "adx": signal.metadata.get("adx", market.adx),
            "atr_pct": signal.metadata.get("atr_pct", market.atr_pct),
            "volatility": market.volatility.value,
            "transformer_prob_up": signal.metadata.get("transformer_prob_up", 0.0),
            "transformer_prob_down": signal.metadata.get("transformer_prob_down", 0.0),
            "transformer_prob_flat": signal.metadata.get("transformer_prob_flat", 0.0),
            "orderflow_bullish_ratio": signal.metadata.get("orderflow_bullish_ratio", 1.0),
            "orderflow_bearish_ratio": signal.metadata.get("orderflow_bearish_ratio", 1.0),
            "spread_pct": signal.metadata.get("spread_pct", 0.0),
            "liq_magnet": signal.metadata.get("liq_magnet", "neutral"),
            "liq_signal": signal.metadata.get("liq_signal", 0),
            "liq_target": signal.metadata.get("target_level", 0.0),
            "liq_distance_pct": signal.metadata.get("liq_distance_pct", 0.0),
            "proposed_signal": signal.side,
            "confluence_score": signal.confidence,
        }

    async def _execute_entry(self, symbol: str, signal: EntrySignal, capital_weight: float):
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
        if result.get("success"):
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
                partial_tp_price=self._compute_partial_tp_price(executed_price, signal.take_profit, signal.side),
                partial_close_fraction=self.partial_tp_close_fraction,
                total_tp_price=signal.take_profit,
            )
            klines = await self.client.get_klines(symbol, self.candle_interval, 50)
            atr_val = self.atr.get_atr(symbol, klines)
            self.exit_engine.initialize_position(pos, atr_val, protective_liq_level=pos.protective_liq_level)
            self._apply_profit_drawdown_profile(pos)
            self.position_manager.add(pos)
            logger.info(f"ENTERED {symbol}: {signal.side} qty={pos.qty:.6f} entry=${executed_price:.4f} weight={capital_weight:.2f}")

    async def _finalize_full_close(self, symbol: str, pos: Position, exit_price: float, pnl: float, reason: str, already_removed: bool = False):
        if not already_removed:
            self.position_manager.remove(symbol)
        self.risk_guard.record_trade(pnl, symbol)
        self.controls.add_trade(pnl, symbol, pos.side, reason)
        self._save_trade(symbol, pos.side, pos.qty, pos.entry_price, exit_price, pnl, reason)
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
        self.risk_guard.record_trade(pnl, symbol)
        self.controls.add_trade(pnl, symbol, pos.side, reason)
        self._save_trade(symbol, pos.side, qty, pos.entry_price, exit_price, pnl, reason)
        self.position_manager.reduce(symbol, qty)
        logger.info(f"REDUCED {symbol}: qty={qty:.6f} pnl=${pnl:.2f} reason={reason}")

    def _calc_pnl(self, pos: Position, exit_price: float, qty: float) -> float:
        if pos.is_long:
            return (exit_price - pos.entry_price) * qty
        return (pos.entry_price - exit_price) * qty

    def _calc_pnl_pct(self, pos: Position, price: float) -> float:
        if pos.entry_price <= 0:
            return 0.0
        if pos.is_long:
            return (price - pos.entry_price) / pos.entry_price * 100
        return (pos.entry_price - price) / pos.entry_price * 100

    def _compute_partial_tp_price(self, entry: float, total_tp: float, side: str) -> float:
        if entry <= 0 or total_tp <= 0:
            return 0.0
        progress = max(0.05, min(self.partial_tp_trigger_progress, 0.95))
        if side.upper() in ["BUY", "LONG"]:
            return entry + (total_tp - entry) * progress if total_tp > entry else 0.0
        return entry - (entry - total_tp) * progress if total_tp < entry else 0.0

    def _unique_symbols(self, symbols: list[str]) -> list[str]:
        unique = []
        seen = set()
        for symbol in symbols:
            if symbol and symbol not in seen:
                unique.append(symbol)
                seen.add(symbol)
        return unique

    def _resolve_liquidation_context(self, symbol: str, current_price: float, klines: list[dict]):
        liq = self.liq_detector.analyze(current_price, self.client.get_liquidation_events(symbol))
        if liq.target_level > 0:
            return liq
        synthetic_events = self._build_synthetic_liquidation_events(klines, current_price)
        fallback = self.liq_detector.analyze(current_price, synthetic_events)
        if fallback.target_level > 0:
            logger.info(f"[HEATMAP] {symbol}: using synthetic price-action fallback")
        return fallback

    def _build_synthetic_liquidation_events(self, klines: list[dict], current_price: float) -> list[dict]:
        events = []
        window = klines[-36:]
        for candle in window:
            high = float(candle.get("high", 0.0))
            low = float(candle.get("low", 0.0))
            close = float(candle.get("close", current_price) or current_price)
            volume = float(candle.get("volume", 0.0))
            weight = max(volume * close, 1.0)
            if high > current_price:
                events.append({"price": high, "size": weight, "side": "Sell"})
            if low < current_price:
                events.append({"price": low, "size": weight, "side": "Buy"})
        return events

    def _build_directional_liq_fallback(self, current_price: float, market, orderflow, atr_val: float) -> LiquidationAnalysis:
        bullish_votes = 0
        bearish_votes = 0
        if market.trend.value > 0:
            bullish_votes += 1
        elif market.trend.value < 0:
            bearish_votes += 1
        if market.htf_trend.value > 0:
            bullish_votes += 1
        elif market.htf_trend.value < 0:
            bearish_votes += 1
        if orderflow.bullish_ratio >= 1.03 and orderflow.bullish_ratio >= orderflow.bearish_ratio:
            bullish_votes += 1
        if orderflow.bearish_ratio >= 1.03 and orderflow.bearish_ratio > orderflow.bullish_ratio:
            bearish_votes += 1

        if bullish_votes == bearish_votes or current_price <= 0:
            return LiquidationAnalysis([], [], None, None, 0.0, 0.0, "neutral", 0, 0.0)

        atr = atr_val if atr_val > 0 else current_price * 0.008
        distance = max(atr * 1.8, current_price * 0.004)
        if bullish_votes > bearish_votes:
            target_level = current_price + distance
            distance_pct = distance / current_price * 100
            cluster = LiquidationCluster(round(target_level, 8), 1.0, 1, round(distance_pct, 4), "shorts")
            logger.info("[HEATMAP] directional fallback: bullish target created")
            return LiquidationAnalysis([cluster], [], cluster, None, cluster.level, cluster.size, "up", 1, cluster.distance_pct)

        target_level = max(current_price - distance, 0.0)
        distance_pct = distance / current_price * 100
        cluster = LiquidationCluster(round(target_level, 8), 1.0, 1, round(distance_pct, 4), "longs")
        logger.info("[HEATMAP] directional fallback: bearish target created")
        return LiquidationAnalysis([], [cluster], None, cluster, cluster.level, cluster.size, "down", -1, cluster.distance_pct)

    async def _sync_exchange_position(self, exchange_position: dict):
        symbol = exchange_position.get("symbol", "")
        if not symbol:
            return
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
        derived_sl, derived_tp = self._derive_manual_position_levels(side, entry_price or mark_price, stop_loss, take_profit, atr_val)
        stop_loss = stop_loss if stop_loss > 0 and self.preserve_existing_sl_tp else derived_sl
        take_profit = take_profit if take_profit > 0 and self.preserve_existing_sl_tp else derived_tp

        external_tp_locked = bool(take_profit > 0 and self.manual_preserve_existing_tp)
        adopted = Position(
            symbol=symbol,
            side=side,
            entry_price=entry_price or mark_price,
            qty=size,
            stop_loss=stop_loss,
            take_profit=take_profit,
            unrealized_pnl=float(exchange_position.get("unrealisedPnl", 0) or 0),
            origin="manual",
            partial_tp_price=0.0 if external_tp_locked else self._compute_partial_tp_price(entry_price or mark_price, take_profit, side),
            partial_close_fraction=self.partial_tp_close_fraction,
            total_tp_price=take_profit,
            position_idx=position_idx,
            external_tp_locked=external_tp_locked,
            last_notified_stop_loss=stop_loss,
        )
        self.exit_engine.initialize_position(adopted, atr_val, protective_liq_level=stop_loss)
        self._apply_manual_trailing_profile(adopted, atr_val)
        self._apply_profit_drawdown_profile(adopted)
        self.position_manager.add(adopted)

        if not self.controls.dry_run:
            if float(exchange_position.get("stopLoss", 0) or 0) <= 0 and stop_loss > 0:
                await self.execution_engine.update_sl(symbol, stop_loss, position_idx=position_idx)
            if float(exchange_position.get("takeProfit", 0) or 0) <= 0 and take_profit > 0:
                await self.execution_engine.update_tp(symbol, take_profit, position_idx=position_idx)
        if self.tg and self.manual_notify_on_adopt:
            await self.tg.send_message(
                f"<b>ПОДХВАЧЕНА ВНЕШНЯЯ ПОЗИЦИЯ</b>\n\n"
                f"Монета: <code>{symbol}</code>\n"
                f"Сторона: <b>{side}</b>\n"
                f"Вход: <code>${adopted.entry_price:.4f}</code>\n"
                f"Объём: <code>{size}</code>\n"
                f"SL: <code>${adopted.stop_loss:.4f}</code>\n"
                f"TP: <code>${adopted.take_profit:.4f}</code>\n"
                f"Режим: <code>manual-safe-trailing</code>"
            )

    def _derive_manual_position_levels(self, side: str, entry_price: float, stop_loss: float, take_profit: float, atr_val: float) -> tuple[float, float]:
        atr = atr_val if atr_val > 0 else entry_price * 0.01
        side_upper = side.upper()
        derived_sl = stop_loss
        if derived_sl <= 0:
            derived_sl = entry_price - atr * self.exit_engine.hard_sl_atr_mult if side_upper in ["BUY", "LONG"] else entry_price + atr * self.exit_engine.hard_sl_atr_mult
        derived_tp = take_profit
        if derived_tp <= 0:
            risk = abs(entry_price - derived_sl) if derived_sl > 0 else atr * self.exit_engine.hard_sl_atr_mult
            rr = self.entry_engine.min_rr_ratio
            derived_tp = entry_price + risk * rr if side_upper in ["BUY", "LONG"] else entry_price - risk * rr
        return derived_sl, derived_tp

    def _apply_manual_trailing_profile(self, pos: Position, atr_val: float):
        atr = atr_val if atr_val > 0 else pos.entry_price * 0.01
        pos.trailing_distance = atr * self.manual_trailing_distance_atr
        if pos.is_long:
            pos.trailing_activation_price = pos.entry_price + atr * self.manual_trailing_activation_atr
        else:
            pos.trailing_activation_price = pos.entry_price - atr * self.manual_trailing_activation_atr

    def _apply_profit_drawdown_profile(self, pos: Position):
        activation_move = self.profit_drawdown_activation_pct / 100
        if pos.is_long:
            target_activation = pos.entry_price * (1 + activation_move)
            pos.trailing_activation_price = max(pos.trailing_activation_price, target_activation)
        else:
            target_activation = pos.entry_price * (1 - activation_move)
            if pos.trailing_activation_price <= 0:
                pos.trailing_activation_price = target_activation
            else:
                pos.trailing_activation_price = min(pos.trailing_activation_price, target_activation)
        pos.profit_guard_armed = False
        pos.profit_peak_price = pos.entry_price
        pos.profit_peak_pct = 0.0

    async def _check_profit_drawdown_guard(self, pos: Position, current_price: float) -> tuple[bool, str]:
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
                )
            return False, ""

        if current_profit_pct > pos.profit_peak_pct:
            pos.profit_peak_pct = current_profit_pct
            pos.profit_peak_price = current_price
            return False, ""

        trigger_profit_pct = pos.profit_peak_pct * (1 - self.profit_drawdown_retrace_pct / 100)
        if current_profit_pct <= trigger_profit_pct and current_profit_pct > 0:
            return True, (
                f"profit_drawdown_guard: peak={pos.profit_peak_pct:.2f}% current={current_profit_pct:.2f}% "
                f"retrace={self.profit_drawdown_retrace_pct:.0f}%"
            )
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
        await self._finalize_partial_close(pos.symbol, pos, current_price, close_qty, "partial_tp_50pct")
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

        any_negative = any(pos.unrealized_pnl < 0 for pos in positions.values())
        if total_unrealized > self.basket_profit_state.peak_profit_usdt:
            self.basket_profit_state.peak_profit_usdt = total_unrealized

        if total_unrealized < self.basket_profit_min_total_usdt:
            return
        if self.basket_profit_require_negative and not any_negative:
            return

        self.basket_profit_state.armed = True
        self.basket_profit_state.last_reason = "negative_position_in_basket"
        peak = self.basket_profit_state.peak_profit_usdt
        if peak <= 0:
            return
        drawdown_pct = ((peak - total_unrealized) / peak) * 100 if peak > 0 else 0.0
        if drawdown_pct + 1e-9 < self.basket_profit_drawdown_pct:
            return

        logger.info(
            f"BASKET PROFIT GUARD HIT: total=${total_unrealized:.2f}, peak=${peak:.2f}, drawdown={drawdown_pct:.1f}%"
        )
        if self.tg:
            await self.tg.send_message(
                f"<b>BASKET PROFIT GUARD</b>\n\n"
                f"Открыто позиций: <code>{len(positions)}</code>\n"
                f"Пик суммарной прибыли: <code>${peak:.2f}</code>\n"
                f"Текущая прибыль: <code>${total_unrealized:.2f}</code>\n"
                f"Откат от пика: <code>{drawdown_pct:.1f}%</code>\n"
                f"Причина: одна из позиций ушла в минус"
            )
        for symbol in list(self.position_manager.symbols()):
            pos = self.position_manager.get(symbol)
            if not pos:
                continue
            current_price = await self.client.get_price(symbol)
            close_result = await self.execution_engine.execute_close(symbol, pos.side, reason="basket_profit_guard", position_idx=pos.position_idx)
            if close_result.get("success"):
                pnl = self._calc_pnl(pos, current_price, pos.qty)
                await self._finalize_full_close(symbol, pos, current_price, pnl, "basket_profit_guard")
        self._reset_basket_profit_state()

    def _save_trade(self, symbol: str, side: str, qty: float, entry: float, exit_price: float, pnl: float, reason: str):
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
        }
        history_path = BOT_DIR / "trade_history.json"
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
