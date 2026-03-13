#!/usr/bin/env python3
"""Entry engine rewritten around transformer + heatmap + orderflow + regime."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Optional


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
    """Strict entry engine from the new specification."""

    def __init__(self, cfg):
        self.transformer_threshold = cfg.get("entry", "transformer_threshold", default=0.62)
        self.max_liq_distance_pct = cfg.get("entry", "max_liq_distance_pct", default=0.4)
        self.min_orderflow_imbalance = cfg.get("entry", "min_orderflow_imbalance", default=1.2)
        self.min_rr_ratio = cfg.get("entry", "min_rr_ratio", default=1.8)
        self.allowed_regimes = set(cfg.get("entry", "allowed_regimes", default=["trend", "breakout"]))
        self.atr_stop_mult = cfg.get("entry", "atr_stop_mult", default=1.6)
        self.liq_stop_buffer_atr = cfg.get("entry", "liq_stop_buffer_atr", default=0.35)

    def generate_signal(self, symbol: str, current_price: float, market_analysis, regime_prediction, transformer_prediction, orderflow_snapshot, liq_analysis, atr_value: float = 0.0) -> EntrySignal:
        signal = EntrySignal(entry_price=current_price)
        regime_value = regime_prediction.regime.value
        regime_ok = regime_value in self.allowed_regimes and market_analysis.can_trade
        liq_near = 0 < liq_analysis.distance_to_target_pct <= self.max_liq_distance_pct

        long_ready = (
            transformer_prediction.prob_up >= self.transformer_threshold
            and liq_analysis.signal >= 0
            and liq_analysis.max_liq_cluster_above is not None
            and liq_near
            and orderflow_snapshot.bullish_ratio >= self.min_orderflow_imbalance
            and regime_ok
        )
        short_ready = (
            transformer_prediction.prob_down >= self.transformer_threshold
            and liq_analysis.signal <= 0
            and liq_analysis.max_liq_cluster_below is not None
            and liq_near
            and orderflow_snapshot.bearish_ratio >= self.min_orderflow_imbalance
            and regime_ok
        )

        if not long_ready and not short_ready:
            signal.filters_passed = {
                "regime": regime_ok,
                "heatmap_distance": liq_near,
                "transformer_long": transformer_prediction.prob_up >= self.transformer_threshold,
                "transformer_short": transformer_prediction.prob_down >= self.transformer_threshold,
                "orderflow_long": orderflow_snapshot.bullish_ratio >= self.min_orderflow_imbalance,
                "orderflow_short": orderflow_snapshot.bearish_ratio >= self.min_orderflow_imbalance,
            }
            return signal

        is_long = long_ready and (transformer_prediction.prob_up >= transformer_prediction.prob_down or not short_ready)
        target_cluster = liq_analysis.max_liq_cluster_above if is_long else liq_analysis.max_liq_cluster_below
        target_level = target_cluster.level if target_cluster else liq_analysis.target_level
        if atr_value <= 0:
            atr_value = current_price * 0.008

        atr_stop = current_price - atr_value * self.atr_stop_mult if is_long else current_price + atr_value * self.atr_stop_mult
        liq_stop = 0.0
        if is_long and liq_analysis.max_liq_cluster_below:
            liq_stop = liq_analysis.max_liq_cluster_below.level - atr_value * self.liq_stop_buffer_atr
            stop_loss = max(atr_stop, liq_stop)
        elif (not is_long) and liq_analysis.max_liq_cluster_above:
            liq_stop = liq_analysis.max_liq_cluster_above.level + atr_value * self.liq_stop_buffer_atr
            stop_loss = min(atr_stop, liq_stop)
        else:
            stop_loss = atr_stop

        risk = abs(current_price - stop_loss)
        if risk <= 0:
            return signal

        rr_target = current_price + risk * self.min_rr_ratio if is_long else current_price - risk * self.min_rr_ratio
        take_profit = max(target_level, rr_target) if is_long and target_level > 0 else min(target_level, rr_target) if (not is_long and target_level > 0) else rr_target
        reward = abs(take_profit - current_price)
        rr_ratio = reward / risk if risk > 0 else 0.0
        if rr_ratio + 1e-6 < self.min_rr_ratio:
            return signal

        transformer_edge = transformer_prediction.prob_up if is_long else transformer_prediction.prob_down
        flow_edge = orderflow_snapshot.bullish_ratio if is_long else orderflow_snapshot.bearish_ratio
        proximity_score = max(0.0, 1.0 - (liq_analysis.distance_to_target_pct / max(self.max_liq_distance_pct, 0.0001)))
        confidence = min(0.99, transformer_edge * 0.5 + min(flow_edge / 2.0, 1.0) * 0.25 + proximity_score * 0.15 + regime_prediction.confidence * 0.1)

        side = "BUY" if is_long else "SELL"
        signal.should_enter = True
        signal.side = side
        signal.confidence = round(confidence, 4)
        signal.stop_loss = round(stop_loss, 8)
        signal.take_profit = round(take_profit, 8)
        signal.rr_ratio = round(rr_ratio, 2)
        signal.capital_score = round(confidence * rr_ratio, 4)
        signal.reasons = [
            f"Transformer {('up' if is_long else 'down')}={transformer_edge:.2f}",
            f"Heatmap target={target_level:.4f} dist={liq_analysis.distance_to_target_pct:.3f}%",
            f"Orderflow {('bullish' if is_long else 'bearish')} ratio={flow_edge:.2f}",
            f"Regime={regime_value} conf={regime_prediction.confidence:.2f}",
        ]
        signal.filters_passed = {
            "transformer": True,
            "heatmap": True,
            "orderflow": True,
            "regime": True,
        }
        signal.metadata = {
            "target_level": target_level,
            "protective_liq_level": liq_stop,
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
        }
        return signal
