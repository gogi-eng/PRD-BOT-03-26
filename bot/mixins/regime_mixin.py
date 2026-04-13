"""Auto-split from main.TradingBot — see package bot.trading_bot."""
from __future__ import annotations

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
