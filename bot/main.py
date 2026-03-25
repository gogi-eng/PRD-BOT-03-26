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
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
import yaml

from dotenv import load_dotenv

BOT_DIR = Path(__file__).parent.resolve()
if str(BOT_DIR) not in sys.path:
    sys.path.insert(0, str(BOT_DIR))

from analysis.ai_analyzer import AITradeAnalyzer
from analysis.correlation_filter import CorrelationFilter
from analysis.feature_engineering import FeatureEngineer
from analysis.liquidation_clusters import LiquidationCluster, LiquidationClusterDetector, LiquidationAnalysis
from analysis.liquidity_heatmap import LiquidityHeatmap
from analysis.market_analyzer import MarketAnalyzer
from analysis.market_regime_ai import MarketRegimeAI
from analysis.market_structure import MarketStructureEngine
from analysis.orderflow_analyzer import OrderflowAnalyzer
from analysis.structure_zones import StructureZoneAnalyzer
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
from engine.signal_feedback_loop import SignalFeedbackLoop
from engine.symbol_quality_filter import SymbolQualityFilter
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
    total_history: dict = None
    symbol_pnl_history: dict = None
    drawdown_detected_at: float = 0.0  # timestamp when drawdown first detected

    def __post_init__(self):
        if self.total_history is None:
            self.total_history = []
        if self.symbol_pnl_history is None:
            self.symbol_pnl_history = {}


