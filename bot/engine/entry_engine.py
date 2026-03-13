#!/usr/bin/env python3
"""
ENTRY ENGINE — ЕДИНСТВЕННЫЙ модуль принятия решений о входе.

Алгоритм:
1. HTF trend filter (ОБЯЗАТЕЛЬНО)
2. Минимум 1 подтверждение: sweep ИЛИ pullback ИЛИ RSI extreme
3. Confluence scoring
4. SL/TP расчёт
"""
from __future__ import annotations
from typing import Dict, List, Optional
from dataclasses import dataclass


@dataclass
class EntrySignal:
    """Сигнал на вход."""
    should_enter: bool = False
    side: str = ""
    confidence: float = 0.0
    entry_price: float = 0.0
    stop_loss: float = 0.0
    take_profit: float = 0.0
    rr_ratio: float = 0.0
    reasons: list = None
    filters_passed: dict = None

    def __post_init__(self):
        if self.reasons is None:
            self.reasons = []
        if self.filters_passed is None:
            self.filters_passed = {}


class EntryEngine:
    """
    Единственный Entry Engine бота.

    Вход если: HTF тренд + минимум 1 подтверждение (sweep / pullback / RSI)
    """

    def __init__(self, cfg):
        self.min_rr_ratio = cfg.get("trading", "min_rr_ratio", default=3.0)
        self.min_confluence = cfg.get("signals", "min_confluence_score", default=0.55)
        self.require_htf_trend = cfg.get("trading", "trend_filter_enabled", default=True)
        self.pullback_enabled = cfg.get("pullback", "enabled", default=True)

    def generate_signal(
        self,
        symbol: str,
        klines: List[Dict],
        market_analysis,
        sweep_signal,
        funding_signal=None,
        liq_analysis=None,
        atr_value: float = 0.0,
    ) -> EntrySignal:
        signal = EntrySignal()
        if not klines:
            return signal

        current_price = float(klines[-1]["close"])
        signal.entry_price = current_price

        # === 1. Market regime check ===
        if not market_analysis.can_trade:
            return signal

        # === 2. HTF Trend (обязательно) ===
        htf = market_analysis.htf_trend
        trend_direction = htf.value if htf.value != 0 else market_analysis.trend.value
        if trend_direction == 0:
            return signal

        # === 3. ADX minimum (тренд должен существовать) ===
        if market_analysis.adx < 15:
            return signal

        # === 4. Собираем подтверждения ===
        confluence_score = 0.0
        reasons = []
        confirmations = 0

        # Тренд
        confluence_score += 0.20
        reasons.append(f"HTF trend: {'bullish' if trend_direction > 0 else 'bearish'} (ADX={market_analysis.adx:.0f})")

        # Сильный тренд — бонус
        if market_analysis.adx > 30:
            confluence_score += 0.10
            reasons.append(f"Strong trend ADX={market_analysis.adx:.0f}")

        # Liquidity Sweep
        sweep_dir = 0
        if sweep_signal.detected:
            sweep_dir = sweep_signal.direction
            if sweep_dir == trend_direction:
                # Свип ПО тренду — сильный сигнал
                confluence_score += 0.30 * sweep_signal.strength
                confirmations += 1
                reasons.append(f"Sweep with trend: {sweep_signal.description}")
            else:
                # Свип ПРОТИВ тренда — не входим
                return signal

        # Pullback
        pullback_dir = self._detect_pullback(klines, market_analysis)
        if pullback_dir == trend_direction:
            confluence_score += 0.20
            confirmations += 1
            reasons.append(f"Pullback {'bullish' if pullback_dir > 0 else 'bearish'}")

        # RSI
        rsi = market_analysis.rsi
        if rsi < 35 and trend_direction > 0:
            confluence_score += 0.15
            confirmations += 1
            reasons.append(f"RSI oversold: {rsi:.1f}")
        elif rsi > 65 and trend_direction < 0:
            confluence_score += 0.15
            confirmations += 1
            reasons.append(f"RSI overbought: {rsi:.1f}")

        # Funding
        if funding_signal and funding_signal.signal != 0:
            if funding_signal.signal == trend_direction:
                confluence_score += 0.05
                reasons.append(f"Funding confirms: {funding_signal.reason}")
            elif funding_signal.strength >= 0.8:
                # Сильный funding ПРОТИВ нас — бонус не даём, но и не блокируем
                confluence_score -= 0.05

        # Liquidation clusters
        if liq_analysis and liq_analysis.signal == trend_direction:
            confluence_score += 0.05
            reasons.append(f"Liq magnet: {liq_analysis.magnet_direction}")

        # === 5. Нужно минимум 1 подтверждение помимо тренда ===
        if confirmations == 0:
            return signal

        # === 6. Confluence check ===
        if confluence_score < self.min_confluence:
            return signal

        # === 7. Определяем сторону ===
        entry_side = trend_direction

        # === 8. SL/TP ===
        if atr_value <= 0:
            atr_value = current_price * 0.01

        if entry_side > 0:
            sl_price = current_price - atr_value * 2.0
            tp_price = current_price + atr_value * self.min_rr_ratio * 2.0
        else:
            sl_price = current_price + atr_value * 2.0
            tp_price = current_price - atr_value * self.min_rr_ratio * 2.0

        # Sweep-based SL
        if sweep_signal.detected and sweep_signal.sweep_level > 0:
            if entry_side > 0:
                sl_from_sweep = sweep_signal.sweep_level - atr_value * 0.3
                if sl_from_sweep > 0 and sl_from_sweep < current_price:
                    sl_price = sl_from_sweep
            else:
                sl_from_sweep = sweep_signal.sweep_level + atr_value * 0.3
                if sl_from_sweep > current_price:
                    sl_price = sl_from_sweep

        # Минимальный SL = 1 ATR (не ближе!)
        min_sl_distance = atr_value * 1.0
        actual_distance = abs(current_price - sl_price)
        if actual_distance < min_sl_distance:
            if entry_side > 0:
                sl_price = current_price - min_sl_distance
            else:
                sl_price = current_price + min_sl_distance

        # RR
        risk = abs(current_price - sl_price)
        reward = abs(tp_price - current_price)
        rr = reward / risk if risk > 0 else 0

        if rr < self.min_rr_ratio:
            if entry_side > 0:
                tp_price = current_price + risk * self.min_rr_ratio
            else:
                tp_price = current_price - risk * self.min_rr_ratio
            rr = self.min_rr_ratio

        signal.should_enter = True
        signal.side = "BUY" if entry_side > 0 else "SELL"
        signal.confidence = min(1.0, confluence_score)
        signal.entry_price = current_price
        signal.stop_loss = round(sl_price, 8)
        signal.take_profit = round(tp_price, 8)
        signal.rr_ratio = round(rr, 2)
        signal.reasons = reasons
        return signal

    def _detect_pullback(self, klines: List[Dict], market_analysis) -> int:
        if not self.pullback_enabled or len(klines) < 20:
            return 0

        closes = [float(k["close"]) for k in klines[-20:]]
        trend = market_analysis.trend.value
        if trend == 0:
            return 0

        ema = market_analysis.ema_fast
        current = closes[-1]
        prev = closes[-3] if len(closes) > 3 else closes[0]

        if trend > 0:
            # Цена была ниже/около EMA, возвращается выше
            if prev < ema * 1.002 and current >= ema * 0.998 and current > prev:
                return 1
        elif trend < 0:
            # Цена была выше/около EMA, возвращается ниже
            if prev > ema * 0.998 and current <= ema * 1.002 and current < prev:
                return -1
        return 0
