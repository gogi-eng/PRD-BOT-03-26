#!/usr/bin/env python3
"""
EXIT ENGINE — управление выходами из позиций.

ATR-based:
1. HARD SL — защита от ошибок входа
2. EARLY EXIT — закрытие "мёртвых" сделок
3. TRAILING EXIT — основной механизм фиксации прибыли
4. TP CAP — safety cap
"""
from __future__ import annotations
from typing import Dict, List, Optional, Tuple
from enum import Enum
from datetime import datetime, timezone


class ExitReason(Enum):
    HARD_SL = "hard_sl"
    LIQUIDATION_STOP = "liquidation_stop"
    EARLY_EXIT = "early_exit"
    TRAILING_EXIT = "trailing_exit"
    TP_CAP = "tp_cap"
    MANUAL = "manual"
    EXCHANGE_CLOSED = "exchange_closed"


class ExitEngine:
    """
    ATR-based exit engine.
    Все уровни фиксируются при входе и НЕ пересчитываются.
    """

    def __init__(
        self,
        hard_sl_atr_mult: float = 2.0,
        early_exit_bars: int = 10,
        early_exit_min_profit_atr: float = 0.5,
        trailing_activation_atr: float = 1.0,
        trailing_distance_atr: float = 1.5,
        tp_cap_atr_mult: float = 10.0,
    ):
        self.hard_sl_atr_mult = hard_sl_atr_mult
        self.early_exit_bars = early_exit_bars
        self.early_exit_min_profit_atr = early_exit_min_profit_atr
        self.trailing_activation_atr = trailing_activation_atr
        self.trailing_distance_atr = trailing_distance_atr
        self.tp_cap_atr_mult = tp_cap_atr_mult

    def initialize_position(self, position, atr_value: float, protective_liq_level: float = 0.0):
        """
        Рассчитать exit levels при входе.
        Модифицирует position in-place.
        """
        entry = position.entry_price
        is_long = position.is_long

        if atr_value <= 0:
            atr_value = entry * 0.01

        # Hard SL (если не задан из entry engine)
        if position.stop_loss <= 0:
            if is_long:
                position.stop_loss = entry - atr_value * self.hard_sl_atr_mult
            else:
                position.stop_loss = entry + atr_value * self.hard_sl_atr_mult

        # TP cap
        if position.take_profit <= 0:
            if is_long:
                position.take_profit = entry + atr_value * self.tp_cap_atr_mult
            else:
                position.take_profit = entry - atr_value * self.tp_cap_atr_mult

        # Trailing setup
        position.trailing_distance = atr_value * self.trailing_distance_atr
        if is_long:
            position.trailing_activation_price = entry + atr_value * self.trailing_activation_atr
        else:
            position.trailing_activation_price = entry - atr_value * self.trailing_activation_atr

        # Immediate activation if mult <= 0
        if self.trailing_activation_atr <= 0:
            position.trailing_active = True
            position.trailing_stop = position.stop_loss

        position.best_price = entry
        if hasattr(position, "protective_liq_level") and protective_liq_level > 0:
            position.protective_liq_level = protective_liq_level

        print(f"   [EXIT] {position.symbol}: SL=${position.stop_loss:.4f} "
              f"TP=${position.take_profit:.4f} trail_dist={position.trailing_distance:.4f}")

    def check_exit(self, position, current_price: float, atr_value: float = 0, protective_level: float = 0.0) -> Tuple[bool, Optional[ExitReason], str]:
        """
        Проверить условия выхода.

        Returns:
            (should_exit, reason, details)
        """
        entry = position.entry_price
        is_long = position.is_long

        if atr_value <= 0:
            atr_value = entry * 0.01

        # PnL
        if is_long:
            profit = current_price - entry
        else:
            profit = entry - current_price

        if protective_level > 0:
            if is_long and current_price <= protective_level:
                return True, ExitReason.LIQUIDATION_STOP, f"Price ${current_price:.4f} <= liq stop ${protective_level:.4f}"
            if not is_long and current_price >= protective_level:
                return True, ExitReason.LIQUIDATION_STOP, f"Price ${current_price:.4f} >= liq stop ${protective_level:.4f}"

        # 1. HARD SL
        if is_long and current_price <= position.stop_loss:
            return True, ExitReason.HARD_SL, f"Price ${current_price:.4f} <= SL ${position.stop_loss:.4f}"
        if not is_long and current_price >= position.stop_loss:
            return True, ExitReason.HARD_SL, f"Price ${current_price:.4f} >= SL ${position.stop_loss:.4f}"

        # 2. EARLY EXIT
        if position.bars_since_entry >= self.early_exit_bars:
            min_profit = atr_value * self.early_exit_min_profit_atr
            if profit < min_profit:
                return True, ExitReason.EARLY_EXIT, (
                    f"No movement after {position.bars_since_entry} bars. "
                    f"Profit ${profit:.4f} < required ${min_profit:.4f}"
                )

        # 3. TP CAP
        if is_long and current_price >= position.take_profit:
            return True, ExitReason.TP_CAP, f"TP hit: ${current_price:.4f} >= ${position.take_profit:.4f}"
        if not is_long and current_price <= position.take_profit:
            return True, ExitReason.TP_CAP, f"TP hit: ${current_price:.4f} <= ${position.take_profit:.4f}"

        # 4. TRAILING EXIT
        if position.trailing_active and position.trailing_stop > 0:
            if is_long and current_price <= position.trailing_stop:
                return True, ExitReason.TRAILING_EXIT, f"Trailing stop hit at ${position.trailing_stop:.4f}"
            if not is_long and current_price >= position.trailing_stop:
                return True, ExitReason.TRAILING_EXIT, f"Trailing stop hit at ${position.trailing_stop:.4f}"

        return False, None, ""

    def update_trailing(self, position, current_price: float) -> bool:
        """
        Обновить trailing stop. Вызывается каждый цикл.

        Returns:
            True если trailing был обновлён
        """
        is_long = position.is_long
        updated = False

        # Update best price
        if is_long:
            if current_price > position.best_price:
                position.best_price = current_price
                updated = True
        else:
            if position.best_price <= 0 or current_price < position.best_price:
                position.best_price = current_price
                updated = True

        # Check activation
        if not position.trailing_active:
            if is_long and current_price >= position.trailing_activation_price:
                position.trailing_active = True
                print(f"   [EXIT] {position.symbol} trailing ACTIVATED at ${current_price:.4f}")
            elif not is_long and current_price <= position.trailing_activation_price:
                position.trailing_active = True
                print(f"   [EXIT] {position.symbol} trailing ACTIVATED at ${current_price:.4f}")

        # Move trailing stop
        if position.trailing_active and position.trailing_distance > 0:
            if is_long:
                new_stop = position.best_price - position.trailing_distance
                if new_stop > position.trailing_stop:
                    position.trailing_stop = new_stop
                    updated = True
            else:
                new_stop = position.best_price + position.trailing_distance
                if position.trailing_stop <= 0 or new_stop < position.trailing_stop:
                    position.trailing_stop = new_stop
                    updated = True

        return updated