class TradingBot:
    """Main trading bot orchestrator."""

    def __init__(self):
        load_dotenv(override=True)
        self.cfg = BotConfig.load(str(BOT_DIR / "config.yaml"))
        self.security = SecureStore()

        self.controls = LiveControls(
            enabled=True,
            dry_run=False,
            signal_only=self.cfg.get("bot", "signal_only", default=False),
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
            min_loss_usdt_for_cooldown=self.cfg.get("risk", "min_loss_usdt_for_cooldown", default=0.25),
            min_loss_usdt_for_consecutive=self.cfg.get("risk", "min_loss_usdt_for_consecutive", default=0.5),
            ignore_loss_cooldown_reasons=self.cfg.get("risk", "ignore_loss_cooldown_reasons", default=["early_exit"]),
            ignore_consecutive_loss_reasons=self.cfg.get("risk", "ignore_consecutive_loss_reasons", default=["early_exit"]),
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
        self.structure_zone_analyzer = StructureZoneAnalyzer()
        self.liquidity_heatmap = LiquidityHeatmap(depth_levels=200)
        self.market_structure_engine = MarketStructureEngine(
            swing_lookback=self.cfg.get("market_structure", "swing_lookback", default=2),
            volume_spike_mult=self.cfg.get("market_structure", "volume_spike_mult", default=2.0),
            bos_volume_mult=self.cfg.get("market_structure", "bos_volume_mult", default=1.5),
            spread_expansion_mult=self.cfg.get("market_structure", "spread_expansion_mult", default=1.5),
        )
        self.ai_analyzer = AITradeAnalyzer()
        self.ai_analyzer.min_confidence = self.cfg.get("ai", "min_confidence", default=60)
        self.ai_analyzer.fail_open = self.cfg.get("ai", "fail_open", default=True)
        self.ai_analyzer.require_direction_match = self.cfg.get("ai", "require_direction_match", default=True)
        self.ai_analyzer.uniformity_guard_enabled = self.cfg.get("ai", "uniformity_guard_enabled", default=True)
        self.ai_analyzer.uniformity_window = int(self.cfg.get("ai", "uniformity_window", default=8))
        self.ai_analyzer.uniformity_conf_spread_max = int(
            self.cfg.get("ai", "uniformity_conf_spread_max", default=3)
        )
        self.atr = ATRCalculator(period=self.cfg.get("atr", "period", default=14))

        self.entry_engine = EntryEngine(self.cfg)
        self.allocator = MultiSymbolCapitalAllocator()
        self.position_manager = PositionManager()
        self.rl_agent = RLPositionAgent(
            add_threshold=self.cfg.get("rl", "add_threshold", default=0.78),
            reduce_threshold=self.cfg.get("rl", "reduce_threshold", default=0.7),
            close_threshold=self.cfg.get("rl", "close_threshold", default=0.8),
            min_close_profit_pct=self.cfg.get("rl", "min_close_profit_pct", default=0.5),
            max_panic_loss_pct=self.cfg.get("rl", "max_panic_loss_pct", default=0.6),
            min_reduce_profit_pct=self.cfg.get("rl", "min_reduce_profit_pct", default=0.8),
        )
        self.exit_engine = ExitEngine(
            hard_sl_atr_mult=self.cfg.get("exit", "hard_sl_atr_mult", default=1.8),
            early_exit_bars=self.cfg.get("exit", "early_exit_bars", default=12),
            early_exit_min_profit_atr=self.cfg.get("exit", "early_exit_min_profit_atr", default=0.35),
            trailing_activation_atr=self.cfg.get("exit", "trailing_activation_atr", default=0.8),
            trailing_distance_atr=self.cfg.get("exit", "trailing_distance_atr", default=1.2),
            tp_cap_atr_mult=self.cfg.get("exit", "tp_cap_atr_mult", default=8.0),
            min_profit_before_trail_pct=self.cfg.get("exit", "min_profit_before_trail_pct", default=0.5),
            sl_buffer_atr_mult=self.cfg.get("exit", "sl_buffer_atr_mult", default=0.2),
        )

        self.tg = None
        tg_token = self.security.get_key("TELEGRAM_TOKEN")
        tg_chat_id = self.security.get_key("TELEGRAM_CHAT_ID")
        if tg_token:
            self.tg = TelegramController(
                token=tg_token,
                controls=self.controls,
                allowed_chat_id=int(tg_chat_id) if tg_chat_id else None,
                mode_switcher=self._switch_signal_mode,
            )
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
        self.htf_4h_interval = self.cfg.get("bot", "htf_4h_interval", default="240")
        self.cycle_sleep = self.cfg.get("bot", "cycle_sleep_sec", default=45)
        self.scan_interval_sec = int(self.cfg.get("bot", "scan_interval_sec", default=self.cycle_sleep))
        self.position_active_sleep_sec = int(self.cfg.get("bot", "position_active_sleep_sec", default=15))
        self._last_scan_ts = 0.0
        self.feature_window = self.cfg.get("bot", "feature_window", default=128)
        self.klines_limit = max(self.cfg.get("bot", "klines_limit", default=180), self.feature_window)
        self.signal_only = self.cfg.get("bot", "signal_only", default=False)
        self.controls.signal_only = self.signal_only
        self.signal_cooldown_sec = int(self.cfg.get("bot", "signal_cooldown_sec", default=3600) or 0)
        self._last_signal_ts: dict[tuple[str, str], float] = {}
        self.signal_feedback = SignalFeedbackLoop(BOT_DIR, self.cfg)
        self.feedback_notify_labeling = self.cfg.get("feedback_loop", "notify_labeling", default=True)
        self.feedback_train_epochs = int(self.cfg.get("feedback_loop", "train_epochs", default=220))
        self.feedback_train_lr = float(self.cfg.get("feedback_loop", "train_lr", default=0.002))
        self.feedback_train_batch_size = int(self.cfg.get("feedback_loop", "train_batch_size", default=32))
        self.feedback_train_val_ratio = float(self.cfg.get("feedback_loop", "train_val_ratio", default=0.2))
        self.feedback_train_decision_threshold = float(
            self.cfg.get("feedback_loop", "train_decision_threshold", default=0.55)
        )
        self.feedback_train_seed = int(self.cfg.get("feedback_loop", "train_seed", default=42))
        self.feedback_augment_wins_factor = int(self.cfg.get("feedback_loop", "augment_wins_factor", default=2))
        self.feedback_augment_noise_std = float(self.cfg.get("feedback_loop", "augment_noise_std", default=0.03))
        self.quality_gate_enabled = self.cfg.get("quality_gate", "enabled", default=True)
        self.quality_gate_min_confidence = float(self.cfg.get("quality_gate", "min_confidence", default=0.68))
        self.quality_gate_min_expected_edge = float(
            self.cfg.get("quality_gate", "min_expected_edge", default=0.75)
        )
        self.quality_gate_min_adx = float(self.cfg.get("quality_gate", "anti_flat_min_adx", default=16.0))
        self.quality_gate_min_atr_pct = float(self.cfg.get("quality_gate", "anti_flat_min_atr_pct", default=0.20))
        self.quality_gate_min_abs_imbalance = float(
            self.cfg.get("quality_gate", "anti_flat_min_abs_imbalance", default=0.08)
        )
        self.quality_gate_allow_chop = self.cfg.get("quality_gate", "anti_flat_allow_chop", default=False)
        self.quality_gate_require_htf_trend = self.cfg.get("quality_gate", "anti_flat_require_htf_trend", default=False)
        self.quality_gate_countertrend_min_confidence = float(
            self.cfg.get("quality_gate", "countertrend_min_confidence", default=0.82)
        )
        self.quality_gate_countertrend_min_abs_imbalance = float(
            self.cfg.get("quality_gate", "countertrend_min_abs_imbalance", default=0.20)
        )
        self.quality_gate_no_zone_min_confidence = float(
            self.cfg.get("quality_gate", "no_zone_min_confidence", default=0.84)
        )
        self.quality_gate_reject_no_zone_entries = self.cfg.get(
            "quality_gate", "reject_no_zone_entries", default=False
        )
        self.correlation_filter_enabled = self.cfg.get("correlation", "enabled", default=True)
        self.correlation_filter = CorrelationFilter(
            threshold=float(self.cfg.get("correlation", "threshold", default=0.75)),
            max_correlated=int(self.cfg.get("correlation", "max_correlated", default=1)),
            lookback=int(self.cfg.get("correlation", "lookback", default=50)),
        )
        self.mtf_zone_enabled = self.cfg.get("mtf_zone_confirmation", "enabled", default=True)
        self.mtf_zone_require_any_zone = self.cfg.get("mtf_zone_confirmation", "require_any_zone", default=False)
        self.mtf_zone_min_confidence_if_single_tf = float(
            self.cfg.get("mtf_zone_confirmation", "min_confidence_if_single_tf", default=0.78)
        )
        self.symbol_quality_filter = SymbolQualityFilter(BOT_DIR, self.cfg)
        self.feedback_use_merged_dataset_for_retrain = self.cfg.get(
            "feedback_loop", "use_merged_dataset_for_retrain", default=True
        )
        self.feedback_apply_to_risk_guard = self.cfg.get(
            "feedback_loop", "apply_to_risk_guard", default=False
        )
        self.feedback_base_dataset_path = BOT_DIR / self.cfg.get(
            "feedback_loop", "base_dataset_path", default="training_data.json"
        )
        self.feedback_min_label_abs_pnl_pct = float(
            self.cfg.get("feedback_loop", "min_feedback_label_abs_pnl_pct", default=0.4)
        )
        self.feedback_min_label_hold_minutes = float(
            self.cfg.get("feedback_loop", "min_feedback_label_hold_minutes", default=8.0)
        )
        self.strict_htf_mode = self.cfg.get("entry", "strict_htf_mode", default=True)
        self.volatility_floor_enabled = self.cfg.get("entry", "volatility_floor_enabled", default=True)
        self.volatility_floor_atr_pct = float(
            self.cfg.get("entry", "volatility_floor_atr_pct", default=0.8)
        )
        self.adaptive_regime_presets_enabled = self.cfg.get("adaptive_regime_presets", "enabled", default=True)
        self.adaptive_regime_presets_interval_sec = int(
            self.cfg.get("adaptive_regime_presets", "switch_interval_sec", default=900)
        )
        self.adaptive_regime_presets_notify = self.cfg.get(
            "adaptive_regime_presets", "notify_on_switch", default=True
        )
        self.adaptive_regime_presets_benchmark_symbol = self.cfg.get(
            "adaptive_regime_presets", "benchmark_symbol", default="BTCUSDT"
        )
        self.adaptive_trend_strict_htf_mode = self.cfg.get(
            "adaptive_regime_presets", "trend_strict_htf_mode", default=True
        )
        self.adaptive_trend_volatility_floor_atr_pct = float(
            self.cfg.get("adaptive_regime_presets", "trend_volatility_floor_atr_pct", default=0.8)
        )
        self.adaptive_range_strict_htf_mode = self.cfg.get(
            "adaptive_regime_presets", "range_strict_htf_mode", default=True
        )
        self.adaptive_range_volatility_floor_atr_pct = float(
            self.cfg.get("adaptive_regime_presets", "range_volatility_floor_atr_pct", default=1.0)
        )
        self._last_regime_profile_check_ts = 0.0
        self._active_regime_profile = "manual"
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
        self.exchange_closed_confirm_cycles = int(
            self.cfg.get("position_sync", "exchange_closed_confirm_cycles", default=3)
        )
        self.exchange_closed_require_closed_pnl = self.cfg.get(
            "position_sync", "exchange_closed_require_closed_pnl", default=True
        )
        self.exchange_closed_force_cycles = int(
            self.cfg.get("position_sync", "exchange_closed_force_cycles", default=8)
        )
        self._missing_exchange_cycles: dict[str, int] = {}
        self._failed_close_attempts: dict[str, int] = {}
        self.exchange_closed_reentry_cooldown_sec = int(
            self.cfg.get("position_sync", "exchange_closed_reentry_cooldown_sec", default=900)
        )
        self.exchange_closed_pause_after_rate_limit_sec = int(
            self.cfg.get("position_sync", "pause_exchange_closed_after_rate_limit_sec", default=180)
        )
        self._last_exchange_sync_pause_log_ts = 0.0
        self._exchange_closed_reentry_until: dict[str, float] = {}
        self.partial_tp_enabled = self.cfg.get("partial_tp", "enabled", default=True)
        self.partial_tp_trigger_progress = self.cfg.get("partial_tp", "trigger_progress", default=0.5)
        self.partial_tp_close_fraction = self.cfg.get("partial_tp", "close_fraction", default=0.5)
        self.partial_tp_move_stop_to_entry = self.cfg.get("partial_tp", "move_stop_to_entry", default=True)
        self.portfolio_tp_enabled = self.cfg.get("portfolio_tp", "enabled", default=True)
        self.portfolio_tp_target_pct = self.cfg.get("portfolio_tp", "target_profit_pct", default=2.0)
        self.basket_profit_guard_enabled = self.cfg.get("basket_profit_guard", "enabled", default=True)
        self.basket_profit_min_positions = self.cfg.get("basket_profit_guard", "min_positions", default=3)
        self.basket_profit_window_sec = self.cfg.get("basket_profit_guard", "monitor_window_sec", default=900)
        self.basket_profit_symbol_drop_pct = self.cfg.get("basket_profit_guard", "symbol_pnl_drop_pct", default=40.0)
        self.basket_profit_total_drawdown_pct = self.cfg.get("basket_profit_guard", "total_drawdown_pct_after_symbol_drop", default=15.0)
        self.basket_profit_min_symbol_peak = self.cfg.get("basket_profit_guard", "min_symbol_peak_profit_usdt", default=0.5)
        self.basket_profit_min_total_usdt = self.cfg.get("basket_profit_guard", "min_total_profit_usdt", default=1.0)
        self.basket_drawdown_confirm_sec = self.cfg.get("basket_profit_guard", "drawdown_confirm_sec", default=900.0)
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
        # Pyramid strategy
        self.pyramid_enabled = self.cfg.get("pyramid", "enabled", default=True)
        self.pyramid_max_adds = self.cfg.get("pyramid", "max_adds", default=2)
        self.pyramid_max_total_risk_pct = self.cfg.get("pyramid", "max_total_risk_pct", default=2.0)
        self.pyramid_add1_min_r = self.cfg.get("pyramid", "add1_min_r", default=0.5)
        self.pyramid_add2_min_r = self.cfg.get("pyramid", "add2_min_r", default=1.2)

    async def _notify_tg(self, message: str):
        if self.tg:
            await self.tg.send_alert(message)

    async def get_trade_symbols(self) -> list:
        """Scan top symbols by momentum. Whitelist symbols always at front (priority)."""
        try:
            tickers = await self.client.get_tickers()
        except Exception as exc:
            logger.error(f"Failed to get tickers: {exc}")
            return self.whitelist[:25] if self.whitelist else []

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
            price_change_pct = abs(float(ticker.get("price24hPcnt", 0) or 0)) * 100
            momentum_score = turnover * (1 + price_change_pct / 10)
            ranked.append((symbol, turnover, momentum_score))

        ranked.sort(key=lambda item: item[2], reverse=True)
        symbols = [item[0] for item in ranked[: self.max_symbols]]

        # Whitelist symbols always at front (priority, not exclusive)
        if self.whitelist_enabled:
            ordered = [s for s in self.whitelist if s not in self.blacklist]
            for s in reversed(ordered):
                if s in symbols:
                    symbols.remove(s)
                symbols.insert(0, s)

        unique = []
        seen = set()
        for s in symbols:
            if s not in seen:
                unique.append(s)
                seen.add(s)

        result = unique[:25]
        wl_in = [s for s in self.whitelist if s in result]
        logger.info(f"Symbol scanner: {len(ranked)} eligible → top {len(result)} (whitelist: {wl_in})")
        return result

    async def run(self):
        logger.info("=" * 72)
        logger.info("TRADING BOT v9.0 — AI FUND ARCHITECTURE")
        logger.info("DATA → STRUCTURE → SWEEP/BOS → ENTRY → EXECUTION")
        logger.info("=" * 72)
        logger.info(
            f"Entry threshold={self.entry_engine.entry_threshold:.2f} | "
            f"same-side cooldown={self.signal_cooldown_sec}s"
        )
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
        if self.signal_only:
            logger.info(
                "Signal feedback loop: "
                f"{'ON' if self.signal_feedback.enabled else 'OFF'} "
                f"(pending timeout={self.signal_feedback.max_pending_hours}h)"
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
            f"Symbol quality filter: {'ON' if self.symbol_quality_filter.enabled else 'OFF'}"
        )

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
                f"Режим: {'СИГНАЛЫ' if self.signal_only else ('ТЕСТ' if self.controls.dry_run else 'LIVE')}\n"
                f"Стратегия: SMC v3 (Sweep→BOS→Retest OB/FVG) + AI + Pyramid"
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

                await self._maybe_apply_regime_preset()

                if self.signal_only and self.signal_feedback.enabled:
                    await self._process_signal_feedback_loop()

                if not self.signal_only:
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
                    if can_trade and (self.signal_only or self.position_manager.count() < self.controls.max_positions):
                        if self._should_scan_entries_now():
                            await self._scan_entries(symbols)
                    elif not can_trade:
                        logger.info(f"Trading blocked: {reason}")
                else:
                    logger.info("Bot paused or emergency")

                self.controls.set_positions(self.position_manager.to_controls_dict())
                sleep_sec = self._get_cycle_sleep_sec()
                logger.info(f"Cycle {cycle} done. Sleeping {sleep_sec}s...")
                await asyncio.sleep(sleep_sec)
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
                            await self._finalize_full_close(symbol, pos, current_price, pnl, "exchange_closed", already_removed=True)
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

            # Get swing levels for R-based trailing
            structure = self.market_structure_engine.analyze(klines, atr_val)
            last_swing_low = structure.swing_lows[-1].price if structure.swing_lows else 0.0
            last_swing_high = structure.swing_highs[-1].price if structure.swing_highs else 0.0
            self.exit_engine.update_trailing(pos, current_price, last_swing_low, last_swing_high)

            # --- Trailing stop diagnostic logging ---
            risk = abs(pos.entry_price - pos.stop_loss) if pos.stop_loss > 0 else pos.entry_price * 0.01
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
                        logger.error(
                            f"[FORCE REMOVE] {symbol} — {fails} consecutive close failures. "
                            f"Removing zombie position (entry={pos.entry_price:.4f} current={current_price:.4f})"
                        )
                        self._failed_close_attempts.pop(symbol, None)
                        pos = self.position_manager.remove(symbol)
                        if pos:
                            pnl = self._calc_pnl(pos, current_price, pos.qty)
                            await self._finalize_full_close(symbol, pos, current_price, pnl, "force_closed_stale", already_removed=True)
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
        reject_counts: dict[str, int] = {}

        def mark_reject(reason: str):
            reject_counts[reason] = reject_counts.get(reason, 0) + 1

        for symbol in symbols:
            if self.position_manager.has(symbol):
                mark_reject("already_in_position")
                continue

            exchange_closed_wait = self._exchange_closed_reentry_remaining(symbol)
            if exchange_closed_wait > 0:
                mark_reject("exchange_closed_reentry_cooldown")
                continue

            quality_allowed, quality_reason, quality_stats = self.symbol_quality_filter.allow(
                symbol,
                is_whitelisted=symbol in self.whitelist,
            )
            if not quality_allowed:
                mark_reject(f"symbol_quality_{quality_reason}")
                continue

            allowed, _ = self.risk_guard.can_trade(symbol)
            if not allowed:
                mark_reject("risk_blocked")
                continue
            try:
                signal = await self._analyze_symbol(symbol)
                if signal.should_enter:
                    cooldown_left = self._same_side_cooldown_remaining(symbol, signal.side)
                    if cooldown_left > 0:
                        logger.info(f"SAME-SIDE COOLDOWN {symbol} {signal.side}: {cooldown_left}s left")
                        mark_reject("same_side_cooldown")
                        continue

                    if self.quality_gate_enabled:
                        gate_ok, gate_reason, gate_meta = self._passes_signal_quality_gate(symbol, signal)
                        if not gate_ok:
                            logger.info(f"QUALITY GATE REJECT {symbol}: {gate_reason}")
                            mark_reject(f"quality_gate_{gate_reason}")
                            continue
                        signal.metadata.update(gate_meta)

                    same_side_peers = self._same_side_peer_symbols(signal.side, candidates)
                    corr_ok, corr_reason = await self._passes_correlation_filter(symbol, same_side_peers)
                    if not corr_ok:
                        logger.info(f"CORRELATION REJECT {symbol}: {corr_reason}")
                        mark_reject("correlation_blocked")
                        continue

                    signal.metadata.update(
                        {
                            "symbol_quality_trades": quality_stats.get("trades", 0),
                            "symbol_quality_winrate": quality_stats.get("winrate", 0.0),
                            "symbol_quality_avg_pnl": quality_stats.get("avg_pnl", 0.0),
                        }
                    )

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
                else:
                    mark_reject(signal.metadata.get("reject_reason", "entry_filters"))
            except Exception as exc:
                logger.error(f"Error analyzing {symbol}: {exc}")
                mark_reject("exception")
            await asyncio.sleep(0.8)

        ranked = self.allocator.allocate(candidates)
        self.controls.set_candidates(ranked)
        summary = ", ".join(f"{key}={value}" for key, value in sorted(reject_counts.items())) or "none"
        logger.info(f"SCAN SUMMARY: symbols={len(symbols)} candidates={len(ranked)} rejects[{summary}]")

        if self.signal_only:
            # Signal-only mode: send to Telegram, no execution
            for item in ranked:
                signal = item["signal"]
                symbol = item["symbol"]
                side = signal.side
                direction = "LONG" if side == "BUY" else "SHORT"
                sl = signal.stop_loss
                tp = signal.take_profit
                tp1 = signal.metadata.get("tp1_level", tp)
                entry = signal.entry_price
                rr = signal.rr_ratio
                zone = signal.metadata.get("entry_zone", "none")
                bos = signal.metadata.get("bos_direction", "none")
                sweep = signal.metadata.get("sweep_direction", "none")
                conf = signal.confidence
                expected_edge = float(signal.metadata.get("quality_expected_edge", 0.0) or 0.0)
                entry_range_low = float(signal.metadata.get("entry_range_low", entry) or entry)
                entry_range_high = float(signal.metadata.get("entry_range_high", entry) or entry)

                msg = (
                    f"<b>SIGNAL {direction}</b>\n\n"
                    f"Монета: <code>{symbol}</code>\n"
                    f"Вход: <code>${entry:.4f}</code>\n"
                    f"Рекомендуемый вход: <code>${entry_range_low:.4f} - ${entry_range_high:.4f}</code>\n"
                    f"SL: <code>${sl:.4f}</code>\n"
                    f"TP1: <code>${tp1:.4f}</code>\n"
                    f"TP2: <code>${tp:.4f}</code>\n"
                    f"RR: <code>{rr:.1f}</code>\n"
                    f"Confidence: <code>{conf:.0%}</code>\n"
                    f"Expected Edge: <code>{expected_edge:.2f}R</code>\n"
                    f"Zone: <code>{zone}</code>\n"
                    f"BOS: <code>{bos}</code> | Sweep: <code>{sweep}</code>"
                )
                logger.info(f"SIGNAL-ONLY {symbol}: {direction} entry=${entry:.4f} SL=${sl:.4f} TP=${tp:.4f} RR={rr:.1f}")
                self._register_signal_timestamp(symbol, side)
                self.signal_feedback.register_signal(symbol, signal)
                if self.tg:
                    await self.tg.send_message(msg)
            return

        available_slots = max(0, self.controls.max_positions - self.position_manager.count())
        for item in ranked[:available_slots]:
            await self._execute_entry(item["symbol"], item["signal"], item.get("capital_weight", 1.0))

    async def _analyze_symbol(self, symbol: str) -> EntrySignal:
        def reject(reason: str) -> EntrySignal:
            signal = EntrySignal()
            signal.metadata["reject_reason"] = reason
            return signal

        klines = await self.client.get_klines(symbol, self.candle_interval, self.klines_limit)
        if len(klines) < 80:
            return reject("not_enough_klines")
        htf_klines = await self.client.get_klines(symbol, self.htf_interval, max(80, self.feature_window))
        market = self.market_analyzer.analyze(klines, htf_klines)
        if not market.can_trade:
            return reject("market_blocked")

        if self.volatility_floor_enabled:
            vol_ok, vol_reason = self._passes_volatility_floor(float(market.atr_pct or 0.0))
            if not vol_ok:
                return reject(vol_reason)

        # 4H trend — the ultimate directional filter
        htf_4h_klines = await self.client.get_klines(symbol, self.htf_4h_interval, 30)
        htf_4h_trend = self._determine_4h_trend(htf_4h_klines)

        atr_val = self.atr.get_atr(symbol, klines)
        current_price = float(klines[-1]["close"])

        # Market Structure: swings, BOS, sweeps, momentum
        structure = self.market_structure_engine.analyze(klines, atr_val)

        orderbook = await self.client.get_orderbook(symbol, limit=25)
        trades = await self.client.get_recent_trades(symbol, limit=120)
        orderflow = self.orderflow_analyzer.analyze(orderbook, trades)

        # Real orderbook-based heatmap (replaces synthetic fallback)
        heatmap_orderbook = await self.client.get_orderbook(symbol, limit=200)
        heatmap = self.liquidity_heatmap.build_heatmap(heatmap_orderbook)
        magnet_dir, magnet_target = self.liquidity_heatmap.get_liquidity_magnet(current_price, heatmap)

        liq = self._resolve_liquidation_context(symbol, current_price, klines)
        if liq.target_level <= 0:
            # Use real heatmap data before falling back to synthetic
            liq = self._heatmap_to_liq_analysis(current_price, heatmap, magnet_dir, magnet_target)
        if liq.target_level <= 0:
            liq = self._build_directional_liq_fallback(current_price, market, orderflow, atr_val)
        self.controls.set_heatmap(symbol, liq)

        zone_context = self.structure_zone_analyzer.analyze(htf_klines, current_price)
        zone_context_4h = self.structure_zone_analyzer.analyze(htf_4h_klines, current_price)

        regime = self.regime_ai.classify(market)
        features = self.feature_engineer.build(klines, orderflow, liq, atr_val)
        transformer = self.transformer_model.predict(features, regime, orderflow, liq)

        # Get funding rate
        funding_rate = 0.0
        try:
            tickers = await self.client.get_tickers()
            for t in tickers:
                if t.get("symbol") == symbol:
                    funding_rate = float(t.get("fundingRate", 0) or 0)
                    break
        except Exception:
            pass

        signal = self.entry_engine.generate_signal(
            symbol, klines, current_price, market, regime, transformer, orderflow, liq,
            atr_val, zone_context=zone_context, structure=structure, funding_rate=funding_rate,
            htf_4h_trend=htf_4h_trend,
        )
        if not signal.should_enter:
            signal.metadata.setdefault("reject_reason", "entry_filters")
            return signal

        if self.strict_htf_mode:
            htf_ok, htf_reason = self._passes_strict_htf_mode(signal.side, htf_4h_trend)
            if not htf_ok:
                return reject(htf_reason)

        if self.mtf_zone_enabled:
            zone_15m_ok = signal.metadata.get("entry_zone", "no_zone") != "no_zone"
            zone_4h_ok = self._zone_matches_side(zone_context_4h, current_price, signal.side)
            confirmations = int(zone_15m_ok) + int(zone_4h_ok)

            signal.metadata.update(
                {
                    "zone_confirm_15m": zone_15m_ok,
                    "zone_confirm_4h": zone_4h_ok,
                    "zone_confirm_count": confirmations,
                }
            )

            if self.mtf_zone_require_any_zone and confirmations == 0:
                return reject("mtf_zone_missing")

            if confirmations == 1 and signal.confidence < self.mtf_zone_min_confidence_if_single_tf:
                return reject("mtf_single_tf_low_confidence")

        liquidity = sum(float(item.get("volume", 0.0)) for item in klines[-30:]) * current_price
        signal.metadata.update({
            "liquidity": liquidity,
            "volatility": market.atr_pct / 100,
            "adx": market.adx,
            "trend": market.trend.name.lower(),
            "htf_trend": market.htf_trend.name.lower(),
            "atr_pct": market.atr_pct,
        })

        # AI is MANDATORY — not advisory
        if self.ai_analyzer.enabled and self.controls.ai_enabled:
            ai_result = await self.ai_analyzer.analyze(symbol, self._build_ai_payload(current_price, market, signal))
            ai_confidence = ai_result.get("confidence", 0)
            ai_should_trade = ai_result.get("should_trade", False)
            ai_min_confidence = self.cfg.get("ai", "min_confidence", default=55)

            if not ai_should_trade:
                logger.info(f"[AI] {symbol} REJECTED: {ai_result.get('reason', 'no reason')} (conf={ai_confidence})")
                return reject(f"ai_rejected ({ai_confidence})")

            if ai_confidence < ai_min_confidence:
                logger.info(f"[AI] {symbol} REJECTED: AI confidence {ai_confidence} < {ai_min_confidence}")
                return reject(f"ai_low_confidence ({ai_confidence})")

            signal.confidence = round((signal.confidence + ai_confidence / 100) / 2, 4)
            signal.capital_score = round(signal.confidence * signal.rr_ratio, 4)
        elif not self.cfg.get("ai", "fail_open", default=False):
            # AI disabled but fail_open=false → reject
            return reject("ai_disabled_fail_closed")

        logger.info(
            f"SIGNAL {symbol}: {signal.side} conf={signal.confidence:.0%} "
            f"smc={signal.metadata.get('smc_score', 0):.2f} "
            f"zone={signal.metadata.get('entry_zone', 'none')} "
            f"bos={signal.metadata.get('bos_direction', 'none')} sweep={signal.metadata.get('sweep_direction', 'none')} "
            f"4H={'BULL' if htf_4h_trend > 0 else 'BEAR' if htf_4h_trend < 0 else 'FLAT'} "
            f"RR={signal.rr_ratio:.1f}"
        )
        return signal

    def _passes_volatility_floor(self, atr_pct: float) -> tuple[bool, str]:
        if not self.volatility_floor_enabled:
            return True, ""
        if atr_pct >= self.volatility_floor_atr_pct:
            return True, ""
        return False, f"volatility_floor ({atr_pct:.3f}% < {self.volatility_floor_atr_pct:.3f}%)"

    def _passes_strict_htf_mode(self, side: str, htf_4h_trend: int) -> tuple[bool, str]:
        if not self.strict_htf_mode:
            return True, ""
        side_up = str(side or "").upper()
        if htf_4h_trend == 0 or side_up not in {"BUY", "SELL"}:
            return True, ""
        if side_up == "BUY" and htf_4h_trend < 0:
            return False, "strict_htf_bear_only"
        if side_up == "SELL" and htf_4h_trend > 0:
            return False, "strict_htf_bull_only"
        return True, ""

    def _switch_signal_mode(self, signal_only: bool) -> tuple[bool, str]:
        target = bool(signal_only)
        if self.signal_only == target:
            return True, f"Режим уже {'SIGNAL-ONLY' if target else 'LIVE'}"

        self.signal_only = target
        self.controls.signal_only = target

        try:
            config_path = BOT_DIR / "config.yaml"
            with open(config_path, "r", encoding="utf-8") as handle:
                cfg = yaml.safe_load(handle) or {}
            if not isinstance(cfg, dict):
                cfg = {}
            cfg.setdefault("bot", {})["signal_only"] = target
            with open(config_path, "w", encoding="utf-8") as handle:
                yaml.safe_dump(cfg, handle, sort_keys=False, allow_unicode=True)
            mode_label = "SIGNAL-ONLY" if target else "LIVE"
            logger.info(f"[MODE SWITCH] Execution mode changed to {mode_label}")
            return True, f"Режим переключён: {mode_label}"
        except Exception as exc:
            logger.error(f"[MODE SWITCH] Failed to persist mode: {exc}")
            return False, f"Ошибка сохранения режима: {exc}"

    def _resolve_regime_preset(self, regime_value: str) -> tuple[str, bool, float]:
        regime = str(regime_value or "range").lower()
        # Treat breakout/volatile closer to trend profile
        if regime in {"trend", "breakout", "volatile"}:
            return (
                "trend",
                bool(self.adaptive_trend_strict_htf_mode),
                float(self.adaptive_trend_volatility_floor_atr_pct),
            )
        return (
            "range",
            bool(self.adaptive_range_strict_htf_mode),
            float(self.adaptive_range_volatility_floor_atr_pct),
        )

    async def _detect_profile_regime(self) -> str:
        symbol = self.adaptive_regime_presets_benchmark_symbol
        klines = await self.client.get_klines(symbol, self.candle_interval, max(80, self.feature_window))
        htf_klines = await self.client.get_klines(symbol, self.htf_interval, max(80, self.feature_window))
        market = self.market_analyzer.analyze(klines, htf_klines)
        prediction = self.regime_ai.classify(market)
        return prediction.regime.value

    async def _maybe_apply_regime_preset(self):
        if not self.adaptive_regime_presets_enabled:
            return
        now_ts = time.time()
        if now_ts - self._last_regime_profile_check_ts < max(30, self.adaptive_regime_presets_interval_sec):
            return
        self._last_regime_profile_check_ts = now_ts

        try:
            regime_value = await self._detect_profile_regime()
            profile_name, target_strict_htf, target_vol_floor = self._resolve_regime_preset(regime_value)

            changed = (
                self._active_regime_profile != profile_name
                or self.strict_htf_mode != target_strict_htf
                or abs(self.volatility_floor_atr_pct - target_vol_floor) > 1e-9
            )

            self.strict_htf_mode = target_strict_htf
            self.volatility_floor_atr_pct = target_vol_floor

            if changed:
                self._active_regime_profile = profile_name
                msg = (
                    f"[ADAPTIVE PRESET] profile={profile_name} regime={regime_value} "
                    f"strict_htf={'ON' if self.strict_htf_mode else 'OFF'} "
                    f"vol_floor={self.volatility_floor_atr_pct:.2f}%"
                )
                logger.info(msg)
                if self.tg and self.adaptive_regime_presets_notify:
                    await self.tg.send_message(
                        "<b>ADAPTIVE PRESET SWITCH</b>\n"
                        f"Профиль: <code>{profile_name}</code>\n"
                        f"Режим рынка: <code>{regime_value}</code>\n"
                        f"Strict HTF: <code>{'ON' if self.strict_htf_mode else 'OFF'}</code>\n"
                        f"Vol floor ATR%: <code>{self.volatility_floor_atr_pct:.2f}</code>"
                    )
        except Exception as exc:
            logger.warning(f"Adaptive preset switch skipped: {exc}")

    def _determine_4h_trend(self, klines_4h: list) -> int:
        """Determine 4H trend: 1=bullish, -1=bearish, 0=neutral.

        Uses EMA20 vs EMA50 on 4H candles + last 3 candle direction.
        """
        if len(klines_4h) < 20:
            return 0
        closes = [float(k["close"]) for k in klines_4h]

        # EMA20 vs EMA50
        ema20 = self._ema(closes, 20)
        ema50 = self._ema(closes, min(50, len(closes)))

        # Last 3 candles direction
        recent = closes[-3:]
        rising = recent[-1] > recent[0]
        falling = recent[-1] < recent[0]

        if ema20 > ema50 and rising:
            return 1
        elif ema20 < ema50 and falling:
            return -1
        return 0

    @staticmethod
    def _ema(data: list, period: int) -> float:
        if len(data) < period:
            return sum(data) / len(data) if data else 0
        mult = 2 / (period + 1)
        ema = sum(data[:period]) / period
        for val in data[period:]:
            ema = (val - ema) * mult + ema
        return ema

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
            logger.info(f"ENTERED {symbol}: {signal.side} qty={pos.qty:.6f} entry=${executed_price:.4f} weight={capital_weight:.2f}")

    async def _process_signal_feedback_loop(self):
        outcomes = await self.signal_feedback.process_pending(self.client.get_price)
        if outcomes:
            wins = sum(1 for item in outcomes if item.record.get("result") == "win")
            losses = len(outcomes) - wins
            quality_labels = sum(1 for item in outcomes if self._is_quality_feedback_record(item.record))
            self.signal_feedback.add_quality_labels(quality_labels)
            if self.feedback_apply_to_risk_guard and (not self.signal_only):
                for item in outcomes:
                    symbol = str(item.record.get("symbol", ""))
                    pnl_proxy = 1.0 if item.record.get("result") == "win" else -1.0
                    self.risk_guard.record_trade(pnl_proxy, symbol=symbol, reason="signal_feedback")
            logger.info(
                f"[FEEDBACK] resolved={len(outcomes)} win={wins} loss={losses} "
                f"quality_labels={quality_labels} dataset={self.signal_feedback.dataset_path.name}"
            )
            if self.tg and self.feedback_notify_labeling:
                await self.tg.send_message(
                    "<b>FEEDBACK LOOP</b>\n"
                    f"Размечено сигналов: <code>{len(outcomes)}</code>\n"
                    f"Win: <code>{wins}</code> | Loss: <code>{losses}</code>\n"
                    f"Качественных меток: <code>{quality_labels}</code>\n"
                    f"Датасет: <code>{self.signal_feedback.dataset_path.name}</code>"
                )

        if self.signal_feedback.should_run_daily_retrain():
            await self._run_feedback_daily_retrain()

    async def _run_feedback_daily_retrain(self):
        logger.info("[FEEDBACK] Daily retrain started from signal-only labels")
        success = False
        try:
            from train_transformer import train as train_transformer_model

            output_path = str(self.entry_engine._resolve_weights_path())
            data_path = self._build_retrain_dataset()
            success = await asyncio.to_thread(
                train_transformer_model,
                data_path=str(data_path),
                epochs=self.feedback_train_epochs,
                lr=self.feedback_train_lr,
                batch_size=self.feedback_train_batch_size,
                output_path=output_path,
                val_ratio=self.feedback_train_val_ratio,
                decision_threshold=self.feedback_train_decision_threshold,
                seed=self.feedback_train_seed,
                augment_wins_factor=max(1, self.feedback_augment_wins_factor),
                augment_noise_std=max(0.0, self.feedback_augment_noise_std),
            )
            if success:
                self.entry_engine._load_trained_model()
                logger.info("[FEEDBACK] Daily retrain completed successfully")
                if self.tg:
                    await self.tg.send_message(
                        "<b>DAILY RETRAIN DONE</b>\n"
                        f"Файл весов: <code>{self.entry_engine._resolve_weights_path().name}</code>"
                    )
            else:
                logger.warning("[FEEDBACK] Daily retrain finished with failure status")
                if self.tg:
                    await self.tg.send_message(
                        "<b>DAILY RETRAIN FAILED</b>\n"
                        "Обучение завершилось без валидного улучшения/чекпоинта."
                    )
        except Exception as exc:
            logger.error(f"[FEEDBACK] Daily retrain error: {exc}")
            if self.tg:
                await self.tg.send_message(
                    "<b>DAILY RETRAIN ERROR</b>\n"
                    f"Ошибка: <code>{exc}</code>"
                )
        finally:
            self.signal_feedback.mark_retrain_attempt(success)

    async def _finalize_full_close(self, symbol: str, pos: Position, exit_price: float, pnl: float, reason: str, already_removed: bool = False):
        self._missing_exchange_cycles.pop(symbol, None)
        if reason == "exchange_closed":
            self._set_exchange_closed_reentry_block(symbol)
        if not already_removed:
            self.position_manager.remove(symbol)
        self.risk_guard.record_trade(pnl, symbol, reason=reason)
        self.controls.add_trade(pnl, symbol, pos.side, reason)
        self._save_trade(symbol, pos.side, pos.qty, pos.entry_price, exit_price, pnl, reason, origin=pos.origin)
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
            return (exit_price - pos.entry_price) * qty
        return (pos.entry_price - exit_price) * qty

    def _calc_pnl_pct(self, pos: Position, price: float) -> float:
        if pos.entry_price <= 0:
            return 0.0
        if pos.is_long:
            return (price - pos.entry_price) / pos.entry_price * 100
        return (pos.entry_price - price) / pos.entry_price * 100

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

    def _passes_signal_quality_gate(self, symbol: str, signal: EntrySignal) -> tuple[bool, str, dict]:
        confidence = float(signal.confidence or 0.0)
        rr_ratio = float(signal.rr_ratio or 0.0)
        model_prob = signal.metadata.get("trained_model_prob")
        base_prob = float(model_prob) if model_prob is not None else confidence
        expected_edge = base_prob * (rr_ratio + 1.0) - 1.0
        abs_imbalance = abs(float(signal.metadata.get("normalized_imbalance", 0.0) or 0.0))
        htf_4h_trend = int(signal.metadata.get("htf_4h_trend", 0) or 0)
        side = str(signal.side or "").upper()
        entry_zone = str(signal.metadata.get("entry_zone", "no_zone")).lower()

        if confidence < self.quality_gate_min_confidence:
            return False, "low_confidence", {"quality_expected_edge": round(expected_edge, 4)}

        if htf_4h_trend != 0 and side in {"BUY", "SELL"}:
            is_countertrend = (side == "BUY" and htf_4h_trend < 0) or (side == "SELL" and htf_4h_trend > 0)
            if is_countertrend:
                if confidence < self.quality_gate_countertrend_min_confidence:
                    return False, "countertrend_low_confidence", {"quality_expected_edge": round(expected_edge, 4)}
                if abs_imbalance < self.quality_gate_countertrend_min_abs_imbalance:
                    return False, "countertrend_weak_imbalance", {"quality_expected_edge": round(expected_edge, 4)}

        if entry_zone == "no_zone" and confidence < self.quality_gate_no_zone_min_confidence:
            return False, "no_zone_low_confidence", {"quality_expected_edge": round(expected_edge, 4)}

        if getattr(self, "quality_gate_reject_no_zone_entries", False) and entry_zone == "no_zone":
            smc_score = float(signal.metadata.get("smc_score", 0.0) or 0.0)
            if confidence >= 0.85 and smc_score >= 0.85:
                logger.info(
                    f"[QUALITY_GATE] {symbol} no_zone BYPASSED (conf={confidence:.2f} smc={smc_score:.2f})"
                )
            else:
                return False, "no_zone_blocked", {"quality_expected_edge": round(expected_edge, 4)}

        if expected_edge < self.quality_gate_min_expected_edge:
            return False, "low_expected_edge", {"quality_expected_edge": round(expected_edge, 4)}

        if self.quality_gate_enabled:
            regime = str(signal.metadata.get("regime", "unknown")).lower()
            adx = float(signal.metadata.get("adx", 0.0) or 0.0)
            atr_pct = float(signal.metadata.get("atr_pct", 0.0) or 0.0)
            htf_trend = str(signal.metadata.get("htf_trend", "neutral")).lower()

            if not self.quality_gate_allow_chop and regime == "chop":
                return False, "chop_regime", {"quality_expected_edge": round(expected_edge, 4)}
            if adx < self.quality_gate_min_adx:
                return False, "low_adx", {"quality_expected_edge": round(expected_edge, 4)}
            if atr_pct < self.quality_gate_min_atr_pct:
                return False, "low_atr", {"quality_expected_edge": round(expected_edge, 4)}
            if abs_imbalance < self.quality_gate_min_abs_imbalance:
                return False, "flat_orderflow", {"quality_expected_edge": round(expected_edge, 4)}
            if self.quality_gate_require_htf_trend and htf_trend in {"neutral", "flat", "range", "sideways"}:
                return False, "flat_htf_trend", {"quality_expected_edge": round(expected_edge, 4)}

        return True, "ok", {"quality_expected_edge": round(expected_edge, 4), "quality_gate_symbol": symbol}

    @staticmethod
    def _zone_matches_side(zone_context, current_price: float, side: str) -> bool:
        if zone_context is None:
            return False
        side_up = str(side).upper()
        if side_up in {"BUY", "LONG"}:
            return (
                zone_context.price_in_bullish_zone(current_price) is not None
                or zone_context.price_near_bullish_zone(current_price, 0.4) is not None
            )
        return (
            zone_context.price_in_bearish_zone(current_price) is not None
            or zone_context.price_near_bearish_zone(current_price, 0.4) is not None
        )

    async def _update_correlation_cache(self, symbol: str):
        lookback = max(int(self.correlation_filter.lookback), 20)
        klines = await self.client.get_klines(symbol, self.candle_interval, lookback + 5)
        closes = [float(item.get("close", 0.0) or 0.0) for item in klines if float(item.get("close", 0.0) or 0.0) > 0]
        if len(closes) >= 10:
            self.correlation_filter.update_prices(symbol, closes)

    async def _passes_correlation_filter(self, symbol: str, same_side_symbols: list[str]) -> tuple[bool, str]:
        if not self.correlation_filter_enabled or not same_side_symbols:
            return True, ""
        try:
            await self._update_correlation_cache(symbol)
            for peer in same_side_symbols:
                await self._update_correlation_cache(peer)
            should_filter, reason = self.correlation_filter.should_filter(symbol, same_side_symbols)
            return (not should_filter), reason
        except Exception as exc:
            logger.warning(f"Correlation filter error for {symbol}: {exc}")
            return True, ""

    def _same_side_peer_symbols(self, side: str, candidates: list[dict]) -> list[str]:
        side_up = str(side).upper()
        peers = []
        for symbol in self.position_manager.symbols():
            pos = self.position_manager.get(symbol)
            if pos and str(pos.side).upper() == side_up:
                peers.append(symbol)
        for item in candidates:
            sig = item.get("signal")
            if sig and str(sig.side).upper() == side_up:
                peers.append(item.get("symbol", ""))
        return [s for s in self._unique_symbols(peers) if s]

    @staticmethod
    def _parse_iso_dt(value: str):
        try:
            dt = datetime.fromisoformat(str(value))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt
        except Exception:
            return None

    def _is_quality_feedback_record(self, record: dict) -> bool:
        if record.get("source") != "signal_only_feedback":
            return False
        if record.get("exit_reason") not in {"stop_loss", "take_profit"}:
            return False
        abs_pnl = abs(float(record.get("pnl_pct", 0.0) or 0.0))
        if abs_pnl < self.feedback_min_label_abs_pnl_pct:
            return False
        entry_dt = self._parse_iso_dt(record.get("entry_time"))
        exit_dt = self._parse_iso_dt(record.get("exit_time"))
        if not entry_dt or not exit_dt:
            return False
        hold_minutes = (exit_dt - entry_dt).total_seconds() / 60.0
        return hold_minutes >= self.feedback_min_label_hold_minutes

    def _build_retrain_dataset(self) -> Path:
        if not self.feedback_use_merged_dataset_for_retrain:
            return self.signal_feedback.dataset_path

        base_rows = []
        feedback_rows = []
        if self.feedback_base_dataset_path.exists():
            try:
                with open(self.feedback_base_dataset_path, "r", encoding="utf-8") as handle:
                    loaded = json.load(handle)
                    if isinstance(loaded, list):
                        base_rows = [row for row in loaded if row.get("result") in {"win", "loss"}]
            except Exception as exc:
                logger.warning(f"Failed to read base dataset for retrain: {exc}")

        if self.signal_feedback.dataset_path.exists():
            try:
                with open(self.signal_feedback.dataset_path, "r", encoding="utf-8") as handle:
                    loaded = json.load(handle)
                    if isinstance(loaded, list):
                        feedback_rows = [row for row in loaded if self._is_quality_feedback_record(row)]
            except Exception as exc:
                logger.warning(f"Failed to read feedback dataset for retrain: {exc}")

        merged = base_rows + feedback_rows
        output_path = BOT_DIR / "training_data_merged.json"
        with open(output_path, "w", encoding="utf-8") as handle:
            json.dump(merged, handle, ensure_ascii=False, indent=2)

        logger.info(
            f"[FEEDBACK] Retrain dataset prepared: base={len(base_rows)} "
            f"quality_feedback={len(feedback_rows)} total={len(merged)}"
        )
        return output_path

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
        # Quasi-liquidation model: estimate where leveraged positions get liquidated
        # based on ATR, typical leverage levels, and price structure
        quasi_liq = self._build_quasi_liquidation_model(klines, current_price)
        if quasi_liq.target_level > 0:
            logger.info(f"[HEATMAP] {symbol}: quasi-liquidation model (ATR+leverage zones)")
            return quasi_liq
        return LiquidationAnalysis([], [], None, None, 0.0, 0.0, "neutral", 0, 0.0)

    def _build_quasi_liquidation_model(self, klines: list[dict], current_price: float) -> LiquidationAnalysis:
        """Build quasi-liquidation heatmap from ATR + leverage zones.

        Logic (Coinglass-inspired):
        1. High leverage (50x-125x) traders get liquidated at 0.8-2% from entry
        2. Medium leverage (10x-25x) at 4-10% from entry
        3. Recent swing highs/lows act as entry clusters
        4. ATR defines the "heat zone" width
        """
        if len(klines) < 20 or current_price <= 0:
            return LiquidationAnalysis([], [], None, None, 0.0, 0.0, "neutral", 0, 0.0)

        highs = [float(k["high"]) for k in klines[-50:]]
        lows = [float(k["low"]) for k in klines[-50:]]

        # ATR approximation
        ranges = [high_val - low_val for high_val, low_val in zip(highs, lows)]
        atr = sum(ranges[-14:]) / min(14, len(ranges)) if ranges else current_price * 0.01

        # Leverage liquidation zones (% from current price)
        # 50x-125x leverage → liquidated at 0.8-2.0% move
        # 10x-25x leverage → liquidated at 4-10% move
        high_lev_dist = current_price * 0.012  # ~1.2% (50x zone)
        # Find recent swing highs/lows as entry clusters
        recent_swing_highs = sorted(highs[-20:], reverse=True)[:3]
        recent_swing_lows = sorted(lows[-20:])[:3]

        above_clusters = []  # Shorts' stop-losses above price (liquidity magnets for longs)
        below_clusters = []  # Longs' stop-losses below price (liquidity magnets for shorts)

        # High-leverage liquidation zone above (shorts getting squeezed)
        liq_above_50x = current_price + high_lev_dist
        above_clusters.append(LiquidationCluster(
            round(liq_above_50x, 8), round(atr * 2, 4), 1,
            round(high_lev_dist / current_price * 100, 4), "shorts_50x"
        ))

        # Swing high clusters (where shorts entered → their stops are above)
        for sh in recent_swing_highs:
            if sh > current_price:
                dist = sh - current_price
                dist_pct = dist / current_price * 100
                above_clusters.append(LiquidationCluster(
                    round(sh + atr * 0.3, 8), round(atr, 4), 1,
                    round(dist_pct, 4), "shorts_swing"
                ))

        # High-leverage liquidation zone below (longs getting liquidated)
        liq_below_50x = current_price - high_lev_dist
        below_clusters.append(LiquidationCluster(
            round(liq_below_50x, 8), round(atr * 2, 4), 1,
            round(high_lev_dist / current_price * 100, 4), "longs_50x"
        ))

        # Swing low clusters (where longs entered → their stops are below)
        for sl in recent_swing_lows:
            if sl < current_price:
                dist = current_price - sl
                dist_pct = dist / current_price * 100
                below_clusters.append(LiquidationCluster(
                    round(sl - atr * 0.3, 8), round(atr, 4), 1,
                    round(dist_pct, 4), "longs_swing"
                ))

        # Determine magnet direction: larger cluster = more liquidity = magnet
        above_total = sum(c.size for c in above_clusters)
        below_total = sum(c.size for c in below_clusters)

        if above_total > below_total * 1.3:
            # More liquidity above → price likely sweeps up
            target = max(above_clusters, key=lambda c: c.size)
            return LiquidationAnalysis(
                above_clusters, below_clusters, target, None,
                target.level, target.size, "up", 1, target.distance_pct
            )
        elif below_total > above_total * 1.3:
            # More liquidity below → price likely sweeps down
            target = max(below_clusters, key=lambda c: c.size)
            return LiquidationAnalysis(
                above_clusters, below_clusters, None, target,
                target.level, target.size, "down", -1, target.distance_pct
            )
        else:
            # Balanced
            return LiquidationAnalysis(
                above_clusters, below_clusters, None, None,
                0.0, 0.0, "neutral", 0, 0.0
            )

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

    def _heatmap_to_liq_analysis(self, current_price: float, heatmap, magnet_dir: str, magnet_target: float) -> LiquidationAnalysis:
        """Convert real orderbook heatmap into LiquidationAnalysis format."""
        if magnet_dir == "neutral" or magnet_target <= 0:
            return LiquidationAnalysis([], [], None, None, 0.0, 0.0, "neutral", 0, 0.0)

        distance_pct = abs(magnet_target - current_price) / current_price * 100 if current_price > 0 else 0.0
        density = heatmap.strongest_ask.volume if magnet_dir == "up" and heatmap.strongest_ask else (
            heatmap.strongest_bid.volume if magnet_dir == "down" and heatmap.strongest_bid else 1.0
        )

        if magnet_dir == "up":
            cluster = LiquidationCluster(round(magnet_target, 8), round(density, 4), 1, round(distance_pct, 4), "shorts")
            logger.info(f"[HEATMAP] real orderbook: bullish magnet @ {magnet_target:.2f} (vol={density:.2f})")
            return LiquidationAnalysis([cluster], [], cluster, None, cluster.level, cluster.size, "up", 1, cluster.distance_pct)
        else:
            cluster = LiquidationCluster(round(magnet_target, 8), round(density, 4), 1, round(distance_pct, 4), "longs")
            logger.info(f"[HEATMAP] real orderbook: bearish magnet @ {magnet_target:.2f} (vol={density:.2f})")
            return LiquidationAnalysis([], [cluster], None, cluster, cluster.level, cluster.size, "down", -1, cluster.distance_pct)


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
        self.exit_engine.initialize_position(adopted, atr_val, protective_liq_level=0.0)
        self._apply_manual_trailing_profile(adopted, atr_val)
        self._apply_profit_drawdown_profile(adopted)
        if not external_tp_locked and partial_tp > 0:
            adopted.partial_tp_price = partial_tp
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
        if pos.is_long:
            pos.trailing_activation_price = pos.entry_price + atr * self.manual_trailing_activation_atr
        else:
            pos.trailing_activation_price = pos.entry_price - atr * self.manual_trailing_activation_atr

    def _apply_profit_drawdown_profile(self, pos: Position):
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

    def _should_finalize_exchange_closed(self, symbol: str) -> bool:
        required = max(1, int(self.exchange_closed_confirm_cycles))
        seen = int(self._missing_exchange_cycles.get(symbol, 0)) + 1
        self._missing_exchange_cycles[symbol] = seen
        if seen < required:
            logger.info(
                f"[POSITION_SYNC] {symbol} missing on exchange ({seen}/{required}) — waiting confirm"
            )
            return False
        return True

    @staticmethod
    def _filter_recent_closed_pnl(closed_records: list | None, max_age_sec: int = 300) -> list:
        """Filter closedPnl records to only include those from the last max_age_sec seconds."""
        if not closed_records:
            return []
        now_ms = int(time.time() * 1000)
        cutoff_ms = now_ms - max_age_sec * 1000
        recent = []
        for record in closed_records:
            updated_time = int(record.get("updatedTime", 0) or record.get("createdTime", 0) or 0)
            if updated_time >= cutoff_ms:
                recent.append(record)
        return recent

    def _can_finalize_exchange_closed(self, missing_cycles: int, closed_records_count: int) -> bool:
        if not self.exchange_closed_require_closed_pnl:
            return True
        if closed_records_count > 0:
            return True
        return missing_cycles >= max(1, int(self.exchange_closed_force_cycles))

    def _set_exchange_closed_reentry_block(self, symbol: str):
        cooldown = max(0, int(self.exchange_closed_reentry_cooldown_sec))
        if cooldown <= 0:
            return
        self._exchange_closed_reentry_until[symbol] = time.time() + cooldown

    def _exchange_closed_reentry_remaining(self, symbol: str) -> int:
        until = self._exchange_closed_reentry_until.get(symbol)
        if not until:
            return 0
        remaining = int((until - time.time()) + 0.999)
        if remaining <= 0:
            self._exchange_closed_reentry_until.pop(symbol, None)
            return 0
        return remaining

    def _exchange_closed_sync_pause_remaining(self) -> int:
        cooldown = max(0, int(self.exchange_closed_pause_after_rate_limit_sec))
        if cooldown <= 0:
            return 0
        last_at = float(getattr(self.client, "last_rate_limit_at_monotonic", 0.0) or 0.0)
        if last_at <= 0:
            return 0
        remaining = int((last_at + cooldown - time.monotonic()) + 0.999)
        return remaining if remaining > 0 else 0

    def _should_scan_entries_now(self) -> bool:
        interval = max(5, int(self.scan_interval_sec))
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
