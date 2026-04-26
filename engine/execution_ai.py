#!/usr/bin/env python3
"""
Execution AI — pre-trade microstructure and timing checks before sending orders.

Works with Bybit-style klines (list of dicts) and orderbook {"bids": [[p,s],...], "asks": [...]}.
Does not replace ExecutionEngine (PostOnly + market fallback); it gates and optionally scales size.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

import numpy as np


@dataclass
class ExecutionVerdict:
    allow_entry: bool
    skip_reason: str
    signal_boost: float
    effective_confidence: float
    timing: str
    spread_pct: float
    vol_std: float
    order_style_hint: str
    depth_bid_vol: float
    depth_ask_vol: float
    notes: Tuple[str, ...] = field(default_factory=tuple)

    @classmethod
    def allow_pass_through(cls, base_confidence: float) -> "ExecutionVerdict":
        return cls(
            allow_entry=True,
            skip_reason="",
            signal_boost=1.0,
            effective_confidence=float(base_confidence),
            timing="ENTER_NOW",
            spread_pct=0.0,
            vol_std=0.0,
            order_style_hint="POST_ONLY_PREFERRED",
            depth_bid_vol=0.0,
            depth_ask_vol=0.0,
            notes=("execution_ai_disabled",),
        )


def _closes_from_klines(klines: Sequence[Mapping[str, Any]]) -> np.ndarray:
    out = []
    for k in klines:
        try:
            out.append(float(k.get("close", 0.0) or 0.0))
        except (TypeError, ValueError):
            out.append(0.0)
    return np.asarray(out, dtype=float)


def _sum_depth(levels: List, n: int) -> float:
    total = 0.0
    for row in (levels or [])[: max(0, n)]:
        if not row or len(row) < 2:
            continue
        try:
            total += float(row[1])
        except (TypeError, ValueError):
            continue
    return total


def _best_bid_ask(orderbook: Mapping[str, Any]) -> Tuple[float, float]:
    bids = orderbook.get("bids") or []
    asks = orderbook.get("asks") or []
    bid = float(bids[0][0]) if bids else 0.0
    ask = float(asks[0][0]) if asks else 0.0
    return bid, ask


class ExecutionAI:
    """Lightweight execution layer: volatility, spread, liquidity, timing, anti-FOMO, book tilt."""

    def __init__(
        self,
        enabled: bool = False,
        spread_max_pct: float = 0.08,
        volatility_limit: float = 0.02,
        kline_lookback_vol: int = 48,
        pullback_momentum_bars: int = 5,
        imbalance_boost_high: float = 1.1,
        imbalance_boost_low: float = 0.9,
        imbalance_high_ratio: float = 1.2,
        imbalance_low_ratio: float = 0.8,
        anti_fomo_sigma_mult: float = 3.0,
        min_top_depth_volume: float = 0.0,
        depth_levels_for_liquidity: int = 25,
        min_confidence_after_boost: float = 0.0,
        apply_boost_to_size: bool = False,
        size_boost_min: float = 0.85,
        size_boost_max: float = 1.15,
        scaled_entry: bool = False,
        scale_fractions: Optional[List[float]] = None,
        leg_delay_sec: float = 1.0,
        skip_on_wait_pullback: bool = True,
        market_order_spread_pct: float = 0.02,
    ):
        self.enabled = bool(enabled)
        self.spread_max_pct = max(0.0, float(spread_max_pct))
        self.volatility_limit = max(1e-9, float(volatility_limit))
        self.kline_lookback_vol = max(8, int(kline_lookback_vol))
        self.pullback_momentum_bars = max(2, int(pullback_momentum_bars))
        self.imbalance_boost_high = float(imbalance_boost_high)
        self.imbalance_boost_low = float(imbalance_boost_low)
        self.imbalance_high_ratio = float(imbalance_high_ratio)
        self.imbalance_low_ratio = float(imbalance_low_ratio)
        self.anti_fomo_sigma_mult = max(0.0, float(anti_fomo_sigma_mult))
        self.min_top_depth_volume = max(0.0, float(min_top_depth_volume))
        self.depth_levels_for_liquidity = max(1, int(depth_levels_for_liquidity))
        self.min_confidence_after_boost = max(0.0, float(min_confidence_after_boost))
        self.apply_boost_to_size = bool(apply_boost_to_size)
        self.size_boost_min = float(size_boost_min)
        self.size_boost_max = float(size_boost_max)
        self.scaled_entry = bool(scaled_entry)
        fr = scale_fractions if scale_fractions is not None else [0.5, 0.3, 0.2]
        self.scale_fractions = self._normalize_fractions(fr)
        self.leg_delay_sec = max(0.0, float(leg_delay_sec))
        self.skip_on_wait_pullback = bool(skip_on_wait_pullback)
        self.market_order_spread_pct = max(0.0, float(market_order_spread_pct))

    @staticmethod
    def _normalize_fractions(fr: Sequence[float]) -> List[float]:
        vals = [max(0.0, float(x)) for x in fr]
        s = sum(vals)
        if s <= 0:
            return [1.0]
        return [v / s for v in vals]

    @classmethod
    def from_mapping(cls, cfg: Optional[Mapping[str, Any]]) -> "ExecutionAI":
        c = dict(cfg or {})
        return cls(
            enabled=bool(c.get("enabled", False)),
            spread_max_pct=float(c.get("spread_max_pct", 0.08)),
            volatility_limit=float(c.get("volatility_limit", 0.02)),
            kline_lookback_vol=int(c.get("kline_lookback_vol", 48)),
            pullback_momentum_bars=int(c.get("pullback_momentum_bars", 5)),
            imbalance_boost_high=float(c.get("imbalance_boost_high", 1.1)),
            imbalance_boost_low=float(c.get("imbalance_boost_low", 0.9)),
            imbalance_high_ratio=float(c.get("imbalance_high_ratio", 1.2)),
            imbalance_low_ratio=float(c.get("imbalance_low_ratio", 0.8)),
            anti_fomo_sigma_mult=float(c.get("anti_fomo_sigma_mult", 3.0)),
            min_top_depth_volume=float(c.get("min_top_depth_volume", 0.0)),
            depth_levels_for_liquidity=int(c.get("depth_levels_for_liquidity", 25)),
            min_confidence_after_boost=float(c.get("min_confidence_after_boost", 0.0)),
            apply_boost_to_size=bool(c.get("apply_boost_to_size", False)),
            size_boost_min=float(c.get("size_boost_min", 0.85)),
            size_boost_max=float(c.get("size_boost_max", 1.15)),
            scaled_entry=bool(c.get("scaled_entry", False)),
            scale_fractions=c.get("scale_fractions"),
            leg_delay_sec=float(c.get("leg_delay_sec", 1.0)),
            skip_on_wait_pullback=bool(c.get("skip_on_wait_pullback", True)),
            market_order_spread_pct=float(c.get("market_order_spread_pct", 0.02)),
        )

    @staticmethod
    def returns_volatility(closes: np.ndarray) -> float:
        if closes.size < 3:
            return 0.0
        r = np.diff(closes) / np.maximum(closes[:-1], 1e-12)
        r = r[np.isfinite(r)]
        if r.size < 2:
            return 0.0
        return float(np.std(r))

    def liquidity_ok(self, orderbook: Mapping[str, Any]) -> Tuple[bool, float, float]:
        bid_v = _sum_depth(list(orderbook.get("bids") or []), self.depth_levels_for_liquidity)
        ask_v = _sum_depth(list(orderbook.get("asks") or []), self.depth_levels_for_liquidity)
        if self.min_top_depth_volume <= 0:
            return True, bid_v, ask_v
        ok = bid_v >= self.min_top_depth_volume and ask_v >= self.min_top_depth_volume
        return ok, bid_v, ask_v

    def spread_pct(self, orderbook: Mapping[str, Any]) -> float:
        bid, ask = _best_bid_ask(orderbook)
        if bid <= 0 or ask <= 0 or ask < bid:
            return 999.0
        mid = 0.5 * (bid + ask)
        if mid <= 0:
            return 999.0
        return float((ask - bid) / mid * 100.0)

    def microstructure_boost(self, side: str, bid_v: float, ask_v: float) -> float:
        eps = 1e-9
        su = side.upper()
        if su in ("BUY", "LONG"):
            ratio = bid_v / (ask_v + eps)
            if ratio >= self.imbalance_high_ratio:
                return self.imbalance_boost_high
            if ratio <= self.imbalance_low_ratio:
                return self.imbalance_boost_low
        else:
            ratio = ask_v / (bid_v + eps)
            if ratio >= self.imbalance_high_ratio:
                return self.imbalance_boost_high
            if ratio <= self.imbalance_low_ratio:
                return self.imbalance_boost_low
        return 1.0

    def entry_timing(self, closes: np.ndarray, side: str) -> str:
        """WAIT_PULLBACK = last stretch moved with the impulse; skip chasing. ENTER_NOW = flatter / pullback."""
        if closes.size < self.pullback_momentum_bars + 1:
            return "ENTER_NOW"
        a = float(closes[-1])
        b = float(closes[-1 - self.pullback_momentum_bars])
        momentum = a - b
        su = side.upper()
        if su in ("BUY", "LONG"):
            return "WAIT_PULLBACK" if momentum > 0 else "ENTER_NOW"
        return "WAIT_PULLBACK" if momentum < 0 else "ENTER_NOW"

    def anti_fomo_blocks(self, klines: Sequence[Mapping[str, Any]], closes: np.ndarray) -> bool:
        if self.anti_fomo_sigma_mult <= 0 or not klines:
            return False
        last = klines[-1]
        try:
            o = float(last.get("open", 0.0) or 0.0)
            c = float(last.get("close", 0.0) or 0.0)
        except (TypeError, ValueError):
            return False
        if c <= 0:
            return False
        body_pct = abs(c - o) / c
        vol = self.returns_volatility(closes)
        thr = max(vol * self.anti_fomo_sigma_mult, 1e-12)
        return body_pct > thr

    def choose_order_style_hint(self, spread_pct_val: float) -> str:
        if spread_pct_val <= self.market_order_spread_pct:
            return "TIGHT_SPREAD_MARKET_OK"
        return "POST_ONLY_PREFERRED"

    def scale_entry_fractions(self) -> List[float]:
        if not self.scaled_entry:
            return [1.0]
        return list(self.scale_fractions)

    def size_multiplier_from_boost(self, boost: float) -> float:
        if not self.apply_boost_to_size:
            return 1.0
        return float(np.clip(boost, self.size_boost_min, self.size_boost_max))

    def evaluate(
        self,
        side: str,
        klines: Sequence[Mapping[str, Any]],
        orderbook: Mapping[str, Any],
        base_confidence: float,
    ) -> ExecutionVerdict:
        bc = float(base_confidence or 0.0)
        if not self.enabled:
            return ExecutionVerdict.allow_pass_through(bc)

        notes: List[str] = []
        closes = _closes_from_klines(klines[-self.kline_lookback_vol :])
        if closes.size < 5:
            return ExecutionVerdict(
                allow_entry=False,
                skip_reason="execution_ai_insufficient_klines",
                signal_boost=1.0,
                effective_confidence=bc,
                timing="?",
                spread_pct=0.0,
                vol_std=0.0,
                order_style_hint="POST_ONLY_PREFERRED",
                depth_bid_vol=0.0,
                depth_ask_vol=0.0,
                notes=("need_more_klines",),
            )

        vol_std = self.returns_volatility(closes)
        if vol_std >= self.volatility_limit:
            return ExecutionVerdict(
                allow_entry=False,
                skip_reason=f"execution_ai_high_volatility (σ={vol_std:.5f} >= {self.volatility_limit})",
                signal_boost=1.0,
                effective_confidence=bc,
                timing="?",
                spread_pct=0.0,
                vol_std=vol_std,
                order_style_hint="POST_ONLY_PREFERRED",
                depth_bid_vol=0.0,
                depth_ask_vol=0.0,
                notes=("volatility",),
            )

        sp = self.spread_pct(orderbook)
        if sp > self.spread_max_pct:
            return ExecutionVerdict(
                allow_entry=False,
                skip_reason=f"execution_ai_wide_spread ({sp:.4f}% > {self.spread_max_pct}%)",
                signal_boost=1.0,
                effective_confidence=bc,
                timing="?",
                spread_pct=sp,
                vol_std=vol_std,
                order_style_hint="POST_ONLY_PREFERRED",
                depth_bid_vol=0.0,
                depth_ask_vol=0.0,
                notes=("spread",),
            )

        liq_ok, bid_v, ask_v = self.liquidity_ok(orderbook)
        if not liq_ok:
            return ExecutionVerdict(
                allow_entry=False,
                skip_reason=(
                    f"execution_ai_thin_book (bid_depth={bid_v:.4f} ask_depth={ask_v:.4f} "
                    f"< min={self.min_top_depth_volume})"
                ),
                signal_boost=1.0,
                effective_confidence=bc,
                timing="?",
                spread_pct=sp,
                vol_std=vol_std,
                order_style_hint=self.choose_order_style_hint(sp),
                depth_bid_vol=bid_v,
                depth_ask_vol=ask_v,
                notes=("liquidity",),
            )

        if self.anti_fomo_blocks(klines, closes):
            return ExecutionVerdict(
                allow_entry=False,
                skip_reason="execution_ai_anti_fomo (last candle body vs vol spike)",
                signal_boost=1.0,
                effective_confidence=bc,
                timing="?",
                spread_pct=sp,
                vol_std=vol_std,
                order_style_hint=self.choose_order_style_hint(sp),
                depth_bid_vol=bid_v,
                depth_ask_vol=ask_v,
                notes=("anti_fomo",),
            )

        timing = self.entry_timing(closes, side)
        if self.skip_on_wait_pullback and timing == "WAIT_PULLBACK":
            return ExecutionVerdict(
                allow_entry=False,
                skip_reason="execution_ai_wait_pullback (avoid chasing impulse)",
                signal_boost=1.0,
                effective_confidence=bc,
                timing=timing,
                spread_pct=sp,
                vol_std=vol_std,
                order_style_hint=self.choose_order_style_hint(sp),
                depth_bid_vol=bid_v,
                depth_ask_vol=ask_v,
                notes=("timing",),
            )

        boost = self.microstructure_boost(side, bid_v, ask_v)
        eff = bc * boost
        notes.append(f"boost={boost:.3f}")

        if self.min_confidence_after_boost > 0 and eff + 1e-12 < self.min_confidence_after_boost:
            return ExecutionVerdict(
                allow_entry=False,
                skip_reason=(
                    f"execution_ai_low_conf_after_boost ({eff:.3f} = {bc:.3f}*{boost:.3f} "
                    f"< {self.min_confidence_after_boost})"
                ),
                signal_boost=boost,
                effective_confidence=eff,
                timing=timing,
                spread_pct=sp,
                vol_std=vol_std,
                order_style_hint=self.choose_order_style_hint(sp),
                depth_bid_vol=bid_v,
                depth_ask_vol=ask_v,
                notes=tuple(notes),
            )

        return ExecutionVerdict(
            allow_entry=True,
            skip_reason="",
            signal_boost=boost,
            effective_confidence=eff,
            timing=timing,
            spread_pct=sp,
            vol_std=vol_std,
            order_style_hint=self.choose_order_style_hint(sp),
            depth_bid_vol=bid_v,
            depth_ask_vol=ask_v,
            notes=tuple(notes),
        )
