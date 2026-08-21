"""Stateful manager for Trend-Continuation Hedge Pair (live/unified hooks).

Places dual-leg hedge orders when orchestrator calls open_pair with execute=true.
Account must be in Bybit Hedge Mode (positionIdx 1=long, 2=short).
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

_POS_IDX_HINT = (
    "Счёт Bybit должен быть в Hedge Mode (не Combined / One-Way). "
    "Иначе place_order с positionIdx=1/2 не сработает."
)


def _is_position_idx_error(err: str) -> bool:
    low = (err or "").lower()
    keys = (
        "positionidx",
        "position idx",
        "position_idx",
        "hedge mode",
        "one-way",
        "one way",
        "combined",
        "10001",
        "110025",
        "110028",
    )
    return any(k in low for k in keys)


def qty_from_margin(
    balance: float,
    price: float,
    *,
    margin_pct_per_leg: float,
    leverage: int,
) -> float:
    """margin_pct of balance * leverage / price ≈ qty (one leg)."""
    if balance <= 0 or price <= 0:
        return 0.0
    lev = max(1, int(leverage or 1))
    margin = balance * (float(margin_pct_per_leg) / 100.0)
    notional = margin * lev
    return notional / price


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
    qty: float = 0.0
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
    """Tracks at most `max_pairs` open hedge pairs."""

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
        reason: str = "",
    ) -> bool:
        _ = reason  # optional context for callers / future logging
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
        qty: float = 0.0,
        levels: Optional[Dict[str, float]] = None,
    ) -> HedgePairState:
        lv = levels if levels is not None else plan_levels(entry, self.config)
        state = HedgePairState(
            symbol=symbol,
            bias=bias,
            entry=entry,
            long_sl=float(lv["long_sl"]),
            long_tp=float(lv["long_tp"]),
            short_sl=float(lv["short_sl"]),
            short_tp=float(lv["short_tp"]),
            opened_at=opened_at if opened_at is not None else time.time(),
            qty=float(qty or 0.0),
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

    async def open_pair(
        self,
        exchange: Any,
        symbol: str,
        bias: SideBias,
        price: float,
        qty: float,
        levels: Dict[str, float],
    ) -> Dict[str, Any]:
        """Open long (Buy idx=1) + short (Sell idx=2) with SL/TP; rollback if one fails."""
        out: Dict[str, Any] = {
            "success": False,
            "long": None,
            "short": None,
            "error": "",
            "rolled_back": False,
        }
        if qty <= 0 or price <= 0:
            out["error"] = "qty or price invalid"
            return out

        long_sl = float(levels["long_sl"])
        long_tp = float(levels["long_tp"])
        short_sl = float(levels["short_sl"])
        short_tp = float(levels["short_tp"])

        try:
            long_res = await exchange.place_order(
                symbol=symbol,
                side="Buy",
                qty=qty,
                stop_loss=long_sl,
                take_profit=long_tp,
                order_type="Market",
                position_idx=1,
            )
        except Exception as exc:
            long_res = {"success": False, "orderId": "", "error": str(exc)}

        out["long"] = long_res
        if not long_res.get("success"):
            err = str(long_res.get("error", "long leg failed"))
            if _is_position_idx_error(err):
                logger.error("hedge_pair place_order LONG fail: %s | %s", err, _POS_IDX_HINT)
            else:
                logger.error("hedge_pair place_order LONG fail %s: %s", symbol, err)
            out["error"] = err
            return out

        try:
            short_res = await exchange.place_order(
                symbol=symbol,
                side="Sell",
                qty=qty,
                stop_loss=short_sl,
                take_profit=short_tp,
                order_type="Market",
                position_idx=2,
            )
        except Exception as exc:
            short_res = {"success": False, "orderId": "", "error": str(exc)}

        out["short"] = short_res
        if not short_res.get("success"):
            err = str(short_res.get("error", "short leg failed"))
            if _is_position_idx_error(err):
                logger.error("hedge_pair place_order SHORT fail: %s | %s", err, _POS_IDX_HINT)
            else:
                logger.error("hedge_pair place_order SHORT fail %s: %s", symbol, err)
            # Flatten successful long immediately — never leave one-sided exposure
            try:
                flat = await exchange.close_position(
                    symbol, "Buy", qty=qty, position_idx=1
                )
                out["rolled_back"] = bool(flat.get("success"))
                logger.warning(
                    "hedge_pair rollback long %s success=%s err=%s",
                    symbol,
                    flat.get("success"),
                    flat.get("error", ""),
                )
            except Exception as flat_exc:
                logger.error(
                    "hedge_pair CRITICAL: short failed and long flatten failed %s: %s",
                    symbol,
                    flat_exc,
                )
                out["rolled_back"] = False
            out["error"] = err
            return out

        self.register_open(symbol, price, bias, qty=qty, levels=levels)
        out["success"] = True
        logger.info(
            "hedge_pair OPENED %s bias=%s qty=%.6g entry=%.6g long_id=%s short_id=%s",
            symbol,
            bias,
            qty,
            price,
            long_res.get("orderId", ""),
            short_res.get("orderId", ""),
        )
        return out

    async def apply_exchange_actions(
        self,
        exchange: Any,
        symbol: str,
        actions: List[Dict[str, Any]],
        *,
        qty: Optional[float] = None,
    ) -> None:
        """Apply on_price_tick actions via exchange (close / move_sl / flatten)."""
        state = self.open_pairs.get(symbol)
        use_qty = float(qty if qty is not None else (state.qty if state else 0.0) or 0.0)

        async def _close_long(reason: str) -> None:
            if use_qty <= 0:
                logger.warning("hedge_pair close_long %s skipped: qty=0 (%s)", symbol, reason)
                return
            res = await exchange.close_position(symbol, "Buy", qty=use_qty, position_idx=1)
            logger.info(
                "hedge_pair close_long %s reason=%s ok=%s err=%s",
                symbol,
                reason,
                res.get("success"),
                res.get("error", ""),
            )

        async def _close_short(reason: str) -> None:
            if use_qty <= 0:
                logger.warning("hedge_pair close_short %s skipped: qty=0 (%s)", symbol, reason)
                return
            res = await exchange.close_position(symbol, "Sell", qty=use_qty, position_idx=2)
            logger.info(
                "hedge_pair close_short %s reason=%s ok=%s err=%s",
                symbol,
                reason,
                res.get("success"),
                res.get("error", ""),
            )

        for act in actions:
            kind = str(act.get("action", ""))
            reason = str(act.get("reason", ""))
            try:
                if kind == "close_long":
                    await _close_long(reason)
                elif kind == "close_short":
                    await _close_short(reason)
                elif kind == "flatten":
                    await _close_long(reason)
                    await _close_short(reason)
                elif kind == "move_sl":
                    side = str(act.get("side", ""))
                    sl = float(act.get("sl", 0) or 0)
                    if sl <= 0 or not hasattr(exchange, "update_stop_loss"):
                        continue
                    idx = 1 if side == "long" else 2
                    res = await exchange.update_stop_loss(symbol, sl, position_idx=idx)
                    logger.info(
                        "hedge_pair move_sl %s side=%s sl=%.6g ok=%s",
                        symbol,
                        side,
                        sl,
                        res.get("success"),
                    )
                else:
                    logger.debug("hedge_pair unknown action %s", kind)
            except Exception as exc:
                logger.error("hedge_pair apply action %s %s failed: %s", kind, symbol, exc)

    def log_would_open(
        self,
        symbol: str,
        bias: SideBias,
        price: float,
        *,
        ema: float = 0.0,
        reason: str = "",
    ) -> None:
        """Safe log-only hook when enabled but execute=false."""
        levels = plan_levels(price, self.config)
        if reason == "fallback_no_other_signals":
            prefix = "hedge_pair FALLBACK (no other signals): would open"
        elif reason:
            prefix = f"hedge_pair would open ({reason})"
        else:
            prefix = "hedge_pair would open"
        logger.info(
            "%s %s bias=%s ema=%.6g entry=%.6g long_sl=%.6g long_tp=%.6g "
            "short_sl=%.6g short_tp=%.6g execute=%s",
            prefix,
            symbol,
            bias,
            ema,
            price,
            levels["long_sl"],
            levels["long_tp"],
            levels["short_sl"],
            levels["short_tp"],
            self.config.execute,
        )
