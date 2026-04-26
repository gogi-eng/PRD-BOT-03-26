"""Auto-split from main.TradingBot — see package bot.trading_bot."""
from __future__ import annotations

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

        # Get funding + 24h change (single-symbol ticker; used for funding gate + mover cap).
        funding_rate = 0.0
        abs_change_24h_dec = None
        try:
            t = await self.client.get_ticker(symbol)
            if t:
                funding_rate = float(t.get("fundingRate", 0) or 0)
                raw_pct = t.get("price24hPcnt")
                if raw_pct is not None and raw_pct != "":
                    abs_change_24h_dec = float(raw_pct)
        except Exception:
            pass

        fail_funding, funding_reason = self.entry_engine.funding_pre_fails(
            funding_rate, abs_change_24h_dec
        )
        if fail_funding:
            return reject(funding_reason)

        if signal is None:
            signal = self.entry_engine.generate_signal(
                symbol, klines, current_price, market, regime, transformer, orderflow, liq,
                atr_val, zone_context=zone_context, structure=structure, funding_rate=funding_rate,
                htf_4h_trend=htf_4h_trend,
                abs_change_24h_dec=abs_change_24h_dec,
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

        h_reason = self._hybrid_voter_check_signal(symbol, signal, market, orderflow)
        if h_reason:
            return reject(h_reason)

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
