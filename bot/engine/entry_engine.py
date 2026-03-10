#!/usr/bin/env python3
"""
ENTRY ENGINE — ЕДИНСТВЕННЫЙ модуль принятия решений о входе.

Алгоритм (из оценки):
1. HTF trend filter
2. Liquidity sweep detection
3. Pullback detection
4. Entry signal generation

Заменяет: smart_entry, smart_entry_v2, smart_entry_v4, smc_strategy, etc.
"""
from __future__ import annotations
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass


@dataclass
class EntrySignal:
    """Сигнал на вход."""
    should_enter: bool = False
    side: str = ""  # "BUY" or "SELL"
    confidence: float = 0.0  # 0-1
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

    Pipeline:
    1. Market Analyzer → trend + regime
    2. Liquidity Sweep → reversal signal
    3. Pullback check → good entry point
    4. Confluence scoring → combine signals
    5. Filters: funding, correlation, liquidation clusters
    6. AI filter (optional)
    """

    def __init__(self, cfg):
        self.min_rr_ratio = cfg.get("trading", "min_rr_ratio", default=2.2)
        self.min_confluence = cfg.get("signals", "min_confluence_score", default=0.50)
        self.min_atr_pct = cfg.get("atr", "min_atr_pct", default=0.50)
        self.require_htf_trend = cfg.get("trading", "trend_filter_enabled", default=True)
        self.pullback_enabled = cfg.get("pullback", "enabled", default=True)
        self.pullback_lookback = cfg.get("pullback", "lookback_bars", default=100)
        self.pullback_min_swing_pct = cfg.get("pullback", "min_swing_pct", default=0.5)

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
        """
        Генерация сигнала на вход.

        Шаги:
        1. Проверка режима рынка (можно ли торговать)
        2. Определение направления по HTF тренду
        3. Ликвидити свип → подтверждение разворота
        4. Pullback → хорошая точка входа
        5. Расчёт SL/TP/RR
        6. Confluence scoring
        """
        signal = EntrySignal()
        if not klines:
            return signal

        current_price = float(klines[-1]["close"])
        signal.entry_price = current_price

        # === 1. Market regime check ===
        if not market_analysis.can_trade:
            return signal

        # === 2. HTF Trend filter ===
        if self.require_htf_trend:
            htf = market_analysis.htf_trend
            if htf.value == 0:
                # Нет чёткого HTF тренда → разрешаем свипы
                pass
            # HTF тренд задает приоритетное направление
            trend_direction = htf.value if htf.value != 0 else market_analysis.trend.value
        else:
            trend_direction = market_analysis.trend.value

        # === 3. Signal generation ===
        confluence_score = 0.0
        reasons = []

        # --- HTF Trend REQUIRED ---
        if trend_direction == 0:
            return signal  # Нет тренда — не торгуем

        # --- Liquidity Sweep (основной сигнал) ---
        if sweep_signal.detected:
            sweep_dir = sweep_signal.direction  # 1 = bullish, -1 = bearish

            # КРИТИЧНО: свип ДОЛЖЕН совпадать с HTF трендом
            if sweep_dir != trend_direction:
                return signal  # Свип против тренда — пропускаем

            confluence_score += 0.35 * sweep_signal.strength
            reasons.append(f"Sweep: {sweep_signal.description}")
            confluence_score += 0.15
            reasons.append("Sweep aligns with HTF trend")
        else:
            sweep_dir = 0

        # --- Trend alignment ---
        confluence_score += 0.15
        reasons.append(f"HTF trend: {'bullish' if trend_direction > 0 else 'bearish'}")

        # --- Pullback check ---
        pullback_dir = self._detect_pullback(klines, market_analysis)
        if pullback_dir != 0 and pullback_dir == trend_direction:
            confluence_score += 0.15
            reasons.append(f"Pullback: {'bullish' if pullback_dir > 0 else 'bearish'}")

        # --- RSI extremes (только по тренду) ---
        rsi = market_analysis.rsi
        if rsi < 30 and trend_direction > 0:
            confluence_score += 0.10
            reasons.append(f"RSI oversold: {rsi:.1f}")
        elif rsi > 70 and trend_direction < 0:
            confluence_score += 0.10
            reasons.append(f"RSI overbought: {rsi:.1f}")

        # --- ADX minimum (тренд должен быть сильным) ---
        if market_analysis.adx < 20:
            return signal  # Слабый тренд — не входим

        # --- Funding signal ---
        if funding_signal and funding_signal.signal != 0:
            confluence_score += 0.05 * funding_signal.strength
            reasons.append(f"Funding: {funding_signal.reason}")

        # --- Liquidation clusters ---
        if liq_analysis and liq_analysis.signal != 0:
            confluence_score += 0.05
            reasons.append(f"Liq magnet: {liq_analysis.magnet_direction}")

        # === 4. Determine entry side ===
        # Требуем: sweep ИЛИ (pullback + trend + RSI)
        if sweep_dir != 0:
            entry_side = sweep_dir
        elif pullback_dir == trend_direction and pullback_dir != 0:
            entry_side = pullback_dir
        else:
            return signal  # Нет чёткого сигнала

        # === 5. Calculate SL/TP ===
        if atr_value <= 0:
            atr_value = current_price * 0.01

        if entry_side > 0:  # LONG
            sl_price = current_price - atr_value * 2.0
            tp_price = current_price + atr_value * self.min_rr_ratio * 2.0
        else:  # SHORT
            sl_price = current_price + atr_value * 2.0
            tp_price = current_price - atr_value * self.min_rr_ratio * 2.0

        # Sweep-based SL
        if sweep_signal.detected and sweep_signal.sweep_level > 0:
            if entry_side > 0:
                sl_from_sweep = sweep_signal.sweep_level - atr_value * 0.3
                if sl_from_sweep > 0:
                    sl_price = sl_from_sweep
            else:
                sl_from_sweep = sweep_signal.sweep_level + atr_value * 0.3
                sl_price = sl_from_sweep

        # RR ratio
        risk = abs(current_price - sl_price)
        reward = abs(tp_price - current_price)
        rr = reward / risk if risk > 0 else 0

        if rr < self.min_rr_ratio:
            # Adjust TP to meet minimum RR
            if entry_side > 0:
                tp_price = current_price + risk * self.min_rr_ratio
            else:
                tp_price = current_price - risk * self.min_rr_ratio
            rr = self.min_rr_ratio

        # === 6. Final check ===
        if confluence_score < self.min_confluence:
            return signal

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
        """
        Обнаруживает откат в тренде.
        Returns: 1 = bullish pullback, -1 = bearish pullback, 0 = none
        """
        if not self.pullback_enabled:
            return 0
        if len(klines) < 20:
            return 0

        closes = [float(k["close"]) for k in klines[-20:]]
        trend = market_analysis.trend.value
        if trend == 0:
            return 0

        # Простая детекция: цена откатила от EMA и возвращается
        ema = market_analysis.ema_fast
        current = closes[-1]
        prev = closes[-3]

        if trend > 0:  # Бычий тренд
            # Pullback: цена была ниже EMA, теперь возвращается выше
            if prev < ema and current >= ema * 0.998:
                return 1
            # Цена коснулась EMA снизу
            if abs(current - ema) / ema < 0.003 and current > prev:
                return 1

        elif trend < 0:  # Медвежий тренд
            if prev > ema and current <= ema * 1.002:
                return -1
            if abs(current - ema) / ema < 0.003 and current < prev:
                return -1

        return 0
