#!/usr/bin/env python3
"""
EXIT ENGINE v3 — structural SL/TP with R-based trailing.

SL: sweep_low - ATR*0.2 (set by entry engine from structure)
TP: TP1 = previous_high, TP2 = liquidity_cluster, TP3 = trailing

Trailing logic:
  1R profit → SL moves to breakeven
  2R profit → SL moves to last swing low (for longs) / swing high (for shorts)
  After that: classic distance-based trailing
"""
from __future__ import annotations
import logging
import numpy as np
from typing import Optional, Tuple
from enum import Enum

logger = logging.getLogger("EXIT_ENGINE")


class ExitReason(Enum):
    HARD_SL = "hard_sl"
    LIQUIDATION_STOP = "liquidation_stop"
    EARLY_EXIT = "early_exit"
    TRAILING_EXIT = "trailing_exit"
    TP_CAP = "tp_cap"
    TREND_EXIT = "trend_exit"
    MANUAL = "manual"
    EXCHANGE_CLOSED = "exchange_closed"


class ExitEngine:
    """Structural exit engine with R-based trailing progression."""

    def __init__(
        self,
        hard_sl_atr_mult: float = 1.8,
        early_exit_bars: int = 12,
        early_exit_min_profit_atr: float = 0.35,
        trailing_activation_atr: float = 0.8,
        trailing_distance_atr: float = 1.2,
        tp_cap_atr_mult: float = 8.0,
        min_profit_before_trail_pct: float = 0.5,
        sl_buffer_atr_mult: float = 0.2,
        fee_rate: float = 0.0006,
    ):
        self.hard_sl_atr_mult = hard_sl_atr_mult
        self.early_exit_bars = early_exit_bars
        self.early_exit_min_profit_atr = early_exit_min_profit_atr
        self.trailing_activation_atr = trailing_activation_atr
        self.trailing_distance_atr = trailing_distance_atr
        self.tp_cap_atr_mult = tp_cap_atr_mult
        self.min_profit_before_trail_pct = min_profit_before_trail_pct
        self.sl_buffer_atr_mult = sl_buffer_atr_mult
        self.fee_rate = fee_rate
        self.breakeven_fee_mult = 2.5  # round-trip fee buffer (open + close + slippage margin)

    def initialize_position(self, position, atr_value: float, protective_liq_level: float = 0.0):
        """Set exit levels when entering. SL/TP should already come from entry engine."""
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

        # Store initial risk (1R) for R-based trailing
        risk = abs(entry - position.stop_loss)
        position.trailing_distance = atr_value * self.trailing_distance_atr

        # Trailing activates at 1R profit (breakeven level)
        # min_profit_before_trail_pct ensures commissions are covered
        min_move = entry * (self.min_profit_before_trail_pct / 100)
        one_r = max(risk, min_move)
        if is_long:
            position.trailing_activation_price = entry + one_r
        else:
            position.trailing_activation_price = entry - one_r

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
                return True, ExitReason.LIQUIDATION_STOP, f"Price {current_price:.4f} <= liq stop {protective_level:.4f}"
            if not is_long and current_price >= protective_level:
                return True, ExitReason.LIQUIDATION_STOP, f"Price {current_price:.4f} >= liq stop {protective_level:.4f}"

        # 1. TRAILING EXIT — check BEFORE hard_sl to prevent blocking on manual positions
        #    (when SL == trailing_stop, hard_sl fires first and gets blocked for manual)
        if position.trailing_active and position.trailing_stop > 0:
            if is_long and current_price <= position.trailing_stop:
                return True, ExitReason.TRAILING_EXIT, f"Trailing stop hit at {position.trailing_stop:.4f}"
            if not is_long and current_price >= position.trailing_stop:
                return True, ExitReason.TRAILING_EXIT, f"Trailing stop hit at {position.trailing_stop:.4f}"

        # 2. HARD SL
        if is_long and current_price <= position.stop_loss:
            return True, ExitReason.HARD_SL, f"SL hit at {position.stop_loss:.4f}"
        if not is_long and current_price >= position.stop_loss:
            return True, ExitReason.HARD_SL, f"SL hit at {position.stop_loss:.4f}"

        # 3. EARLY EXIT — dead trades after N bars
        # early_exit_bars <= 0 means feature disabled
        if allow_early_exit and self.early_exit_bars > 0 and position.bars_since_entry >= self.early_exit_bars:
            min_profit = atr_value * self.early_exit_min_profit_atr
            # Ensure min_profit covers at least trading fees (entry + exit)
            fee_per_unit = entry * self.fee_rate + current_price * self.fee_rate
            min_profit = max(min_profit, fee_per_unit)
            if profit < min_profit:
                return True, ExitReason.EARLY_EXIT, (
                    f"No movement after {position.bars_since_entry} bars. "
                    f"Profit {profit:.4f} < required {min_profit:.4f} (incl fees {fee_per_unit:.4f})"
                )

        # 4. TP CAP
        if is_long and current_price >= position.take_profit:
            return True, ExitReason.TP_CAP, f"TP hit at {position.take_profit:.4f}"
        if not is_long and current_price <= position.take_profit:
            return True, ExitReason.TP_CAP, f"TP hit at {position.take_profit:.4f}"

        return False, None, ""

    def update_trailing(self, position, current_price: float, last_swing_low: float = 0.0, last_swing_high: float = 0.0) -> bool:
        """
        R-based trailing:
          1R → SL to breakeven
          2R → SL to last swing low (long) / swing high (short)
          After: distance-based trailing
        """
        entry = position.entry_price
        is_long = position.is_long
        risk = abs(entry - position.stop_loss) if position.stop_loss > 0 else entry * 0.01
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

        # Check activation (1R profit)
        if not position.trailing_active:
            fee_buffer = entry * self.fee_rate * self.breakeven_fee_mult
            if is_long and current_price >= position.trailing_activation_price:
                position.trailing_active = True
                # 1R: move SL to breakeven + fee buffer
                breakeven_with_fee = entry + fee_buffer
                position.trailing_stop = max(breakeven_with_fee, position.stop_loss)
                updated = True
                logger.info(
                    f"[TRAIL ACTIVATED] {getattr(position, 'symbol', '?')} LONG "
                    f"price={current_price:.4f} >= activation={position.trailing_activation_price:.4f} "
                    f"→ trail_stop={position.trailing_stop:.4f} (breakeven+fee, fee_buf={fee_buffer:.4f})"
                )
            elif not is_long and current_price <= position.trailing_activation_price:
                position.trailing_active = True
                breakeven_with_fee = entry - fee_buffer
                position.trailing_stop = min(breakeven_with_fee, position.stop_loss) if position.stop_loss > 0 else breakeven_with_fee
                updated = True
                logger.info(
                    f"[TRAIL ACTIVATED] {getattr(position, 'symbol', '?')} SHORT "
                    f"price={current_price:.4f} <= activation={position.trailing_activation_price:.4f} "
                    f"→ trail_stop={position.trailing_stop:.4f} (breakeven+fee, fee_buf={fee_buffer:.4f})"
                )

        if not position.trailing_active:
            return updated

        # R-based progression
        if is_long:
            profit = position.best_price - entry
            r_multiple = profit / risk if risk > 0 else 0
            fee_buffer = entry * self.fee_rate * self.breakeven_fee_mult
            breakeven_with_fee = entry + fee_buffer

            # Distance-based trailing
            distance_stop = max(breakeven_with_fee, position.best_price - position.trailing_distance)

            if r_multiple >= 2.0 and last_swing_low > 0 and last_swing_low > breakeven_with_fee:
                # 2R: max of (swing low, distance trail)
                new_stop = max(last_swing_low, distance_stop)
            elif r_multiple >= 1.0:
                # 1R: breakeven+fee minimum, then distance-based
                new_stop = distance_stop
            else:
                new_stop = position.stop_loss

            if new_stop > position.trailing_stop:
                logger.info(
                    f"[TRAIL MOVE] {getattr(position, 'symbol', '?')} LONG "
                    f"R={r_multiple:.2f} old_trail={position.trailing_stop:.4f} → new={new_stop:.4f} "
                    f"(best={position.best_price:.4f} dist_stop={distance_stop:.4f})"
                )
                position.trailing_stop = new_stop
                updated = True

        else:
            profit = entry - position.best_price
            r_multiple = profit / risk if risk > 0 else 0
            fee_buffer = entry * self.fee_rate * self.breakeven_fee_mult
            breakeven_with_fee = entry - fee_buffer

            distance_stop = min(breakeven_with_fee, position.best_price + position.trailing_distance)

            if r_multiple >= 2.0 and last_swing_high > 0 and last_swing_high < breakeven_with_fee:
                new_stop = min(last_swing_high, distance_stop)
            elif r_multiple >= 1.0:
                new_stop = distance_stop
            else:
                new_stop = position.stop_loss

            if position.trailing_stop <= 0 or new_stop < position.trailing_stop:
                logger.info(
                    f"[TRAIL MOVE] {getattr(position, 'symbol', '?')} SHORT "
                    f"R={r_multiple:.2f} old_trail={position.trailing_stop:.4f} → new={new_stop:.4f} "
                    f"(best={position.best_price:.4f} dist_stop={distance_stop:.4f})"
                )
                position.trailing_stop = new_stop
                updated = True

        return updated

    @staticmethod
    def _compute_ema(prices: np.ndarray, period: int) -> np.ndarray:
        """Compute EMA for a price array."""
        if len(prices) < period:
            return prices.copy()
        ema = np.empty_like(prices, dtype=float)
        ema[:period] = np.nan
        ema[period - 1] = np.mean(prices[:period])
        k = 2.0 / (period + 1)
        for i in range(period, len(prices)):
            ema[i] = prices[i] * k + ema[i - 1] * (1 - k)
        return ema

    def check_ema_trend_exit(
        self, position, klines: list, ema_period: int = 20
    ) -> Tuple[bool, Optional[ExitReason], str]:
        """
        EMA Trend Exit: close position if price crosses below EMA(period) for longs
        or above EMA(period) for shorts. Indicates trend reversal.

        Only triggers after position has been held for at least ema_period bars
        to avoid premature exits.
        """
        if not klines or len(klines) < ema_period + 5:
            return False, None, ""

        # Need enough bars of holding to avoid false trigger on entry
        if hasattr(position, 'bars_since_entry') and position.bars_since_entry < ema_period:
            return False, None, ""

        closes = np.array([float(k.get("close", 0)) for k in klines], dtype=float)
        ema = self._compute_ema(closes, ema_period)

        current_price = closes[-1]
        ema_val = ema[-1]

        if np.isnan(ema_val):
            return False, None, ""

        is_long = position.is_long

        if is_long and current_price < ema_val:
            return True, ExitReason.TREND_EXIT, (
                f"EMA trend exit: LONG price {current_price:.4f} < EMA{ema_period} {ema_val:.4f}"
            )
        if not is_long and current_price > ema_val:
            return True, ExitReason.TREND_EXIT, (
                f"EMA trend exit: SHORT price {current_price:.4f} > EMA{ema_period} {ema_val:.4f}"
            )

        return False, None, ""

