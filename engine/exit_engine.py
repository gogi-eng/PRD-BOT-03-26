#!/usr/bin/env python3
"""
EXIT ENGINE v3 — structural SL/TP with R-based trailing.

SL: sweep_low - ATR*0.2 (set by entry engine from structure)
TP: TP1 = previous_high, TP2 = liquidity_cluster, TP3 = trailing

Trailing logic:
  1R profit → SL moves to breakeven
  From trailing_structural_r_threshold R: swing low/high anchor (optional ATR buffer under/above level)
  buffer=0: legacy max(swing, distance) long / min(swing, distance) short (short still needs swing < breakeven for legacy branch)
  After that: classic distance-based trailing
"""
from __future__ import annotations
import logging
from datetime import datetime, timezone
from typing import Optional, Tuple

import numpy as np
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
        early_exit_min_hold_minutes: float = 0.0,
        trailing_activation_atr: float = 0.8,
        trailing_distance_atr: float = 1.2,
        trailing_min_distance_pct: float = 0.0,
        trailing_min_distance_from_price_pct: Optional[float] = None,
        tp_cap_atr_mult: float = 8.0,
        min_profit_before_trail_pct: float = 0.5,
        trailing_structural_r_threshold: float = 2.0,
        trailing_swing_buffer_atr_mult: float = 0.0,
        sl_buffer_atr_mult: float = 0.2,
        fee_rate: float = 0.0006,
        ema_trend_exit_buffer_pct: float = 0.0,
        ema_exit_buffer_pct: Optional[float] = None,
        ema_trend_exit_confirm_bars: int = 1,
        ema_trend_exit_require_slope: bool = True,
        ema_exit_confirm_bars: Optional[int] = None,
        ema_exit_require_ema_slope: Optional[bool] = None,
        ema_trend_exit_min_move_from_entry_pct: float = 0.0,
        ema_exit_min_move_from_entry_pct: Optional[float] = None,
        ema_exit_min_adverse_from_entry_pct: Optional[float] = None,
        ema_trend_exit_min_adverse_from_entry_pct: Optional[float] = None,
    ):
        self.hard_sl_atr_mult = hard_sl_atr_mult
        self.early_exit_bars = early_exit_bars
        self.early_exit_min_profit_atr = early_exit_min_profit_atr
        self.early_exit_min_hold_minutes = max(0.0, float(early_exit_min_hold_minutes))
        self.trailing_activation_atr = trailing_activation_atr
        self.trailing_distance_atr = trailing_distance_atr
        if trailing_min_distance_from_price_pct is not None:
            trailing_min_distance_pct = float(trailing_min_distance_from_price_pct)
        self.trailing_min_distance_pct = max(0.0, float(trailing_min_distance_pct))
        self.tp_cap_atr_mult = tp_cap_atr_mult
        self.min_profit_before_trail_pct = min_profit_before_trail_pct
        self.trailing_structural_r_threshold = max(0.0, float(trailing_structural_r_threshold))
        self.trailing_swing_buffer_atr_mult = max(0.0, float(trailing_swing_buffer_atr_mult))
        self.sl_buffer_atr_mult = sl_buffer_atr_mult
        self.fee_rate = fee_rate
        self.breakeven_fee_mult = 2.5  # round-trip fee buffer (open + close + slippage margin)
        # Extra EMA buffer (in %) to avoid noisy trend-exit flips near EMA.
        # Backward/forward compatibility:
        # - supports both `ema_trend_exit_buffer_pct` and legacy/new alias `ema_exit_buffer_pct`
        if ema_exit_buffer_pct is not None:
            ema_trend_exit_buffer_pct = ema_exit_buffer_pct
        if ema_exit_confirm_bars is not None:
            ema_trend_exit_confirm_bars = int(ema_exit_confirm_bars)
        if ema_exit_require_ema_slope is not None:
            ema_trend_exit_require_slope = bool(ema_exit_require_ema_slope)
        if ema_trend_exit_min_adverse_from_entry_pct is not None:
            ema_trend_exit_min_move_from_entry_pct = float(
                ema_trend_exit_min_adverse_from_entry_pct
            )
        if ema_exit_min_move_from_entry_pct is not None:
            ema_trend_exit_min_move_from_entry_pct = float(ema_exit_min_move_from_entry_pct)
        if ema_exit_min_adverse_from_entry_pct is not None:
            ema_trend_exit_min_move_from_entry_pct = float(
                ema_exit_min_adverse_from_entry_pct
            )
        self.ema_trend_exit_buffer_pct = max(0.0, float(ema_trend_exit_buffer_pct))
        self.ema_trend_exit_confirm_bars = max(1, int(ema_trend_exit_confirm_bars))
        self.ema_trend_exit_require_slope = bool(ema_trend_exit_require_slope)
        # Prevent immediate churn closes around entry price.
        self.ema_trend_exit_min_move_from_entry_pct = max(
            0.0, float(ema_trend_exit_min_move_from_entry_pct)
        )

    @staticmethod
    def _favorable_profit_per_unit(position, entry: float, is_long: bool) -> float:
        """Best (MFE-like) profit seen since entry, per 1 unit."""
        best = float(getattr(position, "best_price", 0.0) or 0.0)
        if best <= 0:
            best = entry
        if is_long:
            return max(0.0, best - entry)
        return max(0.0, entry - best)

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
        atr_trailing_distance = atr_value * self.trailing_distance_atr
        pct_trailing_distance = entry * (self.trailing_min_distance_pct / 100.0)
        position.trailing_distance = max(atr_trailing_distance, pct_trailing_distance)

        # Trailing activation distance:
        # - upper-bounded by ATR profile to avoid "never activate" behavior when SL is wide
        # - lower-bounded by minimum move to at least cover fees/slippage drift
        min_move = entry * (self.min_profit_before_trail_pct / 100)
        one_r_move = max(risk, min_move)
        if self.trailing_activation_atr > 0:
            atr_activation_move = max(atr_value * self.trailing_activation_atr, min_move)
            activation_move = min(one_r_move, atr_activation_move)
        else:
            activation_move = min_move
        if is_long:
            position.trailing_activation_price = entry + activation_move
        else:
            position.trailing_activation_price = entry - activation_move

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

        # 3. EARLY EXIT — dead trades after N bars (optional min wall-clock age)
        # early_exit_bars <= 0 means feature disabled
        hold_ok = True
        if self.early_exit_min_hold_minutes > 0:
            et = getattr(position, "entry_time", None)
            if isinstance(et, datetime):
                now = datetime.now(timezone.utc)
                if et.tzinfo is None:
                    et = et.replace(tzinfo=timezone.utc)
                age_min = (now - et).total_seconds() / 60.0
                hold_ok = age_min >= self.early_exit_min_hold_minutes

        if (
            allow_early_exit
            and self.early_exit_bars > 0
            and position.bars_since_entry >= self.early_exit_bars
            and not position.trailing_active
            and hold_ok
        ):
            min_profit = atr_value * self.early_exit_min_profit_atr
            # Ensure min_profit covers at least trading fees (entry + exit)
            fee_per_unit = entry * self.fee_rate + current_price * self.fee_rate
            min_profit = max(min_profit, fee_per_unit)
            favorable_profit = self._favorable_profit_per_unit(position, entry, is_long)
            effective_profit = max(profit, favorable_profit)
            if effective_profit < min_profit:
                return True, ExitReason.EARLY_EXIT, (
                    f"No movement after {position.bars_since_entry} bars. "
                    f"Profit {profit:.4f} / best {favorable_profit:.4f} < required {min_profit:.4f} "
                    f"(incl fees {fee_per_unit:.4f})"
                )

        # 4. TP CAP
        if is_long and current_price >= position.take_profit:
            return True, ExitReason.TP_CAP, f"TP hit at {position.take_profit:.4f}"
        if not is_long and current_price <= position.take_profit:
            return True, ExitReason.TP_CAP, f"TP hit at {position.take_profit:.4f}"

        return False, None, ""

    def update_trailing(
        self,
        position,
        current_price: float,
        last_swing_low: float = 0.0,
        last_swing_high: float = 0.0,
        atr_value: float = 0.0,
    ) -> bool:
        """
        R-based trailing:
          1R → SL to breakeven
          From trailing_structural_r_threshold R → anchor to swing low/high (with optional ATR buffer)
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
            # Guard rail: trailing stop must stay on the safe side of current price,
            # otherwise it can trigger an immediate trailing_exit in the same cycle.
            min_gap_from_price = max(
                current_price * (self.trailing_min_distance_pct / 100.0),
                max(current_price * 1e-6, 1e-8),
            )
            if is_long and current_price >= position.trailing_activation_price:
                position.trailing_active = True
                # 1R: move SL to breakeven + fee buffer
                breakeven_with_fee = entry + fee_buffer
                raw_stop = max(breakeven_with_fee, position.stop_loss)
                max_allowed_stop = current_price - min_gap_from_price
                position.trailing_stop = max(position.stop_loss, min(raw_stop, max_allowed_stop))
                updated = True
                logger.info(
                    f"[TRAIL ACTIVATED] {getattr(position, 'symbol', '?')} LONG "
                    f"price={current_price:.4f} >= activation={position.trailing_activation_price:.4f} "
                    f"→ trail_stop={position.trailing_stop:.4f} (breakeven+fee, fee_buf={fee_buffer:.4f})"
                )
            elif not is_long and current_price <= position.trailing_activation_price:
                position.trailing_active = True
                breakeven_with_fee = entry - fee_buffer
                raw_stop = (
                    min(breakeven_with_fee, position.stop_loss)
                    if position.stop_loss > 0
                    else breakeven_with_fee
                )
                min_allowed_stop = current_price + min_gap_from_price
                safe_stop = max(raw_stop, min_allowed_stop)
                if position.stop_loss > 0:
                    safe_stop = min(safe_stop, position.stop_loss)
                position.trailing_stop = safe_stop
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

            min_dist_from_price = current_price * (self.trailing_min_distance_pct / 100.0)
            effective_distance = max(position.trailing_distance, min_dist_from_price)
            # Distance-based trailing
            distance_stop = max(breakeven_with_fee, position.best_price - effective_distance)

            atr_use = atr_value if atr_value > 0 else 0.0
            buf = atr_use * self.trailing_swing_buffer_atr_mult

            if r_multiple >= self.trailing_structural_r_threshold and last_swing_low > 0 and last_swing_low > breakeven_with_fee:
                if self.trailing_swing_buffer_atr_mult > 0 and buf > 0:
                    # Stop slightly *below* support (swing low)
                    swing_anchor = last_swing_low - buf
                    if swing_anchor > breakeven_with_fee:
                        new_stop = max(breakeven_with_fee, min(distance_stop, swing_anchor))
                    else:
                        new_stop = distance_stop
                else:
                    # Legacy: do not trail tighter than last swing
                    new_stop = max(last_swing_low, distance_stop)
            elif r_multiple >= 1.0:
                new_stop = distance_stop
            else:
                new_stop = position.stop_loss

            if new_stop > position.trailing_stop:
                logger.info(
                    f"[TRAIL MOVE] {getattr(position, 'symbol', '?')} LONG "
                    f"R={r_multiple:.2f} old_trail={position.trailing_stop:.4f} → new={new_stop:.4f} "
                    f"(best={position.best_price:.4f} dist_stop={distance_stop:.4f} "
                    f"eff_dist={effective_distance:.4f})"
                )
                position.trailing_stop = new_stop
                updated = True

        else:
            profit = entry - position.best_price
            r_multiple = profit / risk if risk > 0 else 0
            fee_buffer = entry * self.fee_rate * self.breakeven_fee_mult
            breakeven_with_fee = entry - fee_buffer

            min_dist_from_price = current_price * (self.trailing_min_distance_pct / 100.0)
            effective_distance = max(position.trailing_distance, min_dist_from_price)
            distance_stop = min(breakeven_with_fee, position.best_price + effective_distance)

            atr_use = atr_value if atr_value > 0 else 0.0
            buf = atr_use * self.trailing_swing_buffer_atr_mult

            if r_multiple >= self.trailing_structural_r_threshold and last_swing_high > 0:
                if self.trailing_swing_buffer_atr_mult > 0 and buf > 0:
                    # Stop slightly *above* resistance (swing high)
                    swing_anchor = last_swing_high + buf
                    new_stop = max(distance_stop, swing_anchor)
                elif last_swing_high < breakeven_with_fee:
                    new_stop = min(last_swing_high, distance_stop)
                else:
                    new_stop = distance_stop
            elif r_multiple >= 1.0:
                new_stop = distance_stop
            else:
                new_stop = position.stop_loss

            if position.trailing_stop <= 0 or new_stop < position.trailing_stop:
                logger.info(
                    f"[TRAIL MOVE] {getattr(position, 'symbol', '?')} SHORT "
                    f"R={r_multiple:.2f} old_trail={position.trailing_stop:.4f} → new={new_stop:.4f} "
                    f"(best={position.best_price:.4f} dist_stop={distance_stop:.4f} "
                    f"eff_dist={effective_distance:.4f})"
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
        self,
        position,
        klines: list,
        ema_period: int = 20,
        confirm_bars: Optional[int] = None,
        require_ema_slope: Optional[bool] = None,
    ) -> Tuple[bool, Optional[ExitReason], str]:
        """
        EMA Trend Exit: close position if price crosses below EMA(period) for longs
        or above EMA(period) for shorts. Indicates trend reversal.

        Only triggers after position has been held for at least ema_period bars
        to avoid premature exits.
        """
        confirm_bars = max(
            1,
            int(
                self.ema_trend_exit_confirm_bars
                if confirm_bars is None
                else confirm_bars
            ),
        )
        require_slope = (
            self.ema_trend_exit_require_slope
            if require_ema_slope is None
            else bool(require_ema_slope)
        )
        if not klines or len(klines) < ema_period + confirm_bars + 4:
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
        entry_price = float(getattr(position, "entry_price", 0.0) or 0.0)
        min_move_pct = max(0.0, float(self.ema_trend_exit_min_move_from_entry_pct))
        if entry_price > 0 and min_move_pct > 0:
            move_from_entry_pct = abs(current_price - entry_price) / entry_price * 100.0
            if move_from_entry_pct < min_move_pct:
                return False, None, ""
        buffer_mult = self.ema_trend_exit_buffer_pct / 100.0
        if is_long:
            confirmed = True
            for i in range(1, confirm_bars + 1):
                thr = ema[-i] * (1.0 - buffer_mult)
                if closes[-i] >= thr:
                    confirmed = False
                    break
            if confirmed and require_slope and ema[-1] >= ema[-2]:
                confirmed = False
            if confirmed:
                long_threshold = ema_val * (1.0 - buffer_mult)
                return True, ExitReason.TREND_EXIT, (
                    f"EMA trend exit: LONG confirmed {confirm_bars} bars below EMA{ema_period} threshold "
                    f"(price={current_price:.4f}, threshold={long_threshold:.4f}, ema={ema_val:.4f}, "
                    f"buffer={self.ema_trend_exit_buffer_pct:.3f}%, slope={'on' if require_slope else 'off'})"
                )
        else:
            confirmed = True
            for i in range(1, confirm_bars + 1):
                thr = ema[-i] * (1.0 + buffer_mult)
                if closes[-i] <= thr:
                    confirmed = False
                    break
            if confirmed and require_slope and ema[-1] <= ema[-2]:
                confirmed = False
            if confirmed:
                short_threshold = ema_val * (1.0 + buffer_mult)
                return True, ExitReason.TREND_EXIT, (
                    f"EMA trend exit: SHORT confirmed {confirm_bars} bars above EMA{ema_period} threshold "
                    f"(price={current_price:.4f}, threshold={short_threshold:.4f}, ema={ema_val:.4f}, "
                    f"buffer={self.ema_trend_exit_buffer_pct:.3f}%, slope={'on' if require_slope else 'off'})"
                )

        return False, None, ""

