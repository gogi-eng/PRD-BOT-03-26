"""AUTO-GENERATED aggregate of TradingBot sources for static tests; do not edit.
Real entry: repo root ``main.py``, implementation: ``bot/trading_bot.py`` + ``bot/mixins/``.
"""
from __future__ import annotations
from typing import Optional

# === bot\trading_bot.py ===
from bot.state import BasketProfitState
from bot.trading_bot_imports import *  # noqa: F403

from bot.mixins.helpers_mixin import TradingBotHelpersMixin
from bot.mixins.regime_mixin import TradingBotRegimeMixin
from bot.mixins.notify_symbols_mixin import TradingBotNotifySymbolsMixin
from bot.mixins.lifecycle_mixin import TradingBotLifecycleMixin
from bot.mixins.position_loop_mixin import TradingBotPositionLoopMixin
from bot.mixins.scanning_mixin import TradingBotScanningMixin
from bot.mixins.analyze_entry_mixin import TradingBotAnalyzeEntryMixin
from bot.mixins.entry_exec_mixin import TradingBotEntryExecMixin
from bot.mixins.correlation_mixin import TradingBotCorrelationMixin
from bot.mixins.feedback_mixin import TradingBotFeedbackMixin
from bot.mixins.liquidation_mixin import TradingBotLiquidationMixin
from bot.mixins.sync_manual_mixin import TradingBotSyncManualMixin
from bot.mixins.guards_mixin import TradingBotGuardsMixin
from bot.mixins.closes_mixin import TradingBotClosesMixin
from bot.mixins.exchange_closed_mixin import TradingBotExchangeClosedMixin


