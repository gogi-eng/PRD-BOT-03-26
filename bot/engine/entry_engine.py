#!/usr/bin/env python3
"""
ENTRY ENGINE v4 — STRICT QUANT PIPELINE

Architecture:
  MARKET SCANNER
        │
        ▼
  HTF TREND (4H) ← only BUY if 4H bullish, only SELL if 4H bearish
        │
        ▼
  LIQUIDITY SWEEP ← mandatory: price must sweep liquidity first
        │
        ▼
  FVG / ORDER BLOCK ← price must retest a zone
        │
        ▼
  VOLUME CONFIRMATION ← volume spike or BOS volume confirmed
        │
        ▼
  AI TRADE FILTER ← final gate (handled in main.py)
        │
        ▼
  ENTRY

Result: 1000 signals → 990 filtered → 10 trades → 7 profit
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
    """Strict quant entry: 4H trend → sweep → zone retest → volume → entry."""

    def __init__(self, cfg):
        self.min_rr_ratio = cfg.get("entry", "min_rr_ratio", default=2.5)
        self.min_target_profit_pct = cfg.get("entry", "min_target_profit_pct", default=1.2)
        self.min_stop_distance_pct = cfg.get("entry", "min_stop_distance_pct", default=0.5)
        self.sl_buffer_atr_mult = cfg.get("entry", "sl_buffer_atr_mult", default=0.5)
        self.zone_proximity_pct = cfg.get("entry", "zone_proximity_pct", default=0.4)
        self.min_smc_score = cfg.get("entry", "min_smc_score", default=0.70)
        self.min_volatility_pct = cfg.get("entry", "min_volatility_pct", default=0.04)
        self.min_orderflow_imbalance = cfg.get("entry", "min_orderflow_imbalance", default=1.10)
        self.transformer_threshold = cfg.get("entry", "transformer_threshold", default=0.54)
        self.max_liq_distance_pct = cfg.get("entry", "max_liq_distance_pct", default=1.30)
        self.max_spread_pct = cfg.get("entry", "max_spread_pct", default=0.08)
        self.max_funding_rate = cfg.get("entry", "max_funding_rate", default=0.05)
        self.require_sweep = cfg.get("entry", "require_sweep", default=True)
        self.require_4h_trend = cfg.get("entry", "require_4h_trend", default=True)

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
        # GATE 1: EXECUTION PRE-CHECKS (spread, funding, volatility)
        # =====================================================
        spread_pct = orderflow_snapshot.spread_pct if hasattr(orderflow_snapshot, 'spread_pct') else 0.0
        if self.max_spread_pct > 0 and spread_pct > self.max_spread_pct:
            signal.metadata["reject_reason"] = f"spread_too_wide ({spread_pct:.3f}%)"
            return signal
        if self.max_funding_rate > 0 and abs(funding_rate) > self.max_funding_rate:
            signal.metadata["reject_reason"] = f"funding_rate_high ({funding_rate:.4f})"
            return signal
        volatility_pct = (atr_value / current_price * 100) if current_price > 0 else 0
        if self.min_volatility_pct > 0 and volatility_pct < self.min_volatility_pct:
            signal.metadata["reject_reason"] = f"low_volatility ({volatility_pct:.2f}%)"
            return signal

        # =====================================================
        # GATE 2: HTF 4H TREND — mandatory directional filter
        # =====================================================
        # htf_4h_trend: 1=bullish, -1=bearish, 0=neutral
        # If 4H bullish → only BUY. If 4H bearish → only SELL.
        if self.require_4h_trend:
            if htf_4h_trend == 0:
                signal.metadata["reject_reason"] = "4h_trend_neutral"
                return signal
            allowed_side = "BUY" if htf_4h_trend > 0 else "SELL"
        else:
            allowed_side = None  # both allowed

        # Also check 15M (HTF) alignment
        htf_15m = market_analysis.htf_trend.value
        ltf = market_analysis.trend.value

        # =====================================================
        # GATE 3: LIQUIDITY SWEEP — mandatory
        # =====================================================
        has_structure = structure is not None
        sweep = structure.last_sweep if has_structure else None
        bos = structure.last_bos if has_structure else None
        struct_trend = structure.trend.value if has_structure else "range"

        if self.require_sweep and sweep is None:
            signal.metadata["reject_reason"] = "no_liquidity_sweep"
            signal.metadata["struct_trend"] = struct_trend
            signal.metadata["has_bos"] = bos is not None
            return signal

        # Determine direction from sweep
        # sweep_down (swept lows) → bullish setup (BUY)
        # sweep_up (swept highs) → bearish setup (SELL)
        if sweep:
            sweep_side = "BUY" if sweep.direction == "down" else "SELL"
        else:
            sweep_side = allowed_side or "BUY"

        # Check sweep aligns with 4H trend
        if allowed_side and sweep_side != allowed_side:
            signal.metadata["reject_reason"] = f"sweep_{sweep.direction}_vs_4h_{allowed_side}"
            return signal

        is_long = sweep_side == "BUY"

        # Additional trend alignment check
        if is_long and (htf_15m < 0 or ltf < 0):
            signal.metadata["reject_reason"] = "sweep_long_but_15m_bearish"
            return signal
        if not is_long and (htf_15m > 0 or ltf > 0):
            signal.metadata["reject_reason"] = "sweep_short_but_15m_bullish"
            return signal

        # =====================================================
        # GATE 4: FVG / ORDER BLOCK RETEST — mandatory zone
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
            signal.metadata["struct_trend"] = struct_trend
            signal.metadata["sweep_direction"] = sweep.direction if sweep else "none"
            return signal

        # =====================================================
        # GATE 5: VOLUME CONFIRMATION
        # =====================================================
        volume_confirmed = False
        if bos and bos.volume_confirmed:
            volume_confirmed = True
        if has_structure and structure.volume_spike:
            volume_confirmed = True
        flow_ratio = orderflow_snapshot.bullish_ratio if is_long else orderflow_snapshot.bearish_ratio
        if flow_ratio >= self.min_orderflow_imbalance:
            volume_confirmed = True

        if not volume_confirmed:
            signal.metadata["reject_reason"] = "no_volume_confirmation"
            return signal

        # =====================================================
        # ALL GATES PASSED — compute score and levels
        # =====================================================
        score = 0.0
        reasons = []

        # Sweep
        score += 0.25
        reasons.append(f"sweep_{sweep.direction}" if sweep else "sweep_implied")

        # BOS
        if bos:
            score += 0.15
            reasons.append(f"BOS_{bos.direction}")
            if bos.volume_confirmed:
                score += 0.05
                reasons.append("BOS_vol_confirmed")

        # Zone retest
        score += 0.20 if zone_in else 0.12
        reasons.append(f"{'in' if zone_in else 'near'}_{active_zone.kind}_{active_zone.bias}")
        score += active_zone.strength * 0.08

        # Trend alignment (all aligned = bonus)
        all_aligned = (
            (is_long and htf_4h_trend > 0 and htf_15m >= 0 and ltf >= 0 and struct_trend != "down")
            or (not is_long and htf_4h_trend < 0 and htf_15m <= 0 and ltf <= 0 and struct_trend != "up")
        )
        if all_aligned:
            score += 0.12
            reasons.append("full_trend_alignment")
        else:
            score += 0.05

        # Momentum
        if has_structure and structure.momentum_confirmed:
            score += 0.08
            reasons.append("momentum")

        # Volume/orderflow
        score += 0.05
        if flow_ratio >= 1.2:
            score += 0.05
            reasons.append(f"strong_flow ({flow_ratio:.2f})")

        # Transformer soft boost
        t_prob = transformer_prediction.prob_up if is_long else transformer_prediction.prob_down
        if t_prob >= self.transformer_threshold:
            score += min(0.08, (t_prob - 0.5) * 0.2)

        # Confluence bonus
        if is_long and zone_context.bullish_confluence:
            score += 0.05
            reasons.append("FVG+OB_confluence")
        elif not is_long and zone_context.bearish_confluence:
            score += 0.05
            reasons.append("FVG+OB_confluence")

        # =====================================================
        # FINAL CHECK: minimum score
        # =====================================================
        if score < self.min_smc_score:
            signal.metadata["reject_reason"] = f"score_too_low ({score:.2f} < {self.min_smc_score})"
            signal.metadata["score"] = round(score, 3)
            return signal

        # =====================================================
        # COMPUTE SL / TP from structure
        # =====================================================
        if is_long:
            # SL = sweep_low - ATR buffer
            if has_structure and structure.sweep_low > 0:
                sl = structure.sweep_low - atr_value * self.sl_buffer_atr_mult
            elif zone_context:
                sl = zone_context.structural_sl_long(current_price, atr_value)
            else:
                sl = current_price - atr_value * 2.5
            # TP1 = previous high, TP2 = next liquidity/resistance
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
        if rr_ratio < self.min_rr_ratio:
            take_profit = current_price + risk * self.min_rr_ratio if is_long else current_price - risk * self.min_rr_ratio
            reward = abs(take_profit - current_price)
            rr_ratio = reward / risk if risk > 0 else 0.0

        # =====================================================
        # BUILD SIGNAL
        # =====================================================
        confidence = min(0.95, score)
        side = "BUY" if is_long else "SELL"

        signal.should_enter = True
        signal.side = side
        signal.confidence = round(confidence, 4)
        signal.stop_loss = round(sl, 8)
        signal.take_profit = round(take_profit, 8)
        signal.rr_ratio = round(rr_ratio, 2)
        signal.capital_score = round(confidence * rr_ratio, 4)
        signal.reasons = reasons + [f"RR={rr_ratio:.1f}", f"4H={'bull' if htf_4h_trend > 0 else 'bear'}"]
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
            "smc_score": round(score, 3),
            "boost_score": 0.0,
            "struct_trend": struct_trend,
            "has_bos": bos is not None,
            "bos_direction": bos.direction if bos else "none",
            "bos_volume_confirmed": bos.volume_confirmed if bos else False,
            "has_sweep": sweep is not None,
            "sweep_direction": sweep.direction if sweep else "none",
            "momentum_confirmed": structure.momentum_confirmed if has_structure else False,
            "volume_spike": structure.volume_spike if has_structure else False,
            "funding_rate": funding_rate,
            "htf_4h_trend": htf_4h_trend,
            "htf_15m_trend": htf_15m,
            "ltf_trend": ltf,
        }
        return signal
