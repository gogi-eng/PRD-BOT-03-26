"""TradingBot orchestrator — composed from mixins (legacy main.TradingBot)."""
from __future__ import annotations

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
        self.ai_analyzer = AITradeAnalyzer()
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
        # Runtime safety timeouts to avoid "silent hangs" on network-bound awaits.
        self.runtime_stage_timeout_sec = float(
            self.cfg.get("bot", "runtime_stage_timeout_sec", default=60)
        )
        self.runtime_scan_timeout_sec = float(
            self.cfg.get("bot", "runtime_scan_timeout_sec", default=180)
        )
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


