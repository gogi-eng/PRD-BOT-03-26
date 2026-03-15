#!/usr/bin/env python3
"""SMC Entry Engine — entries based on Fair Value Gaps and Order Blocks."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List


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
    """SMC-based entry engine: FVG/OB touch + trend alignment + confluence scoring."""

    def __init__(self, cfg):
        self.min_rr_ratio = cfg.get("entry", "min_rr_ratio", default=1.3)
        self.min_target_profit_pct = cfg.get("entry", "min_target_profit_pct", default=1.2)
        self.min_stop_distance_pct = cfg.get("entry", "min_stop_distance_pct", default=0.35)
        self.allowed_regimes = set(cfg.get("entry", "allowed_regimes", default=["trend", "breakout", "chop"]))
        self.atr_stop_mult = cfg.get("entry", "atr_stop_mult", default=1.6)
        self.liq_stop_buffer_atr = cfg.get("entry", "liq_stop_buffer_atr", default=0.35)
        self.level_tp_buffer_atr = cfg.get("entry", "level_tp_buffer_atr", default=0.20)
        self.breakout_lookback = cfg.get("entry", "breakout_lookback", default=20)
        self.zone_proximity_pct = cfg.get("entry", "zone_proximity_pct", default=0.4)
        # Transformer / orderflow / heatmap are soft boosters, not hard gates
        self.transformer_threshold = cfg.get("entry", "transformer_threshold", default=0.54)
        self.max_liq_distance_pct = cfg.get("entry", "max_liq_distance_pct", default=1.30)
        self.min_orderflow_imbalance = cfg.get("entry", "min_orderflow_imbalance", default=1.03)

    def generate_signal(
        self, symbol: str, klines: List[Dict], current_price: float,
        market_analysis, regime_prediction, transformer_prediction,
        orderflow_snapshot, liq_analysis, atr_value: float = 0.0,
        zone_context=None,
    ) -> EntrySignal:
        signal = EntrySignal(entry_price=current_price)

        regime_value = regime_prediction.regime.value
        if regime_value not in self.allowed_regimes or not market_analysis.can_trade:
            signal.metadata["reject_reason"] = "regime_blocked" if market_analysis.can_trade else "market_blocked"
            return signal

        if atr_value <= 0:
            atr_value = current_price * 0.008

        # --- Core SMC logic: detect price interaction with FVG/OB zones ---
        long_score, long_reasons, long_zone = self._smc_long_score(current_price, market_analysis, zone_context, atr_value)
        short_score, short_reasons, short_zone = self._smc_short_score(current_price, market_analysis, zone_context, atr_value)

        # --- Breakout detection as additional entry scenario ---
        structure = self._detect_breakout(klines, current_price, market_analysis)
        if structure["breakout_long"] and long_score < 0.3:
            long_score = max(long_score, 0.35)
            long_reasons.append("breakout_continuation")
        if structure["breakout_short"] and short_score < 0.3:
            short_score = max(short_score, 0.35)
            short_reasons.append("breakout_continuation")

        # --- Soft boosters from transformer, orderflow, heatmap ---
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

        # --- Minimum threshold to enter ---
        min_score = 0.40
        can_long = long_total >= min_score and market_analysis.trend.value >= 0
        can_short = short_total >= min_score and market_analysis.trend.value <= 0

        if not can_long and not can_short:
            reject = self._resolve_smc_reject(long_score, short_score, long_boost, short_boost, structure, market_analysis, zone_context)
            signal.metadata["reject_reason"] = reject
            signal.metadata.update({
                "long_smc_score": round(long_score, 3),
                "short_smc_score": round(short_score, 3),
                "long_boost": round(long_boost, 3),
                "short_boost": round(short_boost, 3),
            })
            return signal

        # Choose direction
        is_long = can_long and (long_total >= short_total or not can_short)

        # --- Compute SL/TP from structure ---
        if is_long:
            sl = zone_context.structural_sl_long(current_price, atr_value) if zone_context else current_price - atr_value * self.atr_stop_mult
            tp1, tp2 = zone_context.structural_tp_long(current_price, atr_value) if zone_context else (current_price + atr_value * 2.5, current_price + atr_value * 4.0)
            total_score = long_total
            reasons = long_reasons
            entry_zone = long_zone
        else:
            sl = zone_context.structural_sl_short(current_price, atr_value) if zone_context else current_price + atr_value * self.atr_stop_mult
            tp1, tp2 = zone_context.structural_tp_short(current_price, atr_value) if zone_context else (current_price - atr_value * 2.5, current_price - atr_value * 4.0)
            total_score = short_total
            reasons = short_reasons
            entry_zone = short_zone

        # --- Enforce minimum distances ---
        min_stop_distance = current_price * (self.min_stop_distance_pct / 100)
        if abs(current_price - sl) < min_stop_distance:
            sl = current_price - min_stop_distance if is_long else current_price + min_stop_distance

        risk = abs(current_price - sl)
        if risk <= 0:
            signal.metadata["reject_reason"] = "zero_risk"
            return signal

        # TP: ensure at least min RR
        rr_min_tp = current_price + risk * self.min_rr_ratio if is_long else current_price - risk * self.min_rr_ratio
        take_profit = tp2
        if is_long:
            take_profit = max(take_profit, rr_min_tp)
        else:
            take_profit = min(take_profit, rr_min_tp)

        min_target_distance = current_price * (self.min_target_profit_pct / 100)
        if abs(take_profit - current_price) < min_target_distance:
            take_profit = current_price + min_target_distance if is_long else current_price - min_target_distance

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
        signal.reasons = reasons + [
            f"RR={rr_ratio:.1f}",
            f"regime={regime_value}",
            f"trend={market_analysis.trend.name}",
            f"htf={market_analysis.htf_trend.name}",
        ]
        signal.metadata = {
            "target_level": tp2,
            "protective_liq_level": round(sl, 8),
            "transformer_prob_up": transformer_prediction.prob_up,
            "transformer_prob_down": transformer_prediction.prob_down,
            "transformer_prob_flat": transformer_prediction.prob_flat,
            "regime": regime_value,
            "orderflow_bullish_ratio": orderflow_snapshot.bullish_ratio,
            "orderflow_bearish_ratio": orderflow_snapshot.bearish_ratio,
            "spread_pct": orderflow_snapshot.spread_pct,
            "liq_distance_pct": liq_analysis.distance_to_target_pct,
            "liq_signal": liq_analysis.signal,
            "liq_magnet": liq_analysis.magnet_direction,
            "liq_density": liq_analysis.target_density,
            "structure_breakout": structure.get("breakout_long" if is_long else "breakout_short", False),
            "structure_pullback": False,
            "tp1_level": tp1,
            "tp2_level": tp2,
            "zone_confluence": (zone_context.bullish_confluence if is_long else zone_context.bearish_confluence) if zone_context else False,
            "entry_zone": f"{entry_zone.kind}_{entry_zone.bias}" if entry_zone else "none",
            "smc_score": round(long_score if is_long else short_score, 3),
            "boost_score": round(long_boost if is_long else short_boost, 3),
        }
        return signal

    def _smc_long_score(self, price, market, zone_ctx, atr) -> tuple[float, list, object]:
        """Score for long entry based on SMC zone proximity + trend alignment."""
        if zone_ctx is None:
            return 0.0, [], None

        score = 0.0
        reasons = []
        best_zone = None

        # Price inside/near a bullish FVG or OB?
        zone_in = zone_ctx.price_in_bullish_zone(price)
        zone_near = zone_ctx.price_near_bullish_zone(price, self.zone_proximity_pct) if not zone_in else None
        active_zone = zone_in or zone_near

        if active_zone:
            score += 0.30 if zone_in else 0.20
            reasons.append(f"price {'in' if zone_in else 'near'} bullish {active_zone.kind} [{active_zone.low:.4f}-{active_zone.high:.4f}]")
            score += active_zone.strength * 0.15
            best_zone = active_zone

        # Confluence: both FVG and OB present below price
        if zone_ctx.bullish_confluence:
            score += 0.10
            reasons.append("FVG+OB confluence")

        # Trend alignment
        if market.htf_trend.value > 0:
            score += 0.15
            reasons.append("HTF bullish")
        elif market.htf_trend.value == 0:
            score += 0.05

        if market.trend.value > 0:
            score += 0.10
            reasons.append("LTF bullish")

        # ADX (trend strength)
        if market.adx >= 20:
            score += 0.05

        return score, reasons, best_zone

    def _smc_short_score(self, price, market, zone_ctx, atr) -> tuple[float, list, object]:
        """Score for short entry based on SMC zone proximity + trend alignment."""
        if zone_ctx is None:
            return 0.0, [], None

        score = 0.0
        reasons = []
        best_zone = None

        zone_in = zone_ctx.price_in_bearish_zone(price)
        zone_near = zone_ctx.price_near_bearish_zone(price, self.zone_proximity_pct) if not zone_in else None
        active_zone = zone_in or zone_near

        if active_zone:
            score += 0.30 if zone_in else 0.20
            reasons.append(f"price {'in' if zone_in else 'near'} bearish {active_zone.kind} [{active_zone.low:.4f}-{active_zone.high:.4f}]")
            score += active_zone.strength * 0.15
            best_zone = active_zone

        if zone_ctx.bearish_confluence:
            score += 0.10
            reasons.append("FVG+OB confluence")

        if market.htf_trend.value < 0:
            score += 0.15
            reasons.append("HTF bearish")
        elif market.htf_trend.value == 0:
            score += 0.05

        if market.trend.value < 0:
            score += 0.10
            reasons.append("LTF bearish")

        if market.adx >= 20:
            score += 0.05

        return score, reasons, best_zone

    def _compute_boost(self, transformer_prob, flow_ratio, liq, is_long) -> float:
        """Soft score boost from transformer, orderflow, heatmap. Not a gate."""
        boost = 0.0
        # Transformer
        if transformer_prob >= self.transformer_threshold:
            boost += min(0.12, (transformer_prob - 0.5) * 0.3)
        elif transformer_prob >= self.transformer_threshold - 0.06:
            boost += 0.04

        # Orderflow
        if flow_ratio >= self.min_orderflow_imbalance:
            boost += min(0.08, (flow_ratio - 1.0) * 0.15)
        elif flow_ratio >= 1.0:
            boost += 0.02

        # Heatmap proximity
        if liq.target_level > 0 and liq.distance_to_target_pct <= self.max_liq_distance_pct:
            correct_dir = (is_long and liq.signal >= 0) or (not is_long and liq.signal <= 0)
            if correct_dir:
                boost += 0.06
            else:
                boost += 0.02

        return boost

    def _detect_breakout(self, klines, current_price, market) -> dict:
        """Simple breakout detection as supplementary signal."""
        result = {"breakout_long": False, "breakout_short": False}
        if len(klines) < self.breakout_lookback + 2:
            return result
        highs = [float(k["high"]) for k in klines]
        lows = [float(k["low"]) for k in klines]
        breakout_high = max(highs[-self.breakout_lookback - 1: -1])
        breakout_low = min(lows[-self.breakout_lookback - 1: -1])
        if market.trend.value > 0 and current_price >= breakout_high * 0.998:
            result["breakout_long"] = True
        if market.trend.value < 0 and current_price <= breakout_low * 1.002:
            result["breakout_short"] = True
        return result

    def _resolve_smc_reject(self, long_score, short_score, long_boost, short_boost, structure, market, zone_ctx) -> str:
        """Determine the specific reason for rejection."""
        if zone_ctx is None:
            return "no_zone_data"

        no_active_zones = not zone_ctx.all_bullish_zones and not zone_ctx.all_bearish_zones
        if no_active_zones:
            return "no_active_smc_zones"

        if long_score < 0.15 and short_score < 0.15:
            return "price_not_near_any_zone"

        if long_score >= 0.15 and market.htf_trend.value < 0:
            return "zone_vs_trend_conflict"
        if short_score >= 0.15 and market.htf_trend.value > 0:
            return "zone_vs_trend_conflict"

        if long_boost < 0.05 and short_boost < 0.05:
            return "weak_confirmation"

        if market.regime.value == "chop" and not structure.get("breakout_long") and not structure.get("breakout_short"):
            return "chop_no_breakout"

        return "insufficient_confluence"
