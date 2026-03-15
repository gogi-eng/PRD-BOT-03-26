#!/usr/bin/env python3
"""
SMC ENTRY ENGINE v3 — Sweep → BOS → Retest OB/FVG.

Entry logic:
  LONG:  sweep_down → BOS_up → price retests order_block/FVG → open_long()
  SHORT: sweep_up → BOS_down → price retests order_block/FVG → open_short()

Filters:
  - trend != RANGE
  - volume_spike (volume > avg*2) AND spread_expansion (range > ATR*1.5)
  - Execution: spread < threshold, funding_rate < 0.05
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional


@dataclass
class EntrySignal:
    should_enter: bool = False
    side: str = ""
    confidence: float = 0.0
    entry_price: float = 0.0
    stop_loss: float = 0.0
    take_profit: float = 0.0
    rr_ratio: float = 0.0
    reasons: list = field(default_factory=list)
    filters_passed: dict = field(default_factory=dict)
    capital_score: float = 0.0
    metadata: dict = field(default_factory=dict)


class EntryEngine:
    """SMC Entry Engine v3: Sweep → BOS → Retest OB/FVG."""

    def __init__(self, cfg):
        self.min_rr_ratio = cfg.get("entry", "min_rr_ratio", default=1.3)
        self.min_target_profit_pct = cfg.get("entry", "min_target_profit_pct", default=1.2)
        self.min_stop_distance_pct = cfg.get("entry", "min_stop_distance_pct", default=0.35)
        self.sl_buffer_atr_mult = cfg.get("entry", "sl_buffer_atr_mult", default=0.2)
        self.zone_proximity_pct = cfg.get("entry", "zone_proximity_pct", default=0.4)
        # Soft boosters
        self.transformer_threshold = cfg.get("entry", "transformer_threshold", default=0.54)
        self.max_liq_distance_pct = cfg.get("entry", "max_liq_distance_pct", default=1.30)
        self.min_orderflow_imbalance = cfg.get("entry", "min_orderflow_imbalance", default=1.03)
        # Execution pre-checks
        self.max_spread_pct = cfg.get("entry", "max_spread_pct", default=0.08)
        self.max_funding_rate = cfg.get("entry", "max_funding_rate", default=0.05)
        self.min_liq_depth = cfg.get("entry", "min_liq_depth", default=0.0)
        # SMC quality thresholds
        self.min_smc_score = cfg.get("entry", "min_smc_score", default=0.55)
        self.min_volatility_pct = cfg.get("entry", "min_volatility_pct", default=0.8)
        # Allowed regimes
        self.allowed_regimes = set(cfg.get("entry", "allowed_regimes", default=["trend", "breakout", "chop"]))

    def generate_signal(
        self, symbol: str, klines: List[Dict], current_price: float,
        market_analysis, regime_prediction, transformer_prediction,
        orderflow_snapshot, liq_analysis, atr_value: float = 0.0,
        zone_context=None, structure=None, funding_rate: float = 0.0,
    ) -> EntrySignal:
        signal = EntrySignal(entry_price=current_price)

        if not market_analysis.can_trade:
            signal.metadata["reject_reason"] = "market_blocked"
            return signal

        if atr_value <= 0:
            atr_value = current_price * 0.008

        # --- Execution pre-checks ---
        spread_pct = orderflow_snapshot.spread_pct if hasattr(orderflow_snapshot, 'spread_pct') else 0.0
        if self.max_spread_pct > 0 and spread_pct > self.max_spread_pct:
            signal.metadata["reject_reason"] = f"spread_too_wide ({spread_pct:.3f}%)"
            return signal
        if self.max_funding_rate > 0 and abs(funding_rate) > self.max_funding_rate:
            signal.metadata["reject_reason"] = f"funding_rate_too_high ({funding_rate:.4f})"
            return signal

        # Volatility filter: ATR/price < min_volatility_pct → skip
        volatility_pct = (atr_value / current_price * 100) if current_price > 0 else 0
        if self.min_volatility_pct > 0 and volatility_pct < self.min_volatility_pct:
            signal.metadata["reject_reason"] = f"low_volatility ({volatility_pct:.2f}% < {self.min_volatility_pct}%)"
            return signal

        # --- Core: Market Structure signals ---
        has_structure = structure is not None
        struct_long = has_structure and structure.signal_ready_long
        struct_short = has_structure and structure.signal_ready_short
        struct_trend = structure.trend.value if has_structure else "range"
        momentum_ok = structure.momentum_confirmed if has_structure else False
        volume_spike = structure.volume_spike if has_structure else False
        bos = structure.last_bos if has_structure else None
        sweep = structure.last_sweep if has_structure else None

        # --- SMC zone proximity (FVG/OB retest) ---
        zone_long_score, zone_long_reasons, zone_long = self._zone_retest_score(
            current_price, zone_context, is_long=True
        )
        zone_short_score, zone_short_reasons, zone_short = self._zone_retest_score(
            current_price, zone_context, is_long=False
        )

        # --- Scoring ---
        long_score = 0.0
        long_reasons = []
        short_score = 0.0
        short_reasons = []

        # Structure signal (sweep + BOS) — primary signal
        if struct_long:
            long_score += 0.35
            long_reasons.append("sweep_down→BOS_up")
            if bos and bos.volume_confirmed:
                long_score += 0.10
                long_reasons.append("BOS_volume_confirmed")
        if struct_short:
            short_score += 0.35
            short_reasons.append("sweep_up→BOS_down")
            if bos and bos.volume_confirmed:
                short_score += 0.10
                short_reasons.append("BOS_volume_confirmed")

        # Continuation model — BOS + small pullback (no sweep required)
        # This catches: BOS → pullback < 0.4 ATR → entry
        if not struct_long and bos and bos.direction == "up" and struct_trend == "up":
            pullback = (structure.previous_high - current_price) if has_structure else 0
            if 0 < pullback < atr_value * 0.4:
                long_score += 0.28
                long_reasons.append(f"continuation_BOS_up (pullback={pullback:.4f})")
                if bos.volume_confirmed:
                    long_score += 0.08
                    long_reasons.append("continuation_vol_confirmed")
        if not struct_short and bos and bos.direction == "down" and struct_trend == "down":
            pullback = (current_price - structure.previous_low) if has_structure else 0
            if 0 < pullback < atr_value * 0.4:
                short_score += 0.28
                short_reasons.append(f"continuation_BOS_down (pullback={pullback:.4f})")
                if bos.volume_confirmed:
                    short_score += 0.08
                    short_reasons.append("continuation_vol_confirmed")

        # Zone retest (OB/FVG)
        long_score += zone_long_score
        long_reasons.extend(zone_long_reasons)
        short_score += zone_short_score
        short_reasons.extend(zone_short_reasons)

        # Momentum filter
        if momentum_ok:
            long_score += 0.10
            short_score += 0.10
            long_reasons.append("momentum_confirmed")
            short_reasons.append("momentum_confirmed")
        elif volume_spike:
            long_score += 0.05
            short_score += 0.05

        # Trend alignment
        if struct_trend == "up":
            long_score += 0.10
            long_reasons.append(f"trend={struct_trend}")
        elif struct_trend == "down":
            short_score += 0.10
            short_reasons.append(f"trend={struct_trend}")

        # HTF trend from market_analysis
        if market_analysis.htf_trend.value > 0:
            long_score += 0.08
            long_reasons.append("HTF_bullish")
        elif market_analysis.htf_trend.value < 0:
            short_score += 0.08
            short_reasons.append("HTF_bearish")

        # --- Soft boosters (transformer, orderflow, heatmap) ---
        long_boost = self._compute_boost(
            transformer_prediction.prob_up, orderflow_snapshot.bullish_ratio,
            liq_analysis, is_long=True,
        )
        short_boost = self._compute_boost(
            transformer_prediction.prob_down, orderflow_snapshot.bearish_ratio,
            liq_analysis, is_long=False,
        )
        long_total = long_score + long_boost
        short_total = short_score + short_boost

        # --- Entry threshold: min_smc_score (default 0.55) ---
        min_score = self.min_smc_score
        can_long = long_total >= min_score and struct_trend != "down"
        can_short = short_total >= min_score and struct_trend != "up"

        if not can_long and not can_short:
            signal.metadata["reject_reason"] = self._resolve_reject(
                long_score, short_score, long_boost, short_boost,
                struct_long, struct_short, struct_trend, zone_context, momentum_ok,
            )
            signal.metadata.update({
                "long_score": round(long_total, 3), "short_score": round(short_total, 3),
                "struct_trend": struct_trend, "has_bos": bos is not None,
                "has_sweep": sweep is not None, "momentum": momentum_ok,
            })
            return signal

        is_long = can_long and (long_total >= short_total or not can_short)

        # --- SL from structure (sweep_low/high - ATR buffer) ---
        if is_long:
            if has_structure and structure.sweep_low > 0:
                sl = structure.sweep_low - atr_value * self.sl_buffer_atr_mult
            elif zone_context:
                sl = zone_context.structural_sl_long(current_price, atr_value)
            else:
                sl = current_price - atr_value * 1.8
            # TP: previous_high (TP1), liquidity_cluster (TP2)
            tp1 = structure.previous_high if has_structure and structure.previous_high > current_price else current_price + atr_value * 2.5
            if zone_context:
                struct_tp1, struct_tp2 = zone_context.structural_tp_long(current_price, atr_value)
                tp2 = max(struct_tp2, tp1)
            else:
                tp2 = tp1 + atr_value * 2.0
            if liq_analysis.target_level > current_price and liq_analysis.signal > 0:
                tp2 = max(tp2, liq_analysis.target_level)
            total_score = long_total
            reasons = long_reasons
            entry_zone = zone_long
        else:
            if has_structure and structure.sweep_high > 0:
                sl = structure.sweep_high + atr_value * self.sl_buffer_atr_mult
            elif zone_context:
                sl = zone_context.structural_sl_short(current_price, atr_value)
            else:
                sl = current_price + atr_value * 1.8
            tp1 = structure.previous_low if has_structure and structure.previous_low < current_price else current_price - atr_value * 2.5
            if zone_context:
                struct_tp1, struct_tp2 = zone_context.structural_tp_short(current_price, atr_value)
                tp2 = min(struct_tp2, tp1)
            else:
                tp2 = tp1 - atr_value * 2.0
            if liq_analysis.target_level > 0 and liq_analysis.target_level < current_price and liq_analysis.signal < 0:
                tp2 = min(tp2, liq_analysis.target_level)
            total_score = short_total
            reasons = short_reasons
            entry_zone = zone_short

        # --- Enforce minimum distances ---
        min_stop_dist = current_price * (self.min_stop_distance_pct / 100)
        if abs(current_price - sl) < min_stop_dist:
            sl = current_price - min_stop_dist if is_long else current_price + min_stop_dist

        risk = abs(current_price - sl)
        if risk <= 0:
            signal.metadata["reject_reason"] = "zero_risk"
            return signal

        # TP: ensure min RR
        take_profit = tp2
        rr_min_tp = current_price + risk * self.min_rr_ratio if is_long else current_price - risk * self.min_rr_ratio
        if is_long:
            take_profit = max(take_profit, rr_min_tp)
        else:
            take_profit = min(take_profit, rr_min_tp)

        min_target_dist = current_price * (self.min_target_profit_pct / 100)
        if abs(take_profit - current_price) < min_target_dist:
            take_profit = current_price + min_target_dist if is_long else current_price - min_target_dist

        reward = abs(take_profit - current_price)
        rr_ratio = reward / risk if risk > 0 else 0.0
        if rr_ratio < self.min_rr_ratio:
            take_profit = current_price + risk * self.min_rr_ratio if is_long else current_price - risk * self.min_rr_ratio
            reward = abs(take_profit - current_price)
            rr_ratio = reward / risk if risk > 0 else 0.0

        confidence = min(0.95, total_score)
        side = "BUY" if is_long else "SELL"

        signal.should_enter = True
        signal.side = side
        signal.confidence = round(confidence, 4)
        signal.stop_loss = round(sl, 8)
        signal.take_profit = round(take_profit, 8)
        signal.rr_ratio = round(rr_ratio, 2)
        signal.capital_score = round(confidence * rr_ratio, 4)
        signal.reasons = reasons + [f"RR={rr_ratio:.1f}", f"trend={struct_trend}"]
        signal.metadata = {
            "target_level": tp2 if is_long else tp2,
            "protective_liq_level": round(sl, 8),
            "transformer_prob_up": transformer_prediction.prob_up,
            "transformer_prob_down": transformer_prediction.prob_down,
            "transformer_prob_flat": transformer_prediction.prob_flat,
            "regime": regime_prediction.regime.value,
            "orderflow_bullish_ratio": orderflow_snapshot.bullish_ratio,
            "orderflow_bearish_ratio": orderflow_snapshot.bearish_ratio,
            "spread_pct": spread_pct,
            "liq_distance_pct": liq_analysis.distance_to_target_pct,
            "liq_signal": liq_analysis.signal,
            "liq_magnet": liq_analysis.magnet_direction,
            "liq_density": liq_analysis.target_density,
            "tp1_level": tp1,
            "tp2_level": tp2,
            "zone_confluence": (zone_context.bullish_confluence if is_long else zone_context.bearish_confluence) if zone_context else False,
            "entry_zone": f"{entry_zone.kind}_{entry_zone.bias}" if entry_zone else "none",
            "smc_score": round(long_score if is_long else short_score, 3),
            "boost_score": round(long_boost if is_long else short_boost, 3),
            "struct_trend": struct_trend,
            "has_bos": bos is not None,
            "bos_direction": bos.direction if bos else "none",
            "bos_volume_confirmed": bos.volume_confirmed if bos else False,
            "has_sweep": sweep is not None,
            "sweep_direction": sweep.direction if sweep else "none",
            "momentum_confirmed": momentum_ok,
            "volume_spike": volume_spike,
            "funding_rate": funding_rate,
        }
        return signal

    def _zone_retest_score(self, price, zone_ctx, is_long: bool):
        """Score for price retesting an OB or FVG zone."""
        if zone_ctx is None:
            return 0.0, [], None

        score = 0.0
        reasons = []
        best_zone = None

        if is_long:
            zone_in = zone_ctx.price_in_bullish_zone(price)
            zone_near = zone_ctx.price_near_bullish_zone(price, self.zone_proximity_pct) if not zone_in else None
        else:
            zone_in = zone_ctx.price_in_bearish_zone(price)
            zone_near = zone_ctx.price_near_bearish_zone(price, self.zone_proximity_pct) if not zone_in else None

        active_zone = zone_in or zone_near
        if active_zone:
            score += 0.25 if zone_in else 0.15
            reasons.append(f"retest {'in' if zone_in else 'near'} {active_zone.kind}_{active_zone.bias}")
            score += active_zone.strength * 0.10
            best_zone = active_zone

        # Confluence: both FVG and OB present
        if is_long and zone_ctx.bullish_confluence:
            score += 0.08
            reasons.append("FVG+OB_confluence")
        elif not is_long and zone_ctx.bearish_confluence:
            score += 0.08
            reasons.append("FVG+OB_confluence")

        return score, reasons, best_zone

    def _compute_boost(self, transformer_prob, flow_ratio, liq, is_long) -> float:
        """Soft score boost from transformer, orderflow, heatmap."""
        boost = 0.0
        if transformer_prob >= self.transformer_threshold:
            boost += min(0.10, (transformer_prob - 0.5) * 0.25)
        if flow_ratio >= self.min_orderflow_imbalance:
            boost += min(0.06, (flow_ratio - 1.0) * 0.12)
        if liq.target_level > 0 and liq.distance_to_target_pct <= self.max_liq_distance_pct:
            correct_dir = (is_long and liq.signal >= 0) or (not is_long and liq.signal <= 0)
            if correct_dir:
                boost += 0.04
        return boost

    def _resolve_reject(self, long_score, short_score, long_boost, short_boost,
                        struct_long, struct_short, struct_trend, zone_ctx, momentum_ok) -> str:
        if struct_trend == "range" and not struct_long and not struct_short:
            return "range_no_signal"
        if not struct_long and not struct_short:
            return "no_sweep_bos_sequence"
        if zone_ctx and not zone_ctx.all_bullish_zones and not zone_ctx.all_bearish_zones:
            return "no_active_zones_for_retest"
        if long_score < 0.15 and short_score < 0.15:
            return "price_not_near_zone"
        if not momentum_ok and long_score + long_boost < 0.40 and short_score + short_boost < 0.40:
            return "weak_momentum"
        return "insufficient_confluence"
