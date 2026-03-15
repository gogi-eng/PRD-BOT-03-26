#!/usr/bin/env python3
"""
EXIT ENGINE v2 — structural SL/TP with commission-aware trailing.

1. HARD SL — set from structure (OB/FVG/swing), not arbitrary ATR
2. EARLY EXIT — close dead trades after N bars
3. TRAILING EXIT — activates only after min profit covers commissions
4. TP CAP — structural target
"""
from __future__ import annotations
from typing import Optional, Tuple
from enum import Enum


class ExitReason(Enum):
    HARD_SL = "hard_sl"
    LIQUIDATION_STOP = "liquidation_stop"
    EARLY_EXIT = "early_exit"
    TRAILING_EXIT = "trailing_exit"
    TP_CAP = "tp_cap"
    MANUAL = "manual"
    EXCHANGE_CLOSED = "exchange_closed"


class ExitEngine:
    """Structural exit engine with commission-aware trailing."""

    def __init__(
        self,
        hard_sl_atr_mult: float = 1.8,
        early_exit_bars: int = 12,
        early_exit_min_profit_atr: float = 0.35,
        trailing_activation_atr: float = 0.8,
        trailing_distance_atr: float = 1.2,
        tp_cap_atr_mult: float = 8.0,
        min_profit_before_trail_pct: float = 0.5,
    ):
        self.hard_sl_atr_mult = hard_sl_atr_mult
        self.early_exit_bars = early_exit_bars
        self.early_exit_min_profit_atr = early_exit_min_profit_atr
        self.trailing_activation_atr = trailing_activation_atr
        self.trailing_distance_atr = trailing_distance_atr
        self.tp_cap_atr_mult = tp_cap_atr_mult
        self.min_profit_before_trail_pct = min_profit_before_trail_pct

    def initialize_position(self, position, atr_value: float, protective_liq_level: float = 0.0):
        """Set exit levels when entering. SL and TP should already come from entry engine's structure analysis."""
        entry = position.entry_price
        is_long = position.is_long

        if atr_value <= 0:
            atr_value = entry * 0.01

        # Fallback SL only if entry engine didn't set one
        if position.stop_loss <= 0:
            if is_long:
                position.stop_loss = entry - atr_value * self.hard_sl_atr_mult
            else:
                position.stop_loss = entry + atr_value * self.hard_sl_atr_mult

        # Fallback TP only if entry engine didn't set one
        if position.take_profit <= 0:
            if is_long:
                position.take_profit = entry + atr_value * self.tp_cap_atr_mult
            else:
                position.take_profit = entry - atr_value * self.tp_cap_atr_mult

        # Trailing: only activates after price moves enough to cover commissions + min profit
        min_move = entry * (self.min_profit_before_trail_pct / 100)
        atr_activation = atr_value * self.trailing_activation_atr
        activation_distance = max(min_move, atr_activation)

        position.trailing_distance = atr_value * self.trailing_distance_atr
        if is_long:
            position.trailing_activation_price = entry + activation_distance
        else:
            position.trailing_activation_price = entry - activation_distance

        if self.trailing_activation_atr <= 0:
            position.trailing_active = True
            position.trailing_stop = position.stop_loss

        position.best_price = entry
        if hasattr(position, "protective_liq_level") and protective_liq_level > 0:
            position.protective_liq_level = protective_liq_level

    def check_exit(self, position, current_price: float, atr_value: float = 0,
                   protective_level: float = 0.0, allow_early_exit: bool = True) -> Tuple[bool, Optional[ExitReason], str]:
        """Check exit conditions."""
        entry = position.entry_price
        is_long = position.is_long

        if atr_value <= 0:
            atr_value = entry * 0.01

        if is_long:
            profit = current_price - entry
        else:
            profit = entry - current_price

        # Protective (liquidation) stop
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

        # 2. EARLY EXIT — only for bot-opened positions that go nowhere
        if allow_early_exit and position.bars_since_entry >= self.early_exit_bars:
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
        """Update trailing stop. Called every cycle."""
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
            elif not is_long and current_price <= position.trailing_activation_price:
                position.trailing_active = True

        # Move trailing stop
        if position.trailing_active and position.trailing_distance > 0:
            if is_long:
                new_stop = position.best_price - position.trailing_distance
                floor = max(position.trailing_stop, position.stop_loss)
                if new_stop > floor:
                    position.trailing_stop = new_stop
                    updated = True
            else:
                new_stop = position.best_price + position.trailing_distance
                ceiling = position.trailing_stop if position.trailing_stop > 0 else position.stop_loss
                if ceiling <= 0 or new_stop < min(ceiling, position.stop_loss if position.stop_loss > 0 else ceiling):
                    position.trailing_stop = new_stop
                    updated = True

        return updated
