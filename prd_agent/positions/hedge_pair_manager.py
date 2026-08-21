"""Stateful manager for Trend-Continuation Hedge Pair (live/unified hooks).

Does not place exchange orders by itself — returns action dicts for the
orchestrator / adapter. Live execute path is gated by hedge_pair.execute.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from prd_agent.strategies.hedge_pair import (
    HedgePairConfig,
    SideBias,
    plan_levels,
    trend_allows_entry,
)

logger = logging.getLogger("prd_agent.hedge_pair")


@dataclass
class HedgePairState:
    symbol: str
    bias: SideBias
    entry: float
    long_sl: float
    long_tp: float
    short_sl: float
    short_tp: float
    opened_at: float
    long_open: bool = True
    short_open: bool = True
    long_sl_current: float = 0.0
    short_sl_current: float = 0.0
    be_done_long: bool = False
    be_done_short: bool = False
    peak_long: float = 0.0
    trough_short: float = 0.0

    def __post_init__(self) -> None:
        if not self.long_sl_current:
            self.long_sl_current = self.long_sl
        if not self.short_sl_current:
            self.short_sl_current = self.short_sl
        if not self.peak_long:
            self.peak_long = self.entry
        if not self.trough_short:
            self.trough_short = self.entry


@dataclass
class HedgePairManager:
    """Tracks at most ``max_pairs`` open hedge pairs."""

    config: HedgePairConfig
    open_pairs: Dict[str, HedgePairState] = field(default_factory=dict)

    @classmethod
    def from_cfg(cls, cfg: Dict[str, Any]) -> "HedgePairManager":
        return cls(config=HedgePairConfig.from_cfg(cfg))

    def should_open(
        self,
        symbol: str,
        *,
        bias: SideBias,
        price: float,
        ema: float,
        open_pair_count: Optional[int] = None,
    ) -> bool:
        if not self.config.enabled:
            return False
        if symbol in self.open_pairs:
            return False
        count = open_pair_count if open_pair_count is not None else len(self.open_pairs)
        if count >= self.config.max_pairs:
            return False
        if self.config.symbols and symbol not in self.config.symbols:
            return False
        if self.config.require_trend_bias and not trend_allows_entry(bias, ema, price):
            return False
        return True

    def plan_levels(self, entry: float, bias: SideBias) -> Dict[str, float]:
        levels = plan_levels(entry, self.config)
        levels["bias"] = bias  # type: ignore[assignment]
        return levels

    def register_open(
        self,
        symbol: str,
        entry: float,
        bias: SideBias,
        *,
        opened_at: Optional[float] = None,
    ) -> HedgePairState:
        levels = plan_levels(entry, self.config)
        state = HedgePairState(
            symbol=symbol,
            bias=bias,
            entry=entry,
            long_sl=levels["long_sl"],
            long_tp=levels["long_tp"],
            short_sl=levels["short_sl"],
            short_tp=levels["short_tp"],
            opened_at=opened_at if opened_at is not None else time.time(),
        )
        self.open_pairs[symbol] = state
        return state

    def evaluate(self, symbol: str, price: float, now: Optional[float] = None) -> List[Dict[str, Any]]:
        """Alias for on_price_tick."""
        return self.on_price_tick(symbol, price, now=now)

    def on_price_tick(
        self, symbol: str, price: float, *, now: Optional[float] = None
    ) -> List[Dict[str, Any]]:
        """Return ordered actions: close_long / close_short / move_sl / flatten."""
        state = self.open_pairs.get(symbol)
        if state is None:
            return []
        ts = now if now is not None else time.time()
        actions: List[Dict[str, Any]] = []
        age_min = (ts - state.opened_at) / 60.0

        if age_min >= self.config.max_pair_minutes and (state.long_open or state.short_open):
            actions.append({"action": "flatten", "symbol": symbol, "reason": "max_pair_minutes"})
            state.long_open = False
            state.short_open = False
            self.open_pairs.pop(symbol, None)
            return actions

        # First-SL / TP while both open
        if state.long_open and state.short_open:
            if price <= state.long_sl:
                actions.append({"action": "close_long", "symbol": symbol, "reason": "sl", "price": state.long_sl})
                state.long_open = False
            elif price >= state.short_sl:
                actions.append({"action": "close_short", "symbol": symbol, "reason": "sl", "price": state.short_sl})
                state.short_open = False
            elif price >= state.long_tp:
                actions.append({"action": "close_long", "symbol": symbol, "reason": "tp", "price": state.long_tp})
                state.long_open = False
            elif price <= state.short_tp:
                actions.append({"action": "close_short", "symbol": symbol, "reason": "tp", "price": state.short_tp})
                state.short_open = False

        # Runner: long only
        if state.long_open and not state.short_open:
            state.peak_long = max(state.peak_long, price)
            upnl_pct = (price / state.entry - 1.0) * 100.0
            if (not state.be_done_long) and upnl_pct >= self.config.be_after_profit_pct:
                new_sl = state.entry * (1.0 + (self.config.fee_pct_roundtrip_per_leg / 2.0) / 100.0)
                state.long_sl_current = new_sl
                state.be_done_long = True
                actions.append({"action": "move_sl", "side": "long", "symbol": symbol, "sl": new_sl, "reason": "breakeven"})
            if state.be_done_long and self.config.trail_distance_pct > 0:
                trail = state.peak_long * (1.0 - self.config.trail_distance_pct / 100.0)
                if trail > state.long_sl_current:
                    state.long_sl_current = trail
                    actions.append({"action": "move_sl", "side": "long", "symbol": symbol, "sl": trail, "reason": "trail"})
            if price <= state.long_sl_current:
                actions.append({"action": "close_long", "symbol": symbol, "reason": "runner_sl", "price": state.long_sl_current})
                state.long_open = False
            elif price >= state.long_tp:
                actions.append({"action": "close_long", "symbol": symbol, "reason": "tp", "price": state.long_tp})
                state.long_open = False

        # Runner: short only
        if state.short_open and not state.long_open:
            state.trough_short = min(state.trough_short, price)
            upnl_pct = (state.entry / price - 1.0) * 100.0 if price else 0.0
            upnl_pct = (state.entry - price) / state.entry * 100.0
            if (not state.be_done_short) and upnl_pct >= self.config.be_after_profit_pct:
                new_sl = state.entry * (1.0 - (self.config.fee_pct_roundtrip_per_leg / 2.0) / 100.0)
                state.short_sl_current = new_sl
                state.be_done_short = True
                actions.append({"action": "move_sl", "side": "short", "symbol": symbol, "sl": new_sl, "reason": "breakeven"})
            if state.be_done_short and self.config.trail_distance_pct > 0:
                trail = state.trough_short * (1.0 + self.config.trail_distance_pct / 100.0)
                if trail < state.short_sl_current:
                    state.short_sl_current = trail
                    actions.append({"action": "move_sl", "side": "short", "symbol": symbol, "sl": trail, "reason": "trail"})
            if price >= state.short_sl_current:
                actions.append({"action": "close_short", "symbol": symbol, "reason": "runner_sl", "price": state.short_sl_current})
                state.short_open = False
            elif price <= state.short_tp:
                actions.append({"action": "close_short", "symbol": symbol, "reason": "tp", "price": state.short_tp})
                state.short_open = False

        if not state.long_open and not state.short_open:
            self.open_pairs.pop(symbol, None)

        return actions

    def log_would_open(self, symbol: str, bias: SideBias, price: float) -> None:
        """Safe log-only hook when enabled but execute=false."""
        levels = plan_levels(price, self.config)
        logger.info(
            "hedge_pair would open %s bias=%s entry=%.6g long_sl=%.6g long_tp=%.6g "
            "short_sl=%.6g short_tp=%.6g execute=%s",
            symbol,
            bias,
            price,
            levels["long_sl"],
            levels["long_tp"],
            levels["short_sl"],
            levels["short_tp"],
            self.config.execute,
        )


# TODO(live): wire HedgePairManager into UnifiedOrchestrator when hedge_pair.execute
# is true: place hedge orders with position_idx=1 (long) / 2 (short) on Bybit hedge mode,
# then apply on_price_tick actions via close_position / update_stop_loss.
