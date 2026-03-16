#!/usr/bin/env python3
"""
ENTRY ENGINE v5 — STRICT 5-POINT ТЗ

Pipeline (NO other logic allowed):
  1. HTF 4H TREND  →  only LONG if 4H bullish, only SHORT if 4H bearish
  2. LIQUIDITY SWEEP  →  mandatory: price must sweep liquidity first
  3. FVG / ORDER BLOCK  →  price must retest a zone after the sweep
  4. RISK/REWARD >= 2.0  →  trade only if RR meets minimum
  5. ENTRY

That's it. No extra filters, no score thresholds, no 15M checks.
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
    """Strict 5-point entry: 4H trend -> sweep -> zone retest -> RR check -> entry."""

    def __init__(self, cfg):
        self.min_rr_ratio = cfg.get("entry", "min_rr_ratio", default=2.0)
        self.min_target_profit_pct = cfg.get("entry", "min_target_profit_pct", default=1.2)
        self.min_stop_distance_pct = cfg.get("entry", "min_stop_distance_pct", default=0.5)
        self.sl_buffer_atr_mult = cfg.get("entry", "sl_buffer_atr_mult", default=0.5)
        self.zone_proximity_pct = cfg.get("entry", "zone_proximity_pct", default=0.4)
        self.max_spread_pct = cfg.get("entry", "max_spread_pct", default=0.08)
        self.max_funding_rate = cfg.get("entry", "max_funding_rate", default=0.05)

    def generate_signal(
        self, symbol: str, klines: List[Dict], current_price: float,
        market_analysis, regime_prediction, transformer_prediction,
        orderflow_snapshot, liq_analysis, atr_value: float = 0.0,
        zone_context=None, structure=None, funding_rate: float = 0.0,
        htf_4h_trend: int = 0,
    ) -> EntrySignal:
        signal = EntrySignal(entry_price=current_price)

        if not market_analysis.can_trade:
            signal.metadata["reject_reason"] = "market_blocked"
            return signal

        if atr_value <= 0:
            atr_value = current_price * 0.008

        # =====================================================
        # PRE-CHECK: spread & funding (execution safety only)
        # =====================================================
        spread_pct = orderflow_snapshot.spread_pct if hasattr(orderflow_snapshot, 'spread_pct') else 0.0
        if self.max_spread_pct > 0 and spread_pct > self.max_spread_pct:
            signal.metadata["reject_reason"] = f"spread_too_wide ({spread_pct:.3f}%)"
            return signal
        if self.max_funding_rate > 0 and abs(funding_rate) > self.max_funding_rate:
            signal.metadata["reject_reason"] = f"funding_rate_high ({funding_rate:.4f})"
            return signal

        # =====================================================
        # GATE 1: HTF 4H TREND — mandatory directional filter
        # Only LONG if 4H bullish. Only SHORT if 4H bearish.
        # Neutral = no trade.
        # =====================================================
        if htf_4h_trend == 0:
            signal.metadata["reject_reason"] = "4h_trend_neutral"
            return signal
        allowed_side = "BUY" if htf_4h_trend > 0 else "SELL"

        # =====================================================
        # GATE 2: LIQUIDITY SWEEP — mandatory
        # Sweep down (swept lows) = bullish setup (BUY)
        # Sweep up (swept highs) = bearish setup (SELL)
        # =====================================================
        has_structure = structure is not None
        sweep = structure.last_sweep if has_structure else None

        if sweep is None:
            signal.metadata["reject_reason"] = "no_liquidity_sweep"
            return signal

        sweep_side = "BUY" if sweep.direction == "down" else "SELL"

        # Sweep must align with 4H trend
        if sweep_side != allowed_side:
            signal.metadata["reject_reason"] = f"sweep_{sweep.direction}_vs_4h_{allowed_side}"
            return signal

        is_long = sweep_side == "BUY"

        # =====================================================
        # GATE 3: FVG / ORDER BLOCK RETEST — mandatory zone
        # Price must be in or near a valid zone after the sweep.
        # =====================================================
        if zone_context is None:
            signal.metadata["reject_reason"] = "no_zone_data"
            return signal

        if is_long:
            zone_in = zone_context.price_in_bullish_zone(current_price)
            zone_near = zone_context.price_near_bullish_zone(current_price, self.zone_proximity_pct) if not zone_in else None
        else:
            zone_in = zone_context.price_in_bearish_zone(current_price)
            zone_near = zone_context.price_near_bearish_zone(current_price, self.zone_proximity_pct) if not zone_in else None

        active_zone = zone_in or zone_near
        if active_zone is None:
            signal.metadata["reject_reason"] = "no_zone_retest"
            return signal

        # =====================================================
        # ALL 3 GATES PASSED — compute SL / TP levels
        # =====================================================
        reasons = [
            f"4H_{'BULL' if htf_4h_trend > 0 else 'BEAR'}",
            f"sweep_{sweep.direction}",
            f"{'in' if zone_in else 'near'}_{active_zone.kind}_{active_zone.bias}",
        ]

        bos = structure.last_bos if has_structure else None
        struct_trend = structure.trend.value if has_structure else "range"

        # --- SL from structure ---
        if is_long:
            if has_structure and structure.sweep_low > 0:
                sl = structure.sweep_low - atr_value * self.sl_buffer_atr_mult
            elif zone_context:
                sl = zone_context.structural_sl_long(current_price, atr_value)
            else:
                sl = current_price - atr_value * 2.5
            # TP from previous high / liquidity
            tp1 = structure.previous_high if has_structure and structure.previous_high > current_price else current_price + atr_value * 3.0
            if zone_context:
                _, struct_tp2 = zone_context.structural_tp_long(current_price, atr_value)
                tp2 = max(struct_tp2, tp1)
            else:
                tp2 = tp1 + atr_value * 2.0
            if liq_analysis.target_level > current_price and liq_analysis.signal > 0:
                tp2 = max(tp2, liq_analysis.target_level)
        else:
            if has_structure and structure.sweep_high > 0:
                sl = structure.sweep_high + atr_value * self.sl_buffer_atr_mult
            elif zone_context:
                sl = zone_context.structural_sl_short(current_price, atr_value)
            else:
                sl = current_price + atr_value * 2.5
            tp1 = structure.previous_low if has_structure and structure.previous_low < current_price else current_price - atr_value * 3.0
            if zone_context:
                _, struct_tp2 = zone_context.structural_tp_short(current_price, atr_value)
                tp2 = min(struct_tp2, tp1)
            else:
                tp2 = tp1 - atr_value * 2.0
            if liq_analysis.target_level > 0 and liq_analysis.target_level < current_price and liq_analysis.signal < 0:
                tp2 = min(tp2, liq_analysis.target_level)

        # Enforce minimum stop distance
        min_stop_dist = current_price * (self.min_stop_distance_pct / 100)
        if abs(current_price - sl) < min_stop_dist:
            sl = current_price - min_stop_dist if is_long else current_price + min_stop_dist

        risk = abs(current_price - sl)
        if risk <= 0:
            signal.metadata["reject_reason"] = "zero_risk"
            return signal

        # TP must satisfy min RR
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

        # =====================================================
        # GATE 4: RISK/REWARD CHECK — must be >= min_rr_ratio
        # =====================================================
        if rr_ratio < self.min_rr_ratio:
            signal.metadata["reject_reason"] = f"rr_too_low ({rr_ratio:.2f} < {self.min_rr_ratio})"
            return signal

        # =====================================================
        # ALL 4 GATES PASSED — BUILD SIGNAL
        # =====================================================
        confidence = min(0.90, 0.60 + active_zone.strength * 0.15 + (0.1 if bos else 0.0))
        side = "BUY" if is_long else "SELL"
        reasons.append(f"RR={rr_ratio:.1f}")

        signal.should_enter = True
        signal.side = side
        signal.confidence = round(confidence, 4)
        signal.stop_loss = round(sl, 8)
        signal.take_profit = round(take_profit, 8)
        signal.rr_ratio = round(rr_ratio, 2)
        signal.capital_score = round(confidence * rr_ratio, 4)
        signal.reasons = reasons
        signal.metadata = {
            "target_level": tp2,
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
            "entry_zone": f"{active_zone.kind}_{active_zone.bias}",
            "smc_score": round(confidence, 3),
            "struct_trend": struct_trend,
            "has_bos": bos is not None,
            "bos_direction": bos.direction if bos else "none",
            "has_sweep": True,
            "sweep_direction": sweep.direction,
            "funding_rate": funding_rate,
            "htf_4h_trend": htf_4h_trend,
        }
        return signal
