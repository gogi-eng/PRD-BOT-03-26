#!/usr/bin/env python3
"""Entry engine for breakout + pullback continuation with level-based exits."""
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
    """Trend-continuation entry engine with breakout and pullback scenarios."""

    def __init__(self, cfg):
        self.transformer_threshold = cfg.get("entry", "transformer_threshold", default=0.60)
        self.max_liq_distance_pct = cfg.get("entry", "max_liq_distance_pct", default=0.55)
        self.min_orderflow_imbalance = cfg.get("entry", "min_orderflow_imbalance", default=1.12)
        self.min_rr_ratio = cfg.get("entry", "min_rr_ratio", default=1.8)
        self.min_target_profit_pct = cfg.get("entry", "min_target_profit_pct", default=1.2)
        self.min_stop_distance_pct = cfg.get("entry", "min_stop_distance_pct", default=0.35)
        self.allowed_regimes = set(cfg.get("entry", "allowed_regimes", default=["trend", "breakout"]))
        self.atr_stop_mult = cfg.get("entry", "atr_stop_mult", default=1.6)
        self.liq_stop_buffer_atr = cfg.get("entry", "liq_stop_buffer_atr", default=0.35)
        self.breakout_lookback = cfg.get("entry", "breakout_lookback", default=20)
        self.pullback_lookback = cfg.get("entry", "pullback_lookback", default=8)
        self.level_tp_buffer_atr = cfg.get("entry", "level_tp_buffer_atr", default=0.20)

    def generate_signal(self, symbol: str, klines: List[Dict], current_price: float, market_analysis, regime_prediction, transformer_prediction, orderflow_snapshot, liq_analysis, atr_value: float = 0.0, zone_context=None) -> EntrySignal:
        signal = EntrySignal(entry_price=current_price)
        regime_value = regime_prediction.regime.value
        regime_ok = regime_value in self.allowed_regimes and market_analysis.can_trade
        liq_near = 0 < liq_analysis.distance_to_target_pct <= self.max_liq_distance_pct
        structure = self._detect_structure(klines, current_price, market_analysis)

        long_checks = self._long_checks(regime_ok, liq_near, structure, market_analysis, transformer_prediction, orderflow_snapshot, liq_analysis)
        short_checks = self._short_checks(regime_ok, liq_near, structure, market_analysis, transformer_prediction, orderflow_snapshot, liq_analysis)

        long_ready = all(long_checks.values())
        short_ready = all(short_checks.values())

        if not long_ready and not short_ready:
            reject_reason = self._resolve_reject_reason(structure, long_checks, short_checks)
            signal.filters_passed = {
                "regime": regime_ok,
                "heatmap_distance": liq_near,
                "transformer_long": long_checks["transformer_ok"],
                "transformer_short": short_checks["transformer_ok"],
                "orderflow_long": long_checks["orderflow_ok"],
                "orderflow_short": short_checks["orderflow_ok"],
                "breakout_long": structure["breakout_long"],
                "breakout_short": structure["breakout_short"],
                "pullback_long": structure["pullback_long"],
                "pullback_short": structure["pullback_short"],
            }
            signal.metadata["reject_reason"] = reject_reason
            return signal

        is_long = long_ready and (transformer_prediction.prob_up >= transformer_prediction.prob_down or not short_ready)
        if atr_value <= 0:
            atr_value = current_price * 0.008

        level_context = self._resolve_levels(klines, liq_analysis, current_price, is_long, zone_context=zone_context)
        target_level = level_context["target_level"]
        liq_stop = level_context["protective_level"]
        tp1_level = level_context.get("tp1_level", 0.0)
        tp2_level = level_context.get("tp2_level", target_level)

        atr_stop = current_price - atr_value * self.atr_stop_mult if is_long else current_price + atr_value * self.atr_stop_mult
        if is_long and liq_stop > 0:
            stop_loss = max(atr_stop, liq_stop - atr_value * self.liq_stop_buffer_atr)
        elif (not is_long) and liq_stop > 0:
            stop_loss = min(atr_stop, liq_stop + atr_value * self.liq_stop_buffer_atr)
        else:
            stop_loss = atr_stop

        min_stop_distance = current_price * (self.min_stop_distance_pct / 100)
        actual_stop_distance = abs(current_price - stop_loss)
        if actual_stop_distance < min_stop_distance:
            stop_loss = current_price - min_stop_distance if is_long else current_price + min_stop_distance

        risk = abs(current_price - stop_loss)
        if risk <= 0:
            return signal

        rr_target = current_price + risk * self.min_rr_ratio if is_long else current_price - risk * self.min_rr_ratio
        if is_long and tp2_level > current_price:
            take_profit = max(rr_target, tp2_level - atr_value * self.level_tp_buffer_atr)
        elif is_long and target_level > current_price:
            take_profit = max(rr_target, target_level - atr_value * self.level_tp_buffer_atr)
        elif (not is_long) and 0 < tp2_level < current_price:
            take_profit = min(rr_target, tp2_level + atr_value * self.level_tp_buffer_atr)
        elif (not is_long) and 0 < target_level < current_price:
            take_profit = min(rr_target, target_level + atr_value * self.level_tp_buffer_atr)
        else:
            take_profit = rr_target

        min_target_distance = current_price * (self.min_target_profit_pct / 100)
        actual_target_distance = abs(take_profit - current_price)
        if actual_target_distance < min_target_distance:
            take_profit = current_price + min_target_distance if is_long else current_price - min_target_distance

        reward = abs(take_profit - current_price)
        rr_ratio = reward / risk if risk > 0 else 0.0
        if rr_ratio + 1e-6 < self.min_rr_ratio:
            take_profit = current_price + risk * self.min_rr_ratio if is_long else current_price - risk * self.min_rr_ratio
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
            structure["trigger_reason"],
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
            "protective_liq_level": round(stop_loss, 8),
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
            "structure_breakout": structure["breakout_long"] if is_long else structure["breakout_short"],
            "structure_pullback": structure["pullback_long"] if is_long else structure["pullback_short"],
            "tp1_level": tp1_level,
            "tp2_level": tp2_level,
            "zone_confluence": zone_context.bullish_confluence if (zone_context and is_long) else zone_context.bearish_confluence if zone_context else False,
        }
        return signal

    def _long_checks(self, regime_ok, liq_near, structure, market, transformer, orderflow, liq):
        breakout_or_pullback = structure["breakout_long"] or structure["pullback_long"] or structure["continuation_long"]
        chop_ok = not (market.regime.value == "chop" and not structure["breakout_long"])
        liq_ok = (liq.signal >= 0 and liq.max_liq_cluster_above is not None and liq_near) or liq.signal > 0 or structure["breakout_long"]
        transformer_ok = (
            transformer.prob_up >= self.transformer_threshold
            or (structure["breakout_long"] and transformer.prob_up >= self.transformer_threshold - 0.05)
            or (structure["pullback_long"] and transformer.prob_up >= self.transformer_threshold - 0.04)
        )
        orderflow_ok = (
            orderflow.bullish_ratio >= self.min_orderflow_imbalance
            or (orderflow.volume_spike >= 1.05 and orderflow.bullish_ratio >= self.min_orderflow_imbalance - 0.08)
        )
        volume_ok = orderflow.volume_spike >= 1.03 or market.volume_expansion >= 1.05 or structure["pullback_long"]
        trend_ok = market.trend.value > 0 and market.htf_trend.value >= 0
        return {
            "regime_ok": regime_ok,
            "structure_ok": breakout_or_pullback,
            "chop_ok": chop_ok,
            "liq_ok": liq_ok,
            "transformer_ok": transformer_ok,
            "orderflow_ok": orderflow_ok,
            "trend_ok": trend_ok,
            "volume_ok": volume_ok,
        }

    def _short_checks(self, regime_ok, liq_near, structure, market, transformer, orderflow, liq):
        breakout_or_pullback = structure["breakout_short"] or structure["pullback_short"] or structure["continuation_short"]
        chop_ok = not (market.regime.value == "chop" and not structure["breakout_short"])
        liq_ok = (liq.signal <= 0 and liq.max_liq_cluster_below is not None and liq_near) or liq.signal < 0 or structure["breakout_short"]
        transformer_ok = (
            transformer.prob_down >= self.transformer_threshold
            or (structure["breakout_short"] and transformer.prob_down >= self.transformer_threshold - 0.05)
            or (structure["pullback_short"] and transformer.prob_down >= self.transformer_threshold - 0.04)
        )
        orderflow_ok = (
            orderflow.bearish_ratio >= self.min_orderflow_imbalance
            or (orderflow.volume_spike >= 1.05 and orderflow.bearish_ratio >= self.min_orderflow_imbalance - 0.08)
        )
        volume_ok = orderflow.volume_spike >= 1.03 or market.volume_expansion >= 1.05 or structure["pullback_short"]
        trend_ok = market.trend.value < 0 and market.htf_trend.value <= 0
        return {
            "regime_ok": regime_ok,
            "structure_ok": breakout_or_pullback,
            "chop_ok": chop_ok,
            "liq_ok": liq_ok,
            "transformer_ok": transformer_ok,
            "orderflow_ok": orderflow_ok,
            "trend_ok": trend_ok,
            "volume_ok": volume_ok,
        }

    def _resolve_reject_reason(self, structure, long_checks, short_checks) -> str:
        if not any([structure["breakout_long"], structure["breakout_short"], structure["pullback_long"], structure["pullback_short"], structure["continuation_long"], structure["continuation_short"]]):
            return "no_structure"
        for reason, key in [
            ("chop_without_breakout", "chop_ok"),
            ("regime_blocked", "regime_ok"),
            ("trend_misaligned", "trend_ok"),
            ("weak_volume", "volume_ok"),
            ("weak_orderflow", "orderflow_ok"),
            ("heatmap_not_confirmed", "liq_ok"),
            ("transformer_not_confirmed", "transformer_ok"),
        ]:
            if not long_checks.get(key, True) and not short_checks.get(key, True):
                return reason
        return "entry_filters"

    def _detect_structure(self, klines: List[Dict], current_price: float, market) -> Dict[str, bool | str]:
        if len(klines) < self.breakout_lookback + 2:
            return {
                "breakout_long": False,
                "breakout_short": False,
                "pullback_long": False,
                "pullback_short": False,
                "continuation_long": False,
                "continuation_short": False,
                "trigger_reason": "No structure",
            }
        highs = [float(k["high"]) for k in klines]
        lows = [float(k["low"]) for k in klines]
        closes = [float(k["close"]) for k in klines]
        breakout_high = max(highs[-self.breakout_lookback - 1 : -1])
        breakout_low = min(lows[-self.breakout_lookback - 1 : -1])
        recent_closes = closes[-self.pullback_lookback :]
        recent_high_band = max(highs[-4:])
        recent_low_band = min(lows[-4:])
        pullback_long = market.trend.value > 0 and min(recent_closes) <= market.ema_fast * 1.006 and current_price >= market.ema_fast * 0.999 and current_price > closes[-2]
        pullback_short = market.trend.value < 0 and max(recent_closes) >= market.ema_fast * 0.994 and current_price <= market.ema_fast * 1.001 and current_price < closes[-2]
        breakout_long = market.trend.value > 0 and current_price >= breakout_high * 0.998 and current_price >= recent_high_band * 0.999
        breakout_short = market.trend.value < 0 and current_price <= breakout_low * 1.002 and current_price <= recent_low_band * 1.001
        continuation_long = market.trend.value > 0 and current_price > closes[-3] > closes[-5] and current_price > market.ema_fast and market.volume_expansion >= 1.02
        continuation_short = market.trend.value < 0 and current_price < closes[-3] < closes[-5] and current_price < market.ema_fast and market.volume_expansion >= 1.02
        trigger_reason = (
            "Breakout continuation" if breakout_long or breakout_short else
            "Pullback continuation" if pullback_long or pullback_short else
            "Momentum continuation" if continuation_long or continuation_short else
            "No structure"
        )
        return {
            "breakout_long": breakout_long,
            "breakout_short": breakout_short,
            "pullback_long": pullback_long,
            "pullback_short": pullback_short,
            "continuation_long": continuation_long,
            "continuation_short": continuation_short,
            "trigger_reason": trigger_reason,
        }

    def _resolve_levels(self, klines: List[Dict], liq, current_price: float, is_long: bool, zone_context=None) -> Dict[str, float]:
        highs = sorted({float(k["high"]) for k in klines[-30:]})
        lows = sorted({float(k["low"]) for k in klines[-30:]})
        nearest_above = min((level for level in highs if level > current_price), default=0.0)
        nearest_below = max((level for level in lows if level < current_price), default=0.0)
        zone_support = max((level for level in (zone_context.support_levels if zone_context else []) if level < current_price), default=0.0)
        zone_resistance = min((level for level in (zone_context.resistance_levels if zone_context else []) if level > current_price), default=0.0)
        target_level = liq.target_level
        protective_level = 0.0
        tp1_level = 0.0
        tp2_level = 0.0
        if is_long:
            if target_level <= current_price:
                target_level = min((level for level in [nearest_above, zone_resistance, liq.max_liq_cluster_above.level if liq.max_liq_cluster_above else 0.0] if level > current_price), default=0.0)
            protective_level = max((level for level in [nearest_below, zone_support, liq.max_liq_cluster_below.level if liq.max_liq_cluster_below else 0.0] if 0 < level < current_price), default=0.0)
            tp_candidates = sorted(set(level for level in [target_level, zone_resistance, nearest_above] if level > current_price))
            if tp_candidates:
                tp1_level = tp_candidates[0]
                tp2_level = tp_candidates[1] if len(tp_candidates) > 1 else tp_candidates[0]
        else:
            if target_level >= current_price or target_level <= 0:
                below_candidates = [level for level in [nearest_below, zone_support, liq.max_liq_cluster_below.level if liq.max_liq_cluster_below else 0.0] if 0 < level < current_price]
                target_level = max(below_candidates) if below_candidates else 0.0
            protective_level = min((level for level in [nearest_above, zone_resistance, liq.max_liq_cluster_above.level if liq.max_liq_cluster_above else 0.0] if level > current_price), default=0.0)
            tp_candidates = sorted(set(level for level in [target_level, zone_support, nearest_below] if 0 < level < current_price), reverse=True)
            if tp_candidates:
                tp1_level = tp_candidates[0]
                tp2_level = tp_candidates[1] if len(tp_candidates) > 1 else tp_candidates[0]
        return {"target_level": target_level, "protective_level": protective_level, "tp1_level": tp1_level, "tp2_level": tp2_level or target_level}