class TradingBot(
    TradingBotHelpersMixin,
    TradingBotRegimeMixin,
    TradingBotNotifySymbolsMixin,
    TradingBotLifecycleMixin,
    TradingBotPositionLoopMixin,
    TradingBotScanningMixin,
    TradingBotAnalyzeEntryMixin,
    TradingBotEntryExecMixin,
    TradingBotCorrelationMixin,
    TradingBotFeedbackMixin,
    TradingBotLiquidationMixin,
    TradingBotSyncManualMixin,
    TradingBotGuardsMixin,
    TradingBotClosesMixin,
    TradingBotExchangeClosedMixin,
):
    """Main trading bot orchestrator."""

    def __init__(self):
        load_dotenv(override=True)
        _bd = resolve_bot_dir()
        self.cfg = BotConfig.load(str(_bd / "config.yaml"))
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
            symbol_loss_streak_cooldown_enabled=bool(
                self.cfg.get("risk", "symbol_loss_streak_cooldown_enabled", default=True)
            ),
            symbol_loss_streak_threshold=int(
                self.cfg.get("risk", "symbol_loss_streak_limit", default=2)
            ),
            symbol_loss_streak_cooldown_count=int(
                self.cfg.get("risk", "symbol_loss_streak_limit", default=2)
            ),
            symbol_loss_streak_cooldown_sec=int(
                self.cfg.get("risk", "symbol_loss_streak_cooldown_sec", default=21600)
            ),
            trend_exit_reentry_cooldown_enabled=bool(
                self.cfg.get("risk", "trend_exit_reentry_cooldown_enabled", default=False)
            ),
            trend_exit_reentry_cooldown_sec=int(
                self.cfg.get("risk", "trend_exit_reentry_cooldown_sec", default=0)
            ),
            trend_exit_reentry_loss_only=bool(
                self.cfg.get("risk", "trend_exit_reentry_loss_only", default=True)
            ),
            early_exit_reentry_cooldown_enabled=bool(
                self.cfg.get("risk", "early_exit_reentry_cooldown_enabled", default=False)
            ),
            early_exit_reentry_cooldown_sec=int(
                self.cfg.get("risk", "early_exit_reentry_cooldown_sec", default=0)
            ),
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
        scalp_cfg = self.cfg.get("scalp", default={}) or {}
        if "timezone_offset" not in scalp_cfg:
            scalp_cfg["timezone_offset"] = int(self.cfg.get("timezone_offset", default=3))
        self.scalp_strategy = ScalpSessionStrategy(config=scalp_cfg, debug=False)
        self.structure_zone_analyzer = StructureZoneAnalyzer()
        self.liquidity_heatmap = LiquidityHeatmap(depth_levels=200)
        self.market_structure_engine = MarketStructureEngine(
            swing_lookback=self.cfg.get("market_structure", "swing_lookback", default=2),
            volume_spike_mult=self.cfg.get("market_structure", "volume_spike_mult", default=2.0),
            bos_volume_mult=self.cfg.get("market_structure", "bos_volume_mult", default=1.5),
            spread_expansion_mult=self.cfg.get("market_structure", "spread_expansion_mult", default=1.5),
        )
        self.ai_analyzer = AITradeAnalyzer(self.cfg)
        self.ai_analyzer.min_confidence = self.cfg.get("ai", "min_confidence", default=60)
        self.ai_analyzer.fail_open = self.cfg.get("ai", "fail_open", default=True)
        self.ai_analyzer.require_direction_match = self.cfg.get("ai", "require_direction_match", default=True)
        self.ai_analyzer.uniformity_guard_enabled = self.cfg.get("ai", "uniformity_guard_enabled", default=True)
        self.ai_analyzer.uniformity_window = int(self.cfg.get("ai", "uniformity_window", default=8))
        self.ai_analyzer.uniformity_conf_spread_max = int(
            self.cfg.get("ai", "uniformity_conf_spread_max", default=3)
        )
        self.ai_claude_enabled = bool(self.cfg.get("ai_claude", "enabled", default=False))
        self.ai_claude_engine = AIDecisionEngine(self.cfg)
        self.advisor = LocalTradingAdvisor(self.cfg.get("advisor", default={}) or {})
        self.atr = ATRCalculator(period=self.cfg.get("atr", "period", default=14))

        self.entry_engine = EntryEngine(self.cfg)
        from engine.bpr_ranker import BPRLinearRanker

        _bpr_cfg = self.cfg.get("bpr_ranker", default={}) or {}
        self.bpr_ranker = BPRLinearRanker(
            enabled=bool(_bpr_cfg.get("enabled", True)),
            weights_path=str(_bpr_cfg.get("weights_path", "bpr_weights.json")),
            blend_weight=float(_bpr_cfg.get("blend_weight", 0.35)),
            top1_when_multiple=bool(_bpr_cfg.get("top1_when_multiple", True)),
            telegram_top_n=int(_bpr_cfg.get("telegram_top_n", 0)),
            bot_dir=_bd,
        )
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
            early_exit_min_hold_minutes=float(
                self.cfg.get("exit", "early_exit_min_hold_minutes", default=0.0)
            ),
            early_exit_session_utc_hours=list(
                self.cfg.get("exit", "early_exit_session_utc_hours", default=[])
                or []
            ),
            early_exit_session_min_profit_atr_boost=float(
                self.cfg.get("exit", "early_exit_session_min_profit_atr_boost", default=0.0)
            ),
            early_exit_allow_loss_close=bool(
                self.cfg.get("exit", "early_exit_allow_loss_close", default=False)
            ),
            early_exit_validator_enabled=bool(
                self.cfg.get("exit", "early_exit_validator_enabled", default=True)
            ),
            trailing_activation_atr=self.cfg.get("exit", "trailing_activation_atr", default=0.8),
            trailing_distance_atr=self.cfg.get("exit", "trailing_distance_atr", default=1.2),
            trailing_min_distance_from_price_pct=float(
                self.cfg.get("exit", "trailing_min_distance_pct", default=0.0)
            ),
            tp_cap_atr_mult=self.cfg.get("exit", "tp_cap_atr_mult", default=8.0),
            min_profit_before_trail_pct=self.cfg.get("exit", "min_profit_before_trail_pct", default=0.5),
            trailing_structural_r_threshold=float(
                self.cfg.get("exit", "trailing_structural_r_threshold", default=2.0)
            ),
            trailing_swing_buffer_atr_mult=float(
                self.cfg.get("exit", "trailing_swing_buffer_atr_mult", default=0.0)
            ),
            sl_buffer_atr_mult=self.cfg.get("exit", "sl_buffer_atr_mult", default=0.2),
            fee_rate=float(self.cfg.get("exit", "fee_rate", default=0.0006)),
            ema_exit_buffer_pct=float(self.cfg.get("exit", "ema_exit_buffer_pct", default=0.0)),
            ema_trend_exit_confirm_bars=int(
                self.cfg.get("exit", "ema_trend_exit_confirm_bars", default=2)
            ),
            ema_trend_exit_require_slope=bool(
                self.cfg.get("exit", "ema_trend_exit_require_ema_slope", default=True)
            ),
            ema_trend_exit_recovery_cancel_enabled=bool(
                self.cfg.get("exit", "ema_trend_exit_recovery_cancel_enabled", default=True)
            ),
            ema_trend_exit_recovery_lookback_bars=int(
                self.cfg.get("exit", "ema_trend_exit_recovery_lookback_bars", default=6)
            ),
            ema_trend_exit_recovery_min_ratio=float(
                self.cfg.get("exit", "ema_trend_exit_recovery_min_ratio", default=0.45)
            ),
            ema_trend_exit_recovery_min_adverse_pct=float(
                self.cfg.get("exit", "ema_trend_exit_recovery_min_adverse_pct", default=0.35)
            ),
            ema_exit_min_move_from_entry_pct=float(
                self.cfg.get("exit", "ema_exit_min_adverse_from_entry_pct", default=0.0)
            ),
        )
        # EMA trend exit config
        self.ema_trend_exit_enabled = self.cfg.get("exit", "ema_trend_exit", default=False)
        self.ema_exit_period = int(self.cfg.get("exit", "ema_exit_period", default=20))
        # Fee rate for PnL calculation
        self.fee_rate = float(self.cfg.get("exit", "fee_rate", default=0.001))

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
        self._running = False
        self._stop_event = threading.Event()
        self.candle_interval = self.cfg.get("bot", "candle_interval", default="1")
        self.htf_interval = self.cfg.get("bot", "htf_interval", default="15")
        self.htf_4h_interval = self.cfg.get("bot", "htf_4h_interval", default="240")
        self.cycle_sleep = self.cfg.get("bot", "cycle_sleep_sec", default=45)
        self.scan_interval_sec = int(self.cfg.get("bot", "scan_interval_sec", default=self.cycle_sleep))
        self.position_active_sleep_sec = int(self.cfg.get("bot", "position_active_sleep_sec", default=15))
        self._last_scan_ts = 0.0
        # Skip re-analyzing the same symbol too often to reduce Bybit API load.
        self.min_symbol_rescan_sec = float(
            self.cfg.get("bot", "min_symbol_rescan_sec", default=90)
        )
        self._last_symbol_scan_ts: dict[str, float] = {}
        self.feature_window = self.cfg.get("bot", "feature_window", default=128)
        self.klines_limit = max(self.cfg.get("bot", "klines_limit", default=180), self.feature_window)

        # TF preset name (applied after all other config reads)
        self._active_tf_preset = self.cfg.get("tf_presets", "active_preset", default="")
        self.signal_only = self.cfg.get("bot", "signal_only", default=False)
        self.controls.signal_only = self.signal_only
        self.signal_cooldown_sec = int(self.cfg.get("bot", "signal_cooldown_sec", default=3600) or 0)
        self.signal_only_min_confidence = float(
            self.cfg.get("bot", "signal_only_min_confidence", default=0.90)
        )
        # Optional faster entry-scan interval during active scalp hours.
        self.scan_interval_active_hours_sec = int(
            self.cfg.get("bot", "scan_interval_active_hours_sec", default=self.scan_interval_sec)
            or self.scan_interval_sec
        )
        self._last_signal_ts: dict[tuple[str, str], float] = {}
        self.signal_feedback = SignalFeedbackLoop(_bd, self.cfg)
        # Connect signal_feedback to Telegram controller (must be after signal_feedback creation)
        if self.tg:
            self.tg.set_profit_lock(self.profit_lock)
            self.tg.set_signal_feedback(self.signal_feedback)
            self.tg.set_bot_instance(self)
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
        self.quality_gate_chop_bypass_enabled = bool(
            self.cfg.get("quality_gate", "chop_bypass_enabled", default=True)
        )
        self.quality_gate_chop_bypass_min_confidence = float(
            self.cfg.get("quality_gate", "chop_bypass_min_confidence", default=0.78)
        )
        self.quality_gate_chop_bypass_min_abs_imbalance = float(
            self.cfg.get("quality_gate", "chop_bypass_min_abs_imbalance", default=0.12)
        )
        self.quality_gate_chop_bypass_require_zone = bool(
            self.cfg.get("quality_gate", "chop_bypass_require_zone", default=True)
        )
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
        self.quality_gate_strong_signal_min_confidence = float(
            self.cfg.get("quality_gate", "strong_signal_min_confidence", default=0.85)
        )
        self.quality_gate_strong_signal_min_smc = float(
            self.cfg.get("quality_gate", "strong_signal_min_smc", default=0.85)
        )
        # Additional entry hard-gates (trade quality protection)
        self.entry_min_orderflow_imbalance = float(
            self.cfg.get("entry", "min_orderflow_imbalance", default=1.20)
        )
        # Config can be ratio-style (e.g. 1.20) while runtime imbalance is normalized [-1..1].
        # Convert ratio -> normalized: n = (r - 1) / (r + 1), so 1.20 -> ~0.091.
        if self.entry_min_orderflow_imbalance > 1.0:
            self.entry_min_orderflow_imbalance_norm = max(
                0.0,
                min(
                    1.0,
                    (self.entry_min_orderflow_imbalance - 1.0)
                    / (self.entry_min_orderflow_imbalance + 1.0),
                ),
            )
        else:
            self.entry_min_orderflow_imbalance_norm = max(
                0.0, min(1.0, self.entry_min_orderflow_imbalance)
            )
        self.entry_min_smc_score = float(
            self.cfg.get("entry", "min_smc_score", default=0.76)
        )
        self.entry_min_volatility_pct = float(
            self.cfg.get("entry", "min_volatility_pct", default=0.08)
        )
        self.entry_require_sweep = bool(
            self.cfg.get("entry", "require_sweep", default=True)
        )
        self.entry_require_4h_trend_base = bool(
            self.cfg.get("entry", "require_4h_trend", default=True)
        )
        self.entry_require_4h_trend = self.entry_require_4h_trend_base
        self.entry_missing_bos_min_confidence = float(
            self.cfg.get(
                "entry",
                "missing_bos_min_confidence",
                default=self.cfg.get(
                    "entry",
                    "hardgate_missing_bos_bypass_confidence",
                    default=0.90,
                ),
            )
        )
        # Late-entry guard: avoid buying near local highs / selling near local lows.
        self.entry_peak_reversal_guard = bool(
            self.cfg.get("entry", "anti_peak_guard_enabled", default=True)
        )
        self.entry_peak_lookback_bars = int(
            self.cfg.get("entry", "anti_peak_lookback", default=24)
        )
        self.entry_peak_distance_atr = float(
            self.cfg.get("entry", "anti_peak_atr_buffer_mult", default=0.35)
        )
        self.entry_peak_confidence_bypass = float(
            self.cfg.get("entry", "peak_confidence_bypass", default=0.87)
        )
        # Impulse -> retest -> confirmation guard for more precise entries.
        self.entry_impulse_retest_confirm_enabled = bool(
            self.cfg.get(
                "entry",
                "impulse_retest_confirm_enabled",
                default=self.cfg.get("entry", "impulse_retest_confirmation_enabled", default=True),
            )
        )
        self.entry_impulse_min_body_atr = float(
            self.cfg.get(
                "entry",
                "impulse_min_body_atr",
                default=self.cfg.get("entry", "impulse_retest_impulse_atr_mult", default=0.8),
            )
        )
        self.entry_retest_max_body_atr = float(
            self.cfg.get(
                "entry",
                "retest_max_body_ratio",
                default=self.cfg.get("entry", "impulse_retest_retest_atr_mult", default=0.35),
            )
        )
        self.entry_confirm_min_body_ratio = float(
            self.cfg.get("entry", "confirm_min_body_ratio", default=0.0)
        )
        self.entry_impulse_confirm_conf_bypass = float(
            self.cfg.get("entry", "impulse_confirm_confidence_bypass", default=0.88)
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
        # SCALP-specific entry unblocking knobs (kept separate from global filters).
        self.scalp_orderflow_hardgate_mult = max(
            0.0,
            min(1.0, float(self.cfg.get("scalp", "hardgate_orderflow_relax_mult", default=1.0))),
        )
        self.scalp_orderflow_hardgate_floor = max(
            0.0,
            min(1.0, float(self.cfg.get("scalp", "orderflow_hardgate_floor", default=0.0))),
        )
        self.scalp_skip_orderbook_direction_guard = bool(
            self.cfg.get("scalp", "skip_orderbook_direction_guard", default=False)
        )
        self.scalp_skip_impulse_retest_guard = bool(
            self.cfg.get("scalp", "skip_impulse_retest_guard", default=False)
        )
        self.scalp_impulse_retest_bypass_confidence = max(
            0.0,
            min(1.0, float(self.cfg.get("scalp", "impulse_retest_bypass_confidence", default=1.1))),
        )
        self.scalp_quality_min_confidence = max(
            0.0,
            min(
                1.0,
                float(
                    self.cfg.get(
                        "scalp",
                        "quality_min_confidence",
                        default=self.cfg.get(
                            "scalp",
                            "quality_gate_bypass_confidence",
                            default=self.quality_gate_min_confidence,
                        ),
                    )
                ),
            ),
        )
        self.scalp_quality_min_expected_edge = float(
            self.cfg.get(
                "scalp",
                "quality_min_expected_edge",
                default=self.cfg.get(
                    "scalp",
                    "quality_gate_min_expected_edge",
                    default=self.quality_gate_min_expected_edge,
                ),
            )
        )
        self.scalp_mtf_single_tf_min_confidence = max(
            0.0,
            min(
                1.0,
                float(
                    self.cfg.get(
                        "scalp",
                        "mtf_single_tf_min_confidence",
                        default=self.mtf_zone_min_confidence_if_single_tf,
                    )
                ),
            ),
        )
        self.symbol_quality_filter = SymbolQualityFilter(_bd, self.cfg)
        self.feedback_use_merged_dataset_for_retrain = self.cfg.get(
            "feedback_loop", "use_merged_dataset_for_retrain", default=True
        )
        self.feedback_apply_to_risk_guard = self.cfg.get(
            "feedback_loop", "apply_to_risk_guard", default=False
        )
        self.feedback_base_dataset_path = _bd / self.cfg.get(
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
        self.adaptive_trend_require_4h_trend = bool(
            self.cfg.get(
                "adaptive_regime_presets",
                "trend_require_4h_trend",
                default=self.entry_require_4h_trend_base,
            )
        )
        self.adaptive_range_strict_htf_mode = self.cfg.get(
            "adaptive_regime_presets", "range_strict_htf_mode", default=True
        )
        self.adaptive_range_volatility_floor_atr_pct = float(
            self.cfg.get("adaptive_regime_presets", "range_volatility_floor_atr_pct", default=1.0)
        )
        self.adaptive_range_require_4h_trend = bool(
            self.cfg.get(
                "adaptive_regime_presets",
                "range_require_4h_trend",
                default=False,
            )
        )
        self._last_regime_profile_check_ts = 0.0
        self._active_regime_profile = "manual"
        self.min_volume = self.cfg.get("market", "min_24h_volume_usdt", default=15_000_000)
        self.max_symbols = self.cfg.get("market", "max_symbols", default=15)
        self.trade_symbols = self.cfg.get("market", "trade_symbols", default=5)
        self.whitelist_enabled = self.cfg.get("market", "whitelist_enabled", default=True)
        self.whitelist_only = self.cfg.get("market", "whitelist_only", default=False)
        self.whitelist = self.cfg.get("market", "whitelist_symbols", default=[])
        self.blacklist = self.cfg.get("trading", "blacklist_symbols", default=[])
        self.blacklist_substrings = self.cfg.get("market", "blacklist_substrings", default=[])
        self.block_entry_utc_hours = {
            int(h) % 24
            for h in (self.cfg.get("trading", "block_entry_utc_hours", default=[]) or [])
            if str(h).strip() != ""
        }
        self.min_position_usdt = self.cfg.get("trading", "min_position_usdt", default=5.0)
        self.position_size_mode = str(
            self.cfg.get("trading", "position_size_mode", default="risk")
        ).strip().lower()
        self.entry_capital_weight_mode = str(
            self.cfg.get(
                "trading",
                "entry_capital_weight_mode",
                default=self.cfg.get("trading", "capital_weight_mode", default="equal"),
            )
        ).strip().lower()
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
        self._last_exchange_close_meta: dict[str, dict] = {}
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
        self.session_flatten_enabled = bool(
            self.cfg.get("session_flatten", "enabled", default=False)
        )
        self.session_flatten_utc_hours = sorted(
            {int(x) % 24 for x in self.cfg.get("session_flatten", "utc_hours", default=[])}
        )
        self.session_flatten_lead_minutes = max(
            1, int(self.cfg.get("session_flatten", "lead_minutes", default=10))
        )
        self._session_flatten_last_key = ""
        self.profit_drawdown_guard_enabled = self.cfg.get("profit_drawdown_guard", "enabled", default=True)
        self.profit_drawdown_activation_pct = self.cfg.get("profit_drawdown_guard", "activation_profit_pct", default=3.0)
        self.profit_drawdown_retrace_pct = self.cfg.get("profit_drawdown_guard", "retrace_from_peak_pct", default=25.0)
        self.profit_drawdown_retrace_confirm_sec = float(
            self.cfg.get("profit_drawdown_guard", "retrace_confirm_sec", default=0.0)
        )
        self.profit_drawdown_require_trend_break = bool(
            self.cfg.get("profit_drawdown_guard", "require_trend_break", default=True)
        )
        self.profit_drawdown_trend_ema_fast = int(
            self.cfg.get("profit_drawdown_guard", "trend_ema_fast", default=20)
        )
        self.profit_drawdown_trend_ema_slow = int(
            self.cfg.get("profit_drawdown_guard", "trend_ema_slow", default=50)
        )
        self.profit_drawdown_pullback_analysis_enabled = bool(
            self.cfg.get("profit_drawdown_guard", "pullback_analysis_enabled", default=True)
        )
        self.profit_drawdown_pullback_lookback_bars = int(
            self.cfg.get("profit_drawdown_guard", "pullback_lookback_bars", default=60)
        )
        self.profit_drawdown_pullback_min_adverse_pct = float(
            self.cfg.get("profit_drawdown_guard", "pullback_min_adverse_pct", default=2.5)
        )
        self.profit_drawdown_pullback_cancel_recovery_ratio = float(
            self.cfg.get("profit_drawdown_guard", "pullback_cancel_recovery_ratio", default=0.35)
        )
        self.profit_drawdown_pullback_max_range_pct = float(
            self.cfg.get("profit_drawdown_guard", "pullback_max_range_pct", default=9.0)
        )
        self.manual_rl_enabled = self.cfg.get("manual_management", "rl_enabled", default=False)
        self.manual_preserve_existing_tp = self.cfg.get("manual_management", "preserve_existing_tp", default=True)
        self.manual_trailing_activation_atr = self.cfg.get("manual_management", "trailing_activation_atr", default=1.6)
        self.manual_trailing_distance_atr = self.cfg.get("manual_management", "trailing_distance_atr", default=2.4)
        self.manual_trailing_min_distance_pct = float(
            self.cfg.get("manual_management", "trailing_min_distance_pct", default=0.35)
        )
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

        # Apply TF preset overrides (LAST, to override all individual settings)
        self._apply_tf_preset()




# === bot\mixins\analyze_entry_mixin.py ===
from bot.trading_bot_imports import *  # noqa: F401,F403

class TradingBotAnalyzeEntryMixin:
    async def _analyze_symbol(self, symbol: str) -> EntrySignal:
        def reject(reason: str) -> EntrySignal:
            signal = EntrySignal()
            signal.metadata["reject_reason"] = reason
            return signal

        def build_scalp_signal(
            side: str,
            confidence: float,
            reason: str,
            current_price: float,
            atr_value: float,
            market,
            orderflow,
            htf_4h_trend: int,
        ) -> EntrySignal:
            sig = EntrySignal()
            sig.should_enter = True
            sig.side = side
            sig.entry_price = current_price

            stop_mult = 1.6
            tp_mult = 3.2
            if side == "BUY":
                sig.stop_loss = max(0.0, current_price - atr_value * stop_mult)
                sig.take_profit = current_price + atr_value * tp_mult
            else:
                sig.stop_loss = current_price + atr_value * stop_mult
                sig.take_profit = max(0.0, current_price - atr_value * tp_mult)

            risk = abs(current_price - sig.stop_loss)
            reward = abs(sig.take_profit - current_price)
            sig.rr_ratio = round((reward / risk) if risk > 0 else 0.0, 2)
            sig.confidence = round(max(0.0, min(1.0, confidence)), 4)
            sig.capital_score = round(sig.confidence * max(sig.rr_ratio, 0.0), 4)
            sig.grade = "B" if sig.confidence >= 0.8 else "C"
            sig.reasons = ["SCALP_SESSION", reason]

            norm_imb = float(getattr(orderflow, "normalized_imbalance", 0.0) or 0.0)
            liq_distance = 0.0
            if current_price > 0 and sig.take_profit > 0:
                liq_distance = abs(sig.take_profit - current_price) / current_price * 100.0

            sig.metadata = {
                "strategy": "scalp_session",
                "scalp": True,
                "composite_score": sig.confidence,
                "smc_score": sig.confidence,
                "trend_score": round(abs(norm_imb), 3),
                "orderflow_score": round(abs(norm_imb), 3),
                "ai_score": round(sig.confidence, 3),
                "normalized_imbalance": norm_imb,
                "target_level": sig.take_profit,
                "protective_liq_level": sig.stop_loss,
                "transformer_prob_up": sig.confidence if side == "BUY" else max(0.0, 1.0 - sig.confidence),
                "transformer_prob_down": sig.confidence if side == "SELL" else max(0.0, 1.0 - sig.confidence),
                "transformer_prob_flat": 0.0,
                "regime": market.regime.value,
                "spread_pct": float(getattr(orderflow, "spread_pct", 0.0) or 0.0),
                "liq_distance_pct": round(liq_distance, 4),
                "liq_signal": 1 if side == "BUY" else -1,
                "liq_magnet": "bullish" if side == "BUY" else "bearish",
                "tp1_level": sig.take_profit,
                "tp2_level": sig.take_profit,
                "tp_confirmed_by_structure": False,
                "entry_zone": "scalp_session",
                "struct_trend": "up" if side == "BUY" else "down",
                "has_bos": True,
                "has_sweep": True,
                "sweep_direction": "down" if side == "BUY" else "up",
                "bos_direction": "up" if side == "BUY" else "down",
                "funding_rate": 0.0,
                "htf_4h_trend": htf_4h_trend,
                "trained_model_prob": None,
                "trained_model_applied": False,
                "blended_confidence": sig.confidence,
                "entry_range_low": current_price,
                "entry_range_high": current_price,
                "signal_grade": sig.grade,
                "orderflow_bullish_ratio": float(getattr(orderflow, "bullish_ratio", 1.0) or 1.0),
                "orderflow_bearish_ratio": float(getattr(orderflow, "bearish_ratio", 1.0) or 1.0),
                "adx": float(getattr(market, "adx", 0.0) or 0.0),
                "trend": market.trend.name.lower(),
                "htf_trend": market.htf_trend.name.lower(),
                "atr_pct": float(getattr(market, "atr_pct", 0.0) or 0.0),
            }
            return sig

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
        # HTF ATR floor: use max of 1m ATR and HTF ATR to prevent micro-stops
        htf_atr_val = self.atr.get_atr(f"{symbol}_htf", htf_klines)
        if htf_atr_val > 0:
            atr_val = max(atr_val, htf_atr_val)
        current_price = float(klines[-1]["close"])

        # Market Structure: swings, BOS, sweeps, momentum
        structure = self.market_structure_engine.analyze(klines, atr_val)

        # Reuse one deep orderbook snapshot for both orderflow and heatmap
        # to reduce API pressure on Bybit.
        orderbook = await self.client.get_orderbook(symbol, limit=200)
        trades = await self.client.get_recent_trades(symbol, limit=120)
        orderflow = self.orderflow_analyzer.analyze(orderbook, trades)

        signal = None
        scalp_result = self.scalp_strategy.analyze(symbol, klines)
        if scalp_result:
            scalp_side = str(scalp_result.get("signal", "")).upper()
            if scalp_side in {"BUY", "SELL"}:
                htf_ok, htf_reason = self._passes_strict_htf_mode(scalp_side, htf_4h_trend)
                if not htf_ok:
                    return reject(htf_reason)
                scalp_signal = build_scalp_signal(
                    side=scalp_side,
                    confidence=float(scalp_result.get("confidence", 0.0) or 0.0),
                    reason=str(scalp_result.get("reason", "SCALP session signal")),
                    current_price=current_price,
                    atr_value=atr_val if atr_val > 0 else current_price * 0.008,
                    market=market,
                    orderflow=orderflow,
                    htf_4h_trend=htf_4h_trend,
                )
                logger.info(
                    f"SCALP SIGNAL {symbol}: {scalp_signal.side} conf={scalp_signal.confidence:.0%} "
                    f"RR={scalp_signal.rr_ratio:.1f} reason={scalp_signal.reasons[-1]}"
                )
                # SCALP is no longer an unconditional fast-path.
                # It must pass the same quality hard-gates as regular signals.
                signal = scalp_signal

        # Real orderbook-based heatmap (replaces synthetic fallback)
        heatmap = self.liquidity_heatmap.build_heatmap(orderbook)
        magnet_dir, magnet_target = self.liquidity_heatmap.get_liquidity_magnet(current_price, heatmap)

        liq = self._resolve_liquidation_context(symbol, current_price, klines)

        def _liq_target_level(x) -> float:
            try:
                return float(getattr(x, "target_level", 0.0) or 0.0)
            except (TypeError, ValueError):
                return 0.0

        if _liq_target_level(liq) <= 0:
            # Use real heatmap data before falling back to synthetic
            liq = self._heatmap_to_liq_analysis(current_price, heatmap, magnet_dir, magnet_target)
        if _liq_target_level(liq) <= 0:
            liq = self._build_directional_liq_fallback(current_price, market, orderflow, atr_val)
        self.controls.set_heatmap(symbol, liq)

        zone_context = self.structure_zone_analyzer.analyze(htf_klines, current_price)
        zone_context_4h = self.structure_zone_analyzer.analyze(htf_4h_klines, current_price)

        regime = self.regime_ai.classify(market)
        features = self.feature_engineer.build(klines, orderflow, liq, atr_val)
        transformer = self.transformer_model.predict(features, regime, orderflow, liq)

        # Get funding rate (one symbol — avoid get_tickers() full list per scanned coin)
        funding_rate = 0.0
        try:
            t = await self.client.get_ticker(symbol)
            if t:
                funding_rate = float(t.get("fundingRate", 0) or 0)
        except Exception:
            pass

        if signal is None:
            signal = self.entry_engine.generate_signal(
                symbol, klines, current_price, market, regime, transformer, orderflow, liq,
                atr_val, zone_context=zone_context, structure=structure, funding_rate=funding_rate,
                htf_4h_trend=htf_4h_trend,
            )
        if not signal.should_enter:
            signal.metadata.setdefault("reject_reason", "entry_filters")
            return signal

        # =====================================================
        # ENTRY HARD-GATES (for both regular and SCALP signals)
        # =====================================================
        confidence = float(signal.confidence or 0.0)
        smc_score = float(signal.metadata.get("smc_score", 0.0) or 0.0)
        norm_imb_signed = float(signal.metadata.get("normalized_imbalance", 0.0) or 0.0)
        norm_imb = abs(norm_imb_signed)
        atr_pct = self._resolve_signal_atr_pct(signal, market)
        has_sweep = bool(signal.metadata.get("has_sweep", False))
        has_bos = bool(signal.metadata.get("has_bos", False))
        side_up = str(signal.side or "").upper()
        is_scalp_signal = str(signal.metadata.get("strategy", "")).lower() == "scalp_session"
        required_orderflow_norm = self.entry_min_orderflow_imbalance_norm
        if is_scalp_signal:
            required_orderflow_norm = max(
                self.scalp_orderflow_hardgate_floor,
                self.entry_min_orderflow_imbalance_norm * self.scalp_orderflow_hardgate_mult,
            )

        if smc_score + 1e-9 < self.entry_min_smc_score:
            return reject(f"entry_hardgate_low_smc ({smc_score:.3f} < {self.entry_min_smc_score:.3f})")
        if norm_imb + 1e-9 < required_orderflow_norm:
            return reject(
                f"entry_hardgate_weak_orderflow ({norm_imb:.3f} < {required_orderflow_norm:.3f})"
            )
        if side_up in {"BUY", "LONG"} and norm_imb_signed + 1e-9 < required_orderflow_norm:
            return reject(
                f"entry_hardgate_wrong_orderflow_direction (BUY but imb={norm_imb_signed:+.3f} "
                f"< +{required_orderflow_norm:.3f})"
            )
        if side_up in {"SELL", "SHORT"} and norm_imb_signed - 1e-9 > -required_orderflow_norm:
            return reject(
                f"entry_hardgate_wrong_orderflow_direction (SELL but imb={norm_imb_signed:+.3f} "
                f"> -{required_orderflow_norm:.3f})"
            )
        if atr_pct + 1e-9 < self.entry_min_volatility_pct:
            return reject(
                f"entry_hardgate_low_volatility ({atr_pct:.3f}% < {self.entry_min_volatility_pct:.3f}%)"
            )
        if self.entry_require_sweep and (not has_sweep):
            return reject("entry_hardgate_missing_sweep")
        if not has_bos and confidence < self.entry_missing_bos_min_confidence:
            return reject("entry_hardgate_missing_bos")
        if self.entry_require_4h_trend and htf_4h_trend == 0:
            return reject("entry_hardgate_flat_4h")

        # =====================================================
        # ORDERBOOK DIRECTION GUARD:
        # Reject if orderbook volume contradicts signal direction
        # SELL blocked when bid_vol >> ask_vol (buyers dominate)
        # BUY blocked when ask_vol >> bid_vol (sellers dominate)
        # =====================================================
        ob_bid_vol = getattr(orderflow, 'bid_volume', 0)
        ob_ask_vol = getattr(orderflow, 'ask_volume', 0)
        is_long_sig = signal.side.upper() in ("BUY", "LONG")
        if not (is_scalp_signal and self.scalp_skip_orderbook_direction_guard):
            if not is_long_sig and ob_bid_vol > 0 and ob_bid_vol > ob_ask_vol * 1.3:
                return reject(
                    f"orderbook_direction_guard (SELL but bid_vol={ob_bid_vol:.0f} >> ask_vol={ob_ask_vol:.0f})"
                )
            if is_long_sig and ob_ask_vol > 0 and ob_ask_vol > ob_bid_vol * 1.3:
                return reject(
                    f"orderbook_direction_guard (BUY but ask_vol={ob_ask_vol:.0f} >> bid_vol={ob_bid_vol:.0f})"
                )

        # =====================================================
        # PRICE MOMENTUM CONFIRMATION:
        # Last 3 candles must show at least 1 candle moving in signal direction.
        # This prevents entering after pure one-directional exhaustion.
        # Also check: price should not be moving strongly AGAINST signal.
        # =====================================================
        if len(klines) >= 4:
            last_3 = klines[-3:]
            favorable = 0
            against = 0
            for k in last_3:
                c_open = float(k.get("open", 0))
                c_close = float(k.get("close", 0))
                if is_long_sig and c_close > c_open:
                    favorable += 1
                elif is_long_sig and c_close < c_open:
                    against += 1
                elif not is_long_sig and c_close < c_open:
                    favorable += 1
                elif not is_long_sig and c_close > c_open:
                    against += 1
            # All 3 candles against signal = strong opposite momentum, reject
            if against == 3:
                return reject(f"price_momentum_against (3/3 candles oppose {signal.side})")

        # =====================================================
        # PEAK REVERSAL GUARD:
        # Avoid late entries directly near local extremes where mean-reversion
        # pullbacks often trigger tiny losses before trend continuation.
        # =====================================================
        if (
            self.entry_peak_reversal_guard
            and atr_val > 0
            and len(klines) >= max(3, self.entry_peak_lookback_bars)
            and confidence < self.entry_peak_confidence_bypass
        ):
            lb = max(3, int(self.entry_peak_lookback_bars))
            recent = klines[-lb:]
            recent_high = max(float(k.get("high", 0.0) or 0.0) for k in recent)
            recent_low = min(float(k.get("low", current_price) or current_price) for k in recent)
            dist_threshold = atr_val * max(0.0, self.entry_peak_distance_atr)
            if is_long_sig:
                distance_to_high = max(0.0, recent_high - current_price)
                if distance_to_high <= dist_threshold:
                    return reject(
                        "peak_reversal_guard "
                        f"(BUY near local high: dist={distance_to_high:.4f} <= {dist_threshold:.4f})"
                    )
            else:
                distance_to_low = max(0.0, current_price - recent_low)
                if distance_to_low <= dist_threshold:
                    return reject(
                        "peak_reversal_guard "
                        f"(SELL near local low: dist={distance_to_low:.4f} <= {dist_threshold:.4f})"
                    )

        # =====================================================
        # IMPULSE → RETEST → CONFIRM GUARD:
        # Require one additional confirmation candle after retest.
        # This improves timing and avoids chasing the first impulse leg.
        # =====================================================
        scalp_bypass_impulse = (
            is_scalp_signal
            and (
                self.scalp_skip_impulse_retest_guard
                or confidence >= self.scalp_impulse_retest_bypass_confidence
            )
        )
        if not scalp_bypass_impulse:
            impulse_ok, impulse_reason = self._passes_impulse_retest_confirmation(
                side=signal.side,
                klines=klines,
                atr_value=atr_val,
                confidence=confidence,
            )
            if not impulse_ok:
                return reject(impulse_reason)

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

            min_single_tf_conf = (
                self.scalp_mtf_single_tf_min_confidence
                if is_scalp_signal
                else self.mtf_zone_min_confidence_if_single_tf
            )
            if confirmations == 1 and signal.confidence < min_single_tf_conf:
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

        # Optional Claude/OpenClaw meta-filter (second opinion gate).
        if self.ai_claude_enabled:
            claude_data = self._build_claude_payload(current_price, market, signal)
            claude_decision = await self.ai_claude_engine.get_decision(claude_data)
            signal.metadata["claude_decision"] = claude_decision
            if not claude_decision.get("allow", True):
                reject_reason = claude_decision.get("reject_reason", "ai_claude_rejected")
                logger.info(f"[AI_CLAUDE] {symbol} REJECTED: {reject_reason}")
                return reject(reject_reason)
            if claude_decision.get("blended_confidence") is not None:
                signal.confidence = float(claude_decision["blended_confidence"])
                signal.capital_score = round(signal.confidence * signal.rr_ratio, 4)

        advisor_decision = self.advisor.evaluate(symbol, signal, market)
        signal.metadata.update(
            {
                "advisor_score": advisor_decision.score,
                "advisor_reason": advisor_decision.reason,
                "advisor_checks": advisor_decision.checks,
            }
        )
        if not advisor_decision.allow and self.advisor.mode == "enforce":
            logger.info(
                f"[ADVISOR] {symbol} REJECTED: {advisor_decision.reason} "
                f"(score={advisor_decision.score:.2f})"
            )
            return reject(advisor_decision.reason)
        if not advisor_decision.allow and self.advisor.mode == "advisory":
            logger.info(
                f"[ADVISOR] {symbol} advisory warning: {advisor_decision.reason} "
                f"(score={advisor_decision.score:.2f})"
            )

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


    @staticmethod
    def _resolve_signal_atr_pct(signal: EntrySignal, market) -> float:
        """Resolve ATR% for hard-gates.

        EntryEngine may not always populate signal.metadata["atr_pct"] for every signal
        path. In that case, fall back to market analyzer ATR% instead of treating it as 0.
        """
        meta_atr_pct = signal.metadata.get("atr_pct")
        if meta_atr_pct is None:
            return float(getattr(market, "atr_pct", 0.0) or 0.0)
        try:
            return float(meta_atr_pct)
        except (TypeError, ValueError):
            return float(getattr(market, "atr_pct", 0.0) or 0.0)


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


    def _passes_impulse_retest_confirmation(
        self,
        *,
        side: str,
        klines: list,
        atr_value: float,
        confidence: float,
    ) -> tuple[bool, str]:
        if (
            not self.entry_impulse_retest_confirm_enabled
            or atr_value <= 0
            or confidence >= self.entry_impulse_confirm_conf_bypass
            or len(klines) < 3
        ):
            return True, ""

        side_up = str(side or "").upper()
        if side_up not in {"BUY", "SELL"}:
            return True, ""

        impulse = klines[-3]
        retest = klines[-2]
        confirm = klines[-1]
        direction = 1 if side_up == "BUY" else -1
        min_impulse_body = atr_value * max(0.0, self.entry_impulse_min_body_atr)
        impulse_body = self._candle_body(impulse)
        impulse_dir = self._candle_dir(impulse)
        retest_body = self._candle_body(retest)
        retest_dir = self._candle_dir(retest)
        confirm_body = self._candle_body(confirm)
        confirm_dir = self._candle_dir(confirm)

        if impulse_dir != direction or impulse_body < min_impulse_body:
            return False, (
                "impulse_retest_confirm_guard "
                f"(no_impulse: dir={impulse_dir} body={impulse_body:.4f} < min={min_impulse_body:.4f})"
            )

        if retest_dir != -direction:
            return False, "impulse_retest_confirm_guard (no_retest_candle)"

        max_retest_body = atr_value * max(0.0, self.entry_retest_max_body_atr)
        if retest_body > max_retest_body:
            return False, (
                "impulse_retest_confirm_guard "
                f"(retest_too_deep: body={retest_body:.4f} > max={max_retest_body:.4f})"
            )

        if confirm_dir != direction:
            return False, "impulse_retest_confirm_guard (no_confirmation_candle)"

        if self.entry_confirm_min_body_ratio > 0:
            min_confirm_body = atr_value * max(0.0, self.entry_confirm_min_body_ratio)
            if confirm_body < min_confirm_body:
                return False, (
                    "impulse_retest_confirm_guard "
                    f"(confirm_body_too_small: body={confirm_body:.4f} < min={min_confirm_body:.4f})"
                )

        confirm_close = float(confirm.get("close", 0.0) or 0.0)
        retest_high = float(retest.get("high", 0.0) or 0.0)
        retest_low = float(retest.get("low", 0.0) or 0.0)
        if direction > 0 and confirm_close <= retest_high:
            return False, "impulse_retest_confirm_guard (confirm_not_above_retest_high)"
        if direction < 0 and confirm_close >= retest_low:
            return False, "impulse_retest_confirm_guard (confirm_not_below_retest_low)"

        return True, ""


    def _build_ai_payload(self, current_price: float, market, signal: EntrySignal) -> dict:
        return {
            "price": current_price,
            "rsi": float(getattr(market, "rsi", 50.0) or 50.0),
            "volume": float(signal.metadata.get("liquidity", 0.0) or 0.0),
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
            "orderflow": float(signal.metadata.get("normalized_imbalance", 0.0) or 0.0),
            "liquidations": float(signal.metadata.get("liq_signal", 0.0) or 0.0),
            "proposed_signal": signal.side,
            "confluence_score": signal.confidence,
        }


    def _build_claude_payload(self, current_price: float, market, signal: EntrySignal) -> dict:
        side = str(signal.side or "").upper()
        orderflow_bull = float(signal.metadata.get("orderflow_bullish_ratio", 1.0) or 1.0)
        orderflow_bear = float(signal.metadata.get("orderflow_bearish_ratio", 1.0) or 1.0)
        orderflow_delta = orderflow_bull - orderflow_bear
        volume_ratio = float(
            signal.metadata.get("volume_ratio")
            or signal.metadata.get("volume_guard_ratio")
            or 1.0
        )
        return {
            "symbol": signal.metadata.get("symbol") or "",
            "side": side,
            "price": float(current_price or 0.0),
            "rsi": float(getattr(market, "rsi", 50.0) or 50.0),
            "volume_ratio": volume_ratio,
            "trend": str(getattr(market, "trend", "")).lower(),
            "htf_trend": str(getattr(market, "htf_trend", "")).lower(),
            "orderflow": orderflow_delta,
            "orderflow_bullish_ratio": orderflow_bull,
            "orderflow_bearish_ratio": orderflow_bear,
            "liquidations": float(signal.metadata.get("liq_signal", 0.0) or 0.0),
            "atr_pct": float(signal.metadata.get("atr_pct", getattr(market, "atr_pct", 0.0)) or 0.0),
            "spread_pct": float(signal.metadata.get("spread_pct", 0.0) or 0.0),
            "confidence": float(signal.confidence or 0.0),
            "smc_score": float(signal.metadata.get("smc_score", 0.0) or 0.0),
            "rr_ratio": float(signal.rr_ratio or 0.0),
        }


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
        is_scalp_signal = str(signal.metadata.get("strategy", "")).lower() == "scalp_session"
        min_confidence_gate = (
            self.scalp_quality_min_confidence if is_scalp_signal else self.quality_gate_min_confidence
        )
        min_expected_edge_gate = (
            self.scalp_quality_min_expected_edge if is_scalp_signal else self.quality_gate_min_expected_edge
        )

        if confidence < min_confidence_gate:
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
            has_bos = bool(signal.metadata.get("has_bos", False))
            has_sweep = bool(signal.metadata.get("has_sweep", False))
            # Require at least 1 structural confirmation (BOS or sweep)
            # even for high-confidence signals. Pure AI + trend = unreliable.
            if (
                confidence >= self.quality_gate_strong_signal_min_confidence
                and smc_score >= self.quality_gate_strong_signal_min_smc
                and (has_bos or has_sweep)
            ):
                logger.info(
                    f"[QUALITY_GATE] {symbol} no_zone BYPASSED (conf={confidence:.2f} smc={smc_score:.2f} bos={has_bos} sweep={has_sweep})"
                )
            else:
                logger.info(
                    f"[QUALITY_GATE] {symbol} no_zone BLOCKED — need BOS or sweep "
                    f"(conf={confidence:.2f} smc={smc_score:.2f} bos={has_bos} sweep={has_sweep})"
                )
                return False, "no_zone_no_structure", {"quality_expected_edge": round(expected_edge, 4)}

        if expected_edge < min_expected_edge_gate:
            return False, "low_expected_edge", {"quality_expected_edge": round(expected_edge, 4)}

        if self.quality_gate_enabled:
            regime = str(signal.metadata.get("regime", "unknown")).lower()
            adx = float(signal.metadata.get("adx", 0.0) or 0.0)
            atr_pct = float(signal.metadata.get("atr_pct", 0.0) or 0.0)
            htf_trend = str(signal.metadata.get("htf_trend", "neutral")).lower()

            # Strong signal with real zone bypasses regime/atr/orderflow checks
            smc_score = float(signal.metadata.get("smc_score", 0.0) or 0.0)
            has_real_zone = entry_zone not in ("no_zone", "")
            strong_signal = (
                (not is_scalp_signal)
                and
                confidence >= self.quality_gate_strong_signal_min_confidence
                and smc_score >= self.quality_gate_strong_signal_min_smc
                and has_real_zone
            )

            if not strong_signal:
                if not self.quality_gate_allow_chop and regime == "chop":
                    chop_bypass_enabled = bool(
                        getattr(self, "quality_gate_chop_bypass_enabled", True)
                    )
                    chop_bypass_min_conf = float(
                        getattr(self, "quality_gate_chop_bypass_min_confidence", 0.78)
                    )
                    chop_bypass_min_imb = float(
                        getattr(self, "quality_gate_chop_bypass_min_abs_imbalance", 0.12)
                    )
                    chop_bypass_require_zone = bool(
                        getattr(self, "quality_gate_chop_bypass_require_zone", True)
                    )
                    chop_bypass_ok = chop_bypass_enabled
                    chop_bypass_ok = chop_bypass_ok and confidence >= chop_bypass_min_conf
                    chop_bypass_ok = chop_bypass_ok and abs_imbalance >= chop_bypass_min_imb
                    if chop_bypass_require_zone:
                        chop_bypass_ok = chop_bypass_ok and has_real_zone
                    if not chop_bypass_ok:
                        return False, "chop_regime", {"quality_expected_edge": round(expected_edge, 4)}
                    logger.info(
                        f"[QUALITY_GATE] {symbol} chop BYPASS "
                        f"(conf={confidence:.2f}, abs_imb={abs_imbalance:.3f}, zone={entry_zone})"
                    )
                if adx < self.quality_gate_min_adx:
                    return False, "low_adx", {"quality_expected_edge": round(expected_edge, 4)}
                if atr_pct < self.quality_gate_min_atr_pct:
                    return False, "low_atr", {"quality_expected_edge": round(expected_edge, 4)}
                if abs_imbalance < self.quality_gate_min_abs_imbalance:
                    return False, "flat_orderflow", {"quality_expected_edge": round(expected_edge, 4)}
                if self.quality_gate_require_htf_trend and htf_trend in {"neutral", "flat", "range", "sideways"}:
                    return False, "flat_htf_trend", {"quality_expected_edge": round(expected_edge, 4)}
            else:
                logger.info(
                    f"[QUALITY_GATE] {symbol} strong signal BYPASS "
                    f"(conf={confidence:.2f} smc={smc_score:.2f} zone={entry_zone})"
                )

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


# === bot\mixins\closes_mixin.py ===
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


# === bot\mixins\correlation_mixin.py ===
from bot.trading_bot_imports import *  # noqa: F401,F403

class TradingBotCorrelationMixin:
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


# === bot\mixins\entry_exec_mixin.py ===
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
        latest_closed_ts = self._last_closed_kline_ts(klines)
        if latest_closed_ts > 0:
            pos.last_counted_kline_ts = latest_closed_ts
            pos.bars_since_entry = 0
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


# === bot\mixins\exchange_closed_mixin.py ===
from bot.trading_bot_imports import *  # noqa: F401,F403

class TradingBotExchangeClosedMixin:
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
        return filter_recent_closed_pnl(closed_records, max_age_sec=max_age_sec)


    @staticmethod
    def _classify_exchange_closed_reason(closed_records: list | None) -> str:
        return classify_exchange_closed_reason(closed_records)


    def _set_exchange_close_meta(self, symbol: str, closed_records: list | None):
        """Persist compact closed-pnl metadata for the next finalized trade record."""
        if not symbol:
            return
        if not closed_records:
            self._last_exchange_close_meta[symbol] = {}
            return
        record = closed_records[0] or {}
        meta = {
            "execType": record.get("execType", ""),
            "stopOrderType": record.get("stopOrderType", ""),
            "orderType": record.get("orderType", ""),
            "createType": record.get("createType", ""),
            "closeType": record.get("closeType", ""),
            "orderFilter": record.get("orderFilter", ""),
            "orderLinkId": record.get("orderLinkId", ""),
            "updatedTime": record.get("updatedTime", ""),
            "createdTime": record.get("createdTime", ""),
        }
        self._last_exchange_close_meta[symbol] = meta


    def _pop_exchange_close_meta(self, symbol: str) -> dict:
        if not symbol:
            return {}
        return self._last_exchange_close_meta.pop(symbol, {}) or {}


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


# === bot\mixins\feedback_mixin.py ===
from bot.trading_bot_imports import *  # noqa: F401,F403

class TradingBotFeedbackMixin:
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
        output_path = resolve_bot_dir() / "training_data_merged.json"
        with open(output_path, "w", encoding="utf-8") as handle:
            json.dump(merged, handle, ensure_ascii=False, indent=2)

        logger.info(
            f"[FEEDBACK] Retrain dataset prepared: base={len(base_rows)} "
            f"quality_feedback={len(feedback_rows)} total={len(merged)}"
        )
        return output_path


# === bot\mixins\guards_mixin.py ===
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


# === bot\mixins\helpers_mixin.py ===
from bot.trading_bot_imports import *  # noqa: F401,F403

class TradingBotHelpersMixin:
    @staticmethod
    def _interval_to_seconds(interval: str | int | float) -> int:
        return interval_to_seconds(interval)


    @staticmethod
    def _last_closed_kline_ts(klines: list) -> int:
        return last_closed_kline_ts(klines)


    @staticmethod
    def _parse_iso_dt(value: str):
        return parse_iso_dt(value)


    def _unique_symbols(self, symbols: list[str]) -> list[str]:
        unique = []
        seen = set()
        for symbol in symbols:
            if symbol and symbol not in seen:
                unique.append(symbol)
                seen.add(symbol)
        return unique


    @staticmethod
    def _candle_dir(candle: dict) -> int:
        c_open = float(candle.get("open", 0.0) or 0.0)
        c_close = float(candle.get("close", 0.0) or 0.0)
        if c_close > c_open:
            return 1
        if c_close < c_open:
            return -1
        return 0


    @staticmethod
    def _candle_body(candle: dict) -> float:
        c_open = float(candle.get("open", 0.0) or 0.0)
        c_close = float(candle.get("close", 0.0) or 0.0)
        return abs(c_close - c_open)


    @staticmethod
    def _ema(data: list, period: int) -> float:
        if len(data) < period:
            return sum(data) / len(data) if data else 0
        mult = 2 / (period + 1)
        ema = sum(data[:period]) / period
        for val in data[period:]:
            ema = (val - ema) * mult + ema
        return ema


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


# === bot\mixins\lifecycle_mixin.py ===
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
            ok, err = self.security.validate_bybit_keys()
            if not ok:
                logger.error(f"Bybit keys: {err}")
                return

            if self.tg:
                asyncio.create_task(self.tg.start_async())
                tg_started = True
                await asyncio.sleep(2)

            startup_balance_ok = True
            startup_balance_error = ""
            balance = 0.0
            for attempt in range(1, 4):
                try:
                    balance = await self.client.get_balance()
                    if balance > 0:
                        break
                    startup_balance_error = (
                        "Failed to read positive balance. "
                        "Check Bybit API key/secret permissions and expiration."
                    )
                except Exception as exc:
                    startup_balance_error = f"get_balance error: {exc}"
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
                self.signal_only = True
                self.controls.signal_only = True
                logger.warning("[STARTUP] Balance unavailable -> forcing SIGNAL-ONLY mode.")

            if self.tg:
                balance_display = f"${balance:.2f}" if startup_balance_ok else "N/A"
                startup_text = (
                    f"<b>Бот v9.0 запущен</b>\n"
                    f"Баланс: <code>{balance_display}</code>\n"
                    f"Режим: {'СИГНАЛЫ' if self.signal_only else ('ТЕСТ' if self.controls.dry_run else 'LIVE')}\n"
                    f"Стратегия: SMC v3 (Sweep→BOS→Retest OB/FVG) + AI + Pyramid"
                )
                if not startup_balance_ok and startup_balance_error:
                    startup_text += f"\n⚠️ Balance warning: <code>{startup_balance_error}</code>"
                await self.tg.send_message(startup_text)

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


# === bot\mixins\liquidation_mixin.py ===
from bot.trading_bot_imports import *  # noqa: F401,F403

class TradingBotLiquidationMixin:
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


# === bot\mixins\notify_symbols_mixin.py ===
from bot.trading_bot_imports import *  # noqa: F401,F403

class TradingBotNotifySymbolsMixin:
    async def _notify_tg(self, message: str):
        if self.tg:
            await self.tg.send_alert(message)


    async def get_trade_symbols(self) -> list:
        """Scan top symbols by momentum. Whitelist symbols always at front (priority).
        If whitelist_only=True, ONLY whitelist symbols are traded."""
        # Whitelist-only mode: skip scanning, return whitelist directly
        if self.whitelist_only and self.whitelist:
            result = [s for s in self.whitelist if s not in self.blacklist]
            logger.info(f"Symbol scanner: WHITELIST-ONLY mode → {len(result)} symbols: {result}")
            return result

        try:
            tickers = await self.client.get_tickers()
        except Exception as exc:
            logger.error(f"Failed to get tickers: {exc}")
            limit = max(1, int(getattr(self, "trade_symbols", 25) or 25))
            return self.whitelist[:limit] if self.whitelist else []

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

        limit = max(1, int(getattr(self, "trade_symbols", 25) or 25))
        result = unique[:limit]
        wl_in = [s for s in self.whitelist if s in result]
        logger.info(f"Symbol scanner: {len(ranked)} eligible → top {len(result)} (whitelist: {wl_in})")
        return result


# === bot\mixins\position_loop_mixin.py ===
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

            guard_exit, guard_reason = await self._check_profit_drawdown_guard(pos, current_price, klines)
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
                reason_code = age_min = "n/a"
                parsed = re.search(
                    r"Profit\s+([+-]?\d+(?:\.\d+)?)\s*/\s*best\s+([+-]?\d+(?:\.\d+)?)\s*<\s*required\s+([+-]?\d+(?:\.\d+)?)\s*\(incl fees\s+([+-]?\d+(?:\.\d+)?)\)",
                    details or "",
                )
                parsed_code = re.search(r"\bcode=([a-zA-Z0-9_]+)\b", details or "")
                parsed_age = re.search(r"\bage_min=([0-9.]+|n/a)\b", details or "")
                if parsed:
                    raw_profit = parsed.group(1)
                    best_profit = parsed.group(2)
                    required_profit = parsed.group(3)
                    fee_floor = parsed.group(4)
                    try:
                        effective_profit = f"{max(float(raw_profit), float(best_profit)):.4f}"
                    except Exception:
                        effective_profit = "n/a"
                if parsed_code:
                    reason_code = parsed_code.group(1)
                if parsed_age:
                    age_min = parsed_age.group(1)
                logger.info(
                    f"[EARLY_EXIT] {symbol} {pos.side} "
                    f"price={current_price:.4f} entry={pos.entry_price:.4f} "
                    f"best={pos.best_price:.4f} bars={pos.bars_since_entry} "
                    f"raw_profit={raw_profit} best_profit={best_profit} "
                    f"effective_profit={effective_profit} required_profit={required_profit} "
                    f"fee_floor={fee_floor} "
                    f"reason_code={reason_code} age_min={age_min} "
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

            # EMA TREND EXIT — close if price reverses against EMA(20).
            # While trailing is active, skip: 1m EMA noise otherwise exits winners before the trail can.
            if (
                not should_exit
                and self.ema_trend_exit_enabled
                and not getattr(pos, "trailing_active", False)
            ):
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


# === bot\mixins\regime_mixin.py ===
from bot.trading_bot_imports import *  # noqa: F401,F403

class TradingBotRegimeMixin:
    def _apply_tf_preset(self):
        """Apply timeframe preset overrides (1m/5m/15m) to all relevant settings."""
        if not self._active_tf_preset:
            return
        presets_cfg = self.cfg.get("tf_presets", "presets", default={})
        tf = presets_cfg.get(self._active_tf_preset) if isinstance(presets_cfg, dict) else None
        if not tf or not isinstance(tf, dict):
            logger.warning(f"[TF PRESET] '{self._active_tf_preset}' not found, using base config")
            return
        self.candle_interval = str(tf.get("candle_interval", self.candle_interval))
        self.htf_interval = str(tf.get("htf_interval", self.htf_interval))
        self.htf_4h_interval = str(tf.get("htf_4h_interval", self.htf_4h_interval))
        self.cycle_sleep = int(tf.get("cycle_sleep_sec", self.cycle_sleep))
        self.scan_interval_sec = int(tf.get("scan_interval_sec", self.scan_interval_sec))
        self.position_active_sleep_sec = int(tf.get("position_active_sleep_sec", self.position_active_sleep_sec))
        self.klines_limit = max(int(tf.get("klines_limit", self.klines_limit)), self.feature_window)
        self.exit_engine.early_exit_bars = int(tf.get("early_exit_bars", self.exit_engine.early_exit_bars))
        self.exit_engine.early_exit_min_hold_minutes = max(
            0.0,
            float(tf.get("early_exit_min_hold_minutes", self.exit_engine.early_exit_min_hold_minutes)),
        )
        self.exit_engine.trailing_activation_atr = float(tf.get("trailing_activation_atr", self.exit_engine.trailing_activation_atr))
        self.exit_engine.trailing_distance_atr = float(tf.get("trailing_distance_atr", self.exit_engine.trailing_distance_atr))
        self.exit_engine.hard_sl_atr_mult = float(tf.get("hard_sl_atr_mult", self.exit_engine.hard_sl_atr_mult))
        if "volatility_floor_atr_pct" in tf:
            self.volatility_floor_atr_pct = float(tf["volatility_floor_atr_pct"])
        logger.info(
            f"[TF PRESET] Applied '{self._active_tf_preset}': "
            f"candle={self.candle_interval} htf={self.htf_interval} htf_4h={self.htf_4h_interval} "
            f"cycle={self.cycle_sleep}s early_exit={self.exit_engine.early_exit_bars}bars "
            f"trail_act={self.exit_engine.trailing_activation_atr} trail_dist={self.exit_engine.trailing_distance_atr}"
        )


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
            target_require_4h_trend = (
                self.adaptive_trend_require_4h_trend
                if profile_name == "trend"
                else self.adaptive_range_require_4h_trend
            )

            changed = (
                self._active_regime_profile != profile_name
                or self.strict_htf_mode != target_strict_htf
                or abs(self.volatility_floor_atr_pct - target_vol_floor) > 1e-9
                or self.entry_require_4h_trend != target_require_4h_trend
            )

            self.strict_htf_mode = target_strict_htf
            self.volatility_floor_atr_pct = target_vol_floor
            self.entry_require_4h_trend = target_require_4h_trend
            self.entry_engine.require_4h_trend = target_require_4h_trend

            if changed:
                self._active_regime_profile = profile_name
                msg = (
                    f"[ADAPTIVE PRESET] profile={profile_name} regime={regime_value} "
                    f"strict_htf={'ON' if self.strict_htf_mode else 'OFF'} "
                    f"vol_floor={self.volatility_floor_atr_pct:.2f}% "
                    f"require_4h_trend={'ON' if self.entry_require_4h_trend else 'OFF'}"
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


    def _switch_signal_mode(self, signal_only: bool) -> tuple[bool, str]:
        target = bool(signal_only)
        if self.signal_only == target:
            return True, f"Режим уже {'SIGNAL-ONLY' if target else 'LIVE'}"

        self.signal_only = target
        self.controls.signal_only = target

        try:
            config_path = resolve_bot_dir() / "config.yaml"
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


# === bot\mixins\scanning_mixin.py ===
from bot.trading_bot_imports import *  # noqa: F401,F403

class TradingBotScanningMixin:
    async def _scan_entries(self, symbols: list):
        candidates = []
        reject_counts: dict[str, int] = {}
        blocked_hours = set(getattr(self, "block_entry_utc_hours", set()) or set())
        current_utc_hour = datetime.now(timezone.utc).hour
        if blocked_hours and current_utc_hour in blocked_hours:
            logger.warning(
                f"ENTRY SCAN BLOCKED: utc_hour={current_utc_hour} in block_entry_utc_hours={sorted(blocked_hours)}"
            )
            logger.info(
                f"SCAN SUMMARY: symbols={len(symbols)} candidates=0 rejects[blocked_utc_hour={len(symbols)}]"
            )
            self.controls.set_candidates([])
            return

        def mark_reject(reason: str):
            reject_counts[reason] = reject_counts.get(reason, 0) + 1

        for symbol in symbols:
            if self.position_manager.has(symbol):
                mark_reject("already_in_position")
                continue
            now_ts = time.monotonic()
            min_symbol_rescan_sec = max(0.0, float(getattr(self, "min_symbol_rescan_sec", 0.0) or 0.0))
            if min_symbol_rescan_sec > 0:
                last_scanned_at = float(getattr(self, "_last_symbol_scan_ts", {}).get(symbol, 0.0) or 0.0)
                if last_scanned_at > 0 and (now_ts - last_scanned_at) < min_symbol_rescan_sec:
                    mark_reject("symbol_rescan_throttle")
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

            allowed, risk_reason = self.risk_guard.can_trade(symbol)
            if not allowed:
                if risk_reason:
                    reason_key = str(risk_reason).strip().lower().replace(" ", "_")
                    mark_reject(f"risk_blocked_{reason_key}")
                else:
                    mark_reject("risk_blocked")
                continue
            try:
                self._last_symbol_scan_ts[symbol] = now_ts
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

        bpr = getattr(self, "bpr_ranker", None)
        if bpr is not None and bpr.enabled and candidates:
            bpr.annotate_candidates(candidates)

        ranked = self.allocator.allocate(candidates)

        if bpr is not None and bpr.enabled:
            ranked = bpr.maybe_take_top1(ranked)
        self.controls.set_candidates(ranked)
        summary = ", ".join(f"{key}={value}" for key, value in sorted(reject_counts.items())) or "none"
        logger.info(f"SCAN SUMMARY: symbols={len(symbols)} candidates={len(ranked)} rejects[{summary}]")

        if bpr is not None and bpr.enabled and candidates and bpr.telegram_top_n > 0 and getattr(self, "tg", None):
            topn = sorted(
                candidates,
                key=lambda c: float(c.get("bpr_score", 0.0) or 0.0),
                reverse=True,
            )[: bpr.telegram_top_n]
            lines = ["<b>BPR rank (cycle)</b>"]
            for i, c in enumerate(topn, 1):
                sig = c["signal"]
                bs = float(c.get("bpr_score", 0.0) or 0.0)
                conf = float(getattr(sig, "confidence", 0.0) or 0.0)
                side = str(getattr(sig, "side", "") or "")
                soft = bool((sig.metadata or {}).get("entry_soft_pass"))
                lines.append(
                    f"{i}. <code>{c['symbol']}</code> BPR={bs:.3f} conf={conf:.2f} {side}"
                    + (" <i>(soft)</i>" if soft else "")
                )
            try:
                await self.tg.send_message("\n".join(lines))
            except Exception as exc:
                logger.warning(f"BPR telegram notify failed: {exc}")

        if self.signal_only:
            # Signal-only mode: send to Telegram, no execution (only if confidence above threshold)
            for item in ranked:
                signal = item["signal"]
                symbol = item["symbol"]
                conf_val = float(signal.confidence or 0.0)
                if conf_val <= self.signal_only_min_confidence:
                    logger.info(
                        f"SIGNAL-ONLY SKIP {symbol}: confidence={conf_val:.0%} "
                        f"(need >{self.signal_only_min_confidence:.0%})"
                    )
                    continue
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
                    f"<b>SIGNAL {direction} [{signal.grade}]</b>\n\n"
                    f"Монета: <code>{symbol}</code>\n"
                    f"Грейд: <b>{signal.grade}</b>\n"
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
                logger.info(f"SIGNAL-ONLY {symbol}: {direction} [{signal.grade}] entry=${entry:.4f} SL=${sl:.4f} TP=${tp:.4f} RR={rr:.1f}")
                self._register_signal_timestamp(symbol, side)
                self.signal_feedback.register_signal(symbol, signal)
                if self.tg:
                    await self.tg.send_message(msg)
            return

        available_slots = max(0, self.controls.max_positions - self.position_manager.count())
        executable = ranked[:available_slots]
        if executable:
            if self.entry_capital_weight_mode == "equal":
                # Equal split across executable signals to avoid tiny under-sized
                # entries on low-ranked candidates (user expected fixed margin cap).
                equal_weight = 1.0 / float(len(executable))
                for item in executable:
                    item["capital_weight"] = equal_weight
            else:
                # Renormalize capital weights to the actually executable set
                # (top-N by available slots). Otherwise each weight is diluted by
                # non-executed candidates and position size becomes too small.
                total_weight = sum(float(item.get("capital_weight", 0.0) or 0.0) for item in executable)
                if total_weight > 0:
                    for item in executable:
                        item["capital_weight"] = max(
                            0.0, float(item.get("capital_weight", 0.0) or 0.0) / total_weight
                        )
        for item in executable:
            await self._execute_entry(item["symbol"], item["signal"], item.get("capital_weight", 1.0))


# === bot\mixins\sync_manual_mixin.py ===
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
        # Align trailing activation with profit-guard arming threshold (+/- activation % from entry).
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


